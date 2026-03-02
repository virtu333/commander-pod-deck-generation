"""Tests for bracket estimation orchestration."""

from __future__ import annotations

from src.brackets.combo_detector import SpellbookResult
from src.brackets.estimator import BracketEstimator
from src.collection.models import Card, ComboInfo


def _card(name: str) -> Card:
    return Card(
        scryfall_id=name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0,
        color_identity=[],
        type_line="Creature",
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="rare",
    )


def _combo(bracket: int, tag: str = "P", two_card: bool = True) -> ComboInfo:
    return ComboInfo(
        combo_id=f"combo-{tag}-{bracket}",
        card_names=["Card A", "Card B"],
        description="Test combo",
        bracket_tag=tag,
        bracket=bracket,
        is_two_card=two_card,
        mana_needed="{3}",
    )


def _spellbook_result(
    tag: str = "O",
    bracket: int = 2,
    combos: list[ComboInfo] | None = None,
) -> SpellbookResult:
    return SpellbookResult(
        bracket_tag=tag,
        bracket=bracket,
        game_changer_cards=[],
        mld_cards=[],
        extra_turn_cards=[],
        combos=combos or [],
    )


class FakeGameChangerDetector:
    def __init__(self, matches: list[Card], success: bool = True) -> None:
        self.matches = matches
        self.success = success
        self.calls: list[list[Card]] = []

    def detect(self, cards: list[Card]) -> tuple[list[Card], bool]:
        self.calls.append(cards)
        return self.matches, self.success


class FakeRuleChecker:
    def __init__(self, mld: list[Card] | None = None, extra_turns: list[Card] | None = None) -> None:
        self.mld = mld or []
        self.extra_turns = extra_turns or []

    def find_mld(self, cards: list[Card]) -> list[Card]:
        return self.mld

    def find_extra_turns(self, cards: list[Card]) -> list[Card]:
        return self.extra_turns


class FakeComboDetector:
    def __init__(self, result: SpellbookResult | None) -> None:
        self.result = result
        self.calls: list[tuple[list[str], list[str]]] = []

    def estimate_bracket(
        self,
        card_names: list[str],
        commander_names: list[str],
    ) -> SpellbookResult | None:
        self.calls.append((card_names, commander_names))
        return self.result


def _estimator(
    gc_matches: list[Card] | None = None,
    gc_success: bool = True,
    mld: list[Card] | None = None,
    extra_turns: list[Card] | None = None,
    spellbook: SpellbookResult | None = None,
) -> tuple[BracketEstimator, FakeComboDetector, FakeGameChangerDetector]:
    gc_detector = FakeGameChangerDetector(gc_matches or [], success=gc_success)
    combo_detector = FakeComboDetector(spellbook)
    estimator = BracketEstimator(
        game_changers=gc_detector,
        rules=FakeRuleChecker(mld=mld, extra_turns=extra_turns),
        combo_detector=combo_detector,
    )
    return estimator, combo_detector, gc_detector


def test_clean_deck_is_bracket_2_with_high_confidence() -> None:
    estimator, _, _ = _estimator(spellbook=_spellbook_result(tag="O", bracket=2))

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])

    assert result.bracket == 2
    assert result.confidence == "high"


def test_one_to_three_game_changers_pushes_bracket_3() -> None:
    estimator, _, _ = _estimator(
        gc_matches=[_card("Sol Ring"), _card("Rhystic Study")],
        spellbook=_spellbook_result(),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 3


def test_four_or_more_game_changers_pushes_bracket_4() -> None:
    estimator, _, _ = _estimator(
        gc_matches=[_card("A"), _card("B"), _card("C"), _card("D")],
        spellbook=_spellbook_result(),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4


def test_mld_pushes_bracket_4() -> None:
    estimator, _, _ = _estimator(
        mld=[_card("Armageddon")],
        spellbook=_spellbook_result(),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4
    assert result.mld_cards == ["Armageddon"]


def test_one_or_two_extra_turns_pushes_bracket_3() -> None:
    estimator, _, _ = _estimator(
        extra_turns=[_card("Time Warp"), _card("Nexus of Fate")],
        spellbook=_spellbook_result(),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 3


def test_three_or_more_extra_turns_pushes_bracket_4() -> None:
    estimator, _, _ = _estimator(
        extra_turns=[_card("A"), _card("B"), _card("C")],
        spellbook=_spellbook_result(),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4


def test_ruthless_spellbook_tag_pushes_bracket_4() -> None:
    estimator, _, _ = _estimator(spellbook=_spellbook_result(tag="R", bracket=4))

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4
    assert result.spellbook_bracket_tag == "R"


def test_spellbook_e_tag_returns_bracket_1_with_reason() -> None:
    estimator, _, _ = _estimator(spellbook=_spellbook_result(tag="E", bracket=1))

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 1
    assert result.confidence == "high"
    assert result.spellbook_bracket_tag == "E"
    assert any("suggests bracket 1" in reason.lower() for reason in result.reasons)


def test_two_card_combo_pushes_bracket_3() -> None:
    estimator, _, _ = _estimator(
        spellbook=_spellbook_result(tag="O", bracket=2, combos=[_combo(bracket=3, tag="P")]),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 3
    assert len(result.combos) == 1


def test_multiple_signals_take_max() -> None:
    estimator, _, _ = _estimator(
        gc_matches=[_card("Sol Ring")],
        mld=[_card("Armageddon")],
        spellbook=_spellbook_result(tag="O", bracket=2),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4


def test_spellbook_down_and_no_local_signals_is_low_confidence() -> None:
    estimator, _, _ = _estimator(spellbook=None)

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 1
    assert result.confidence == "low"


def test_spellbook_down_but_local_signals_is_medium_confidence() -> None:
    estimator, _, _ = _estimator(
        gc_matches=[_card("Sol Ring")],
        spellbook=None,
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 3
    assert result.confidence == "medium"


def test_gc_fetch_failed_lowers_confidence_and_adds_reason() -> None:
    estimator, _, _ = _estimator(
        gc_success=False,
        spellbook=_spellbook_result(tag="O", bracket=2),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.confidence == "medium"
    assert any("unavailable" in reason.lower() for reason in result.reasons)


def test_spellbook_and_gc_failure_forces_low_confidence() -> None:
    estimator, _, _ = _estimator(
        gc_success=False,
        mld=[_card("Armageddon")],
        spellbook=None,
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])
    assert result.bracket == 4
    assert result.confidence == "low"


def test_multiple_commanders_are_passed_to_spellbook() -> None:
    estimator, combo_detector, _ = _estimator(spellbook=_spellbook_result())
    commanders = [_card("Thrasios, Triton Hero"), _card("Tymna the Weaver")]

    estimator.estimate(cards=[_card("Sol Ring"), _card("Arcane Signet")], commanders=commanders)

    assert combo_detector.calls
    card_names, commander_names = combo_detector.calls[0]
    assert card_names == ["Sol Ring", "Arcane Signet"]
    assert commander_names == ["Thrasios, Triton Hero", "Tymna the Weaver"]


def test_reasons_populated_for_each_signal() -> None:
    estimator, _, _ = _estimator(
        gc_matches=[_card("Sol Ring")],
        mld=[_card("Armageddon")],
        extra_turns=[_card("Time Warp")],
        spellbook=_spellbook_result(tag="R", bracket=4, combos=[_combo(3)]),
    )

    result = estimator.estimate(cards=[_card("Cultivate")], commanders=[_card("Omnath")])

    joined = " | ".join(result.reasons).lower()
    assert "game changer" in joined
    assert "mass land denial" in joined
    assert "extra-turn" in joined
    assert "spellbook bracket tag" in joined
    assert "two-card combos" in joined

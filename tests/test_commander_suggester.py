"""Tests for commander suggestion logic."""

from __future__ import annotations

from src.collection.models import Card, Collection, OwnedCard
from src.commanders.edhrec_client import CommanderProfile, EDHRecCard
from src.commanders.suggester import (
    CommanderSuggester,
    _color_diversity,
    _is_legal_commander,
)


class FakeEDHRecClient:
    def __init__(self, profiles: dict[str, CommanderProfile | None] | None = None) -> None:
        self.profiles = profiles or {}
        self.calls: list[str] = []

    def get_commander_profile(self, commander_name: str) -> CommanderProfile | None:
        self.calls.append(commander_name)
        return self.profiles.get(commander_name.strip().casefold())


def _card(
    name: str,
    colors: list[str] | None = None,
    *,
    type_line: str = "Legendary Creature - Test",
    oracle_text: str = "",
    commander_legality: str = "legal",
    scryfall_id: str | None = None,
) -> Card:
    return Card(
        scryfall_id=scryfall_id or name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=colors or [],
        type_line=type_line,
        oracle_text=oracle_text,
        keywords=[],
        legalities={"commander": commander_legality},
        set_code="tst",
        collector_number="1",
        rarity="rare",
    )


def _owned(card: Card, quantity: int = 1) -> OwnedCard:
    return OwnedCard(card=card, quantity=quantity)


def _collection(cards: list[OwnedCard]) -> Collection:
    return Collection(cards=cards, unresolved=[], import_date="2026-03-02")


def _profile(commander_name: str, card_names: list[str]) -> CommanderProfile:
    cards = [
        EDHRecCard(
            name=card_name,
            scryfall_id=f"id-{index}",
            synergy=0.1,
            inclusion_rate=0.5,
            category="Top Cards",
        )
        for index, card_name in enumerate(card_names, start=1)
    ]
    return CommanderProfile(commander_name=commander_name, cards=cards, num_decks=1000)


def test_legendary_creature_is_commander() -> None:
    assert _is_legal_commander(_card("Test Commander"))


def test_non_legendary_creature_not_commander() -> None:
    assert not _is_legal_commander(_card("Bear Cub", type_line="Creature - Bear"))


def test_legendary_non_creature_not_commander() -> None:
    assert not _is_legal_commander(_card("The One Ring", type_line="Legendary Artifact"))


def test_can_be_your_commander_text() -> None:
    card = _card(
        "Grist, the Hunger Tide",
        type_line="Legendary Planeswalker - Grist",
        oracle_text="Grist, the Hunger Tide can be your commander.",
    )
    assert _is_legal_commander(card)


def test_banned_commander_excluded() -> None:
    card = _card("Leovold", commander_legality="banned")
    assert not _is_legal_commander(card)


def test_commander_check_is_case_insensitive() -> None:
    card = _card("Lowercase Legend", type_line="legendary creature - wizard")
    assert _is_legal_commander(card)


def test_diversity_all_new_colors() -> None:
    assert _color_diversity(["W", "U"], set()) == 0.4


def test_diversity_no_new_colors() -> None:
    assert _color_diversity(["W"], {"W", "U", "B", "R", "G"}) == 0.0


def test_diversity_partial_overlap() -> None:
    assert _color_diversity(["W", "U"], {"W", "B"}) == 1 / 3


def test_diversity_colorless_candidate() -> None:
    assert _color_diversity([], set()) == 0.0


def test_find_commanders_filters_correctly() -> None:
    good_legend = _card("Legend A", ["W"])
    bad_nonlegend = _card("Not Legend", ["U"], type_line="Creature - Wizard")
    bad_banned = _card("Banned", ["B"], commander_legality="banned")
    text_commander = _card(
        "Text Commander",
        ["R"],
        type_line="Legendary Planeswalker - X",
        oracle_text="This card can be your commander.",
    )
    collection = _collection(
        [
            _owned(good_legend),
            _owned(bad_nonlegend),
            _owned(bad_banned),
            _owned(text_commander),
        ]
    )
    suggester = CommanderSuggester(FakeEDHRecClient())

    commanders = suggester.find_commanders_in_collection(collection)
    names = [card.name for card in commanders]

    assert names == ["Legend A", "Text Commander"]


def test_suggest_fills_remaining_slots() -> None:
    a = _card("Commander A", ["W"])
    b = _card("Commander B", ["U"])
    c = _card("Commander C", ["B"])
    d = _card("Commander D", ["R"])
    collection = _collection([_owned(a), _owned(b), _owned(c), _owned(d)])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=[a], count=4)

    assert len(suggestions) == 3
    assert all(candidate.card.name != "Commander A" for candidate in suggestions)


def test_suggest_maximizes_color_diversity() -> None:
    selected = _card("Already Selected", ["B", "R", "G"])
    white = _card("White Option", ["W"])
    redundant = _card("Redundant BRG", ["B", "R", "G"])
    collection = _collection([_owned(selected), _owned(white), _owned(redundant)])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=[selected], count=2, max_edhrec_lookups=0)

    assert suggestions
    assert suggestions[0].card.name == "White Option"


def test_suggest_with_no_selected_returns_up_to_count() -> None:
    cards = [
        _card("Commander A", ["W"]),
        _card("Commander B", ["U"]),
        _card("Commander C", ["B"]),
        _card("Commander D", ["R"]),
    ]
    collection = _collection([_owned(card) for card in cards])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=None, count=4)

    assert len(suggestions) == 4


def test_suggest_all_selected_returns_empty() -> None:
    cards = [
        _card("Commander A", ["W"]),
        _card("Commander B", ["U"]),
        _card("Commander C", ["B"]),
        _card("Commander D", ["R"]),
    ]
    collection = _collection([_owned(card) for card in cards])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=cards, count=4)

    assert suggestions == []


def test_suggest_empty_collection_returns_empty() -> None:
    collection = _collection([])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=None, count=4)

    assert suggestions == []


def test_suggest_edhrec_down_falls_back_to_diversity_only() -> None:
    commanders = [
        _card("Commander A", ["W"]),
        _card("Commander B", ["U"]),
        _card("Commander C", ["B"]),
        _card("Commander D", ["R"]),
    ]
    collection = _collection([_owned(card) for card in commanders])
    fake = FakeEDHRecClient(profiles={})
    suggester = CommanderSuggester(fake)

    suggestions = suggester.suggest(collection, selected=None, count=3, max_edhrec_lookups=10)

    assert len(suggestions) == 3
    assert all(candidate.collection_overlap == 0.0 for candidate in suggestions)
    assert len(fake.calls) == 4


def test_suggest_caps_edhrec_lookups() -> None:
    commanders = [_card(f"Commander {index}", ["W"]) for index in range(10)]
    profiles = {
        card.name.casefold(): _profile(card.name, [f"Card {index}"])
        for index, card in enumerate(commanders)
    }
    collection = _collection([_owned(card) for card in commanders])
    fake = FakeEDHRecClient(profiles=profiles)
    suggester = CommanderSuggester(fake)

    suggestions = suggester.suggest(collection, selected=None, count=4, max_edhrec_lookups=5)

    assert len(suggestions) == 4
    assert len(fake.calls) == 5


def test_suggest_overlap_scoring() -> None:
    alpha = _card("Alpha Commander", ["W"])
    beta = _card("Beta Commander", ["W"])
    support = [_card("Owned Card 1", []), _card("Owned Card 2", []), _card("Owned Card 3", [])]
    collection = _collection([_owned(alpha), _owned(beta), *[_owned(card) for card in support]])
    fake = FakeEDHRecClient(
        profiles={
            "alpha commander": _profile("Alpha Commander", ["Owned Card 1", "Owned Card 2", "Owned Card 3"]),
            "beta commander": _profile("Beta Commander", ["Missing 1", "Missing 2", "Missing 3"]),
        }
    )
    suggester = CommanderSuggester(fake)

    suggestions = suggester.suggest(collection, selected=None, count=1, max_edhrec_lookups=2)

    assert len(suggestions) == 1
    assert suggestions[0].card.name == "Alpha Commander"
    assert suggestions[0].collection_overlap == 1.0


def test_buildable_count_dedupes_duplicate_printings() -> None:
    commander = _card("White Commander", ["W"])
    duplicate_a = _card(
        "Swords to Plowshares",
        ["W"],
        type_line="Instant",
        scryfall_id="stp-a",
    )
    duplicate_b = _card(
        "Swords to Plowshares",
        ["W"],
        type_line="Instant",
        scryfall_id="stp-b",
    )
    unique_support = _card(
        "Path to Exile",
        ["W"],
        type_line="Instant",
        scryfall_id="pte",
    )
    collection = _collection(
        [
            _owned(commander),
            _owned(duplicate_a),
            _owned(duplicate_b),
            _owned(unique_support),
        ]
    )
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=None, count=1, max_edhrec_lookups=0)

    assert len(suggestions) == 1
    assert suggestions[0].card.name == "White Commander"
    assert suggestions[0].buildable_count == 2


def test_suggest_greedy_rescoring_prefers_new_colors_after_first_pick() -> None:
    alpha_white = _card("Alpha White", ["W"])
    omega_white = _card("Omega White", ["W"])
    blue = _card("Blue Mage", ["U"])
    collection = _collection([_owned(alpha_white), _owned(omega_white), _owned(blue)])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(collection, selected=None, count=2, max_edhrec_lookups=0)

    assert [candidate.card.name for candidate in suggestions] == ["Alpha White", "Blue Mage"]


def test_suggest_prefers_candidates_meeting_buildable_threshold() -> None:
    selected = _card("Selected Green", ["G"])
    white = _card("White Commander", ["W"])
    blue = _card("Blue Commander", ["U"])

    white_support = [_card(f"White Support {i}", ["W"]) for i in range(45)]
    blue_support = [_card(f"Blue Support {i}", ["U"]) for i in range(5)]
    collection = _collection(
        [_owned(selected), _owned(white), _owned(blue)]
        + [_owned(card) for card in white_support]
        + [_owned(card) for card in blue_support]
    )
    fake = FakeEDHRecClient(
        profiles={
            "white commander": _profile("White Commander", ["Missing A", "Missing B"]),
            "blue commander": _profile(
                "Blue Commander",
                [card.name for card in blue_support],
            ),
        }
    )
    suggester = CommanderSuggester(fake)

    suggestions = suggester.suggest(
        collection,
        selected=[selected],
        count=2,
        max_edhrec_lookups=2,
        min_buildable_cards=40,
    )

    assert len(suggestions) == 1
    assert suggestions[0].card.name == "White Commander"
    assert suggestions[0].buildable_count >= 40


def test_suggest_falls_back_when_no_candidate_meets_buildable_threshold() -> None:
    a = _card("Commander A", ["W"])
    b = _card("Commander B", ["U"])
    collection = _collection([_owned(a), _owned(b)])
    suggester = CommanderSuggester(FakeEDHRecClient())

    suggestions = suggester.suggest(
        collection,
        selected=None,
        count=1,
        max_edhrec_lookups=0,
        min_buildable_cards=40,
    )

    assert len(suggestions) == 1

"""Tests for Game Changer detection wrapper."""

from __future__ import annotations

from src.brackets.game_changers import GameChangerDetector
from src.collection.models import Card
from src.utils.scryfall import ScryfallError


def _card(name: str) -> Card:
    return Card(
        scryfall_id=name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0,
        color_identity=[],
        type_line="Artifact",
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="rare",
    )


class FakeScryfall:
    def __init__(self, game_changers: list[Card], error: Exception | None = None) -> None:
        self._game_changers = game_changers
        self._error = error
        self.calls = 0

    def get_game_changers(self) -> list[Card]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._game_changers


def test_detect_known_game_changers() -> None:
    scryfall = FakeScryfall([_card("Sol Ring"), _card("Rhystic Study")])
    detector = GameChangerDetector(scryfall)

    matched, success = detector.detect([_card("Sol Ring"), _card("Opt")])

    assert success is True
    assert [card.name for card in matched] == ["Sol Ring"]


def test_no_false_positives_on_clean_list() -> None:
    scryfall = FakeScryfall([_card("Sol Ring")])
    detector = GameChangerDetector(scryfall)

    matched, success = detector.detect([_card("Cultivate"), _card("Counterspell")])

    assert success is True
    assert matched == []


def test_case_insensitive_matching() -> None:
    scryfall = FakeScryfall([_card("Rhystic Study")])
    detector = GameChangerDetector(scryfall)

    matched, success = detector.detect([_card("rhystic study")])

    assert success is True
    assert [card.name for card in matched] == ["rhystic study"]


def test_gc_names_cached_across_calls() -> None:
    scryfall = FakeScryfall([_card("Sol Ring")])
    detector = GameChangerDetector(scryfall)

    first, first_success = detector.detect([_card("Sol Ring")])
    second, second_success = detector.detect([_card("Sol Ring")])

    assert first_success is True
    assert second_success is True
    assert [card.name for card in first] == ["Sol Ring"]
    assert [card.name for card in second] == ["Sol Ring"]
    assert scryfall.calls == 1


def test_scryfall_error_gracefully_degrades() -> None:
    scryfall = FakeScryfall([], error=ScryfallError("network down"))
    detector = GameChangerDetector(scryfall)

    matched, success = detector.detect([_card("Sol Ring")])

    assert matched == []
    assert success is False

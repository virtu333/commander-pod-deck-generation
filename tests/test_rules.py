"""Tests for strict bracket rule card detection."""

from __future__ import annotations

from src.brackets.rules import RuleChecker
from src.collection.models import Card


def _card(name: str) -> Card:
    return Card(
        scryfall_id=name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0,
        color_identity=[],
        type_line="Sorcery",
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="rare",
    )


def test_find_mld_cards() -> None:
    checker = RuleChecker()
    cards = [_card("Armageddon"), _card("Obliterate"), _card("Cultivate")]

    found = checker.find_mld(cards)

    assert [card.name for card in found] == ["Armageddon", "Obliterate"]


def test_find_extra_turn_cards() -> None:
    checker = RuleChecker()
    cards = [_card("Time Warp"), _card("Nexus of Fate"), _card("Opt")]

    found = checker.find_extra_turns(cards)

    assert [card.name for card in found] == ["Time Warp", "Nexus of Fate"]


def test_no_false_positives() -> None:
    checker = RuleChecker()
    cards = [_card("Cultivate"), _card("Counterspell"), _card("Llanowar Elves")]

    assert checker.find_mld(cards) == []
    assert checker.find_extra_turns(cards) == []


def test_split_card_boom_bust_matches_mld() -> None:
    checker = RuleChecker()
    cards = [_card("Boom // Bust"), _card("Lightning Bolt")]

    found = checker.find_mld(cards)

    assert [card.name for card in found] == ["Boom // Bust"]


def test_case_insensitive_detection() -> None:
    checker = RuleChecker()
    cards = [_card("armageddon"), _card("time warp")]

    mld = checker.find_mld(cards)
    extra_turns = checker.find_extra_turns(cards)

    assert [card.name for card in mld] == ["armageddon"]
    assert [card.name for card in extra_turns] == ["time warp"]

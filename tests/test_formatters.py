"""Tests for deck export formatters."""

from __future__ import annotations

import csv
from pathlib import Path

from src.collection.models import Card
from src.deckbuilder.builder import BuiltDeck
from src.export import format_archidekt, format_manabox, format_moxfield, write_deck_exports


def _card(name: str, *, type_line: str = "Creature", scryfall_id: str | None = None) -> Card:
    return Card(
        scryfall_id=scryfall_id or name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=[],
        type_line=type_line,
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="common",
    )


def _deck() -> BuiltDeck:
    commander = _card("Atraxa, Praetors' Voice", type_line="Legendary Creature")
    cards = [_card("Sol Ring"), _card("Arcane Signet"), _card("Arcane Signet")]
    return BuiltDeck(
        commander=commander,
        cards=cards,
        scores={card.name.casefold(): 0.0 for card in cards},
        basics_added={},
        edhrec_available=False,
    )


def test_moxfield_format_includes_commander_and_quantities() -> None:
    text = format_moxfield(_deck())
    assert "1 Atraxa, Praetors' Voice" in text
    assert "2 Arcane Signet" in text
    assert "1 Sol Ring" in text


def test_archidekt_format_contains_sections() -> None:
    text = format_archidekt(_deck())
    assert "// Commander" in text
    assert "// Mainboard" in text
    assert "1 Atraxa, Praetors' Voice" in text


def test_manabox_format_is_valid_csv_with_expected_counts() -> None:
    csv_text = format_manabox(_deck())
    rows = list(csv.reader(csv_text.splitlines()))
    header = rows[0]
    assert "Name" in header
    assert "Quantity" in header
    assert rows[1][0] == "Atraxa, Praetors' Voice"
    assert any(row[0] == "Arcane Signet" and row[5] == "2" for row in rows[2:])


def test_write_deck_exports_writes_requested_formats(tmp_path: Path) -> None:
    deck = _deck()
    written = write_deck_exports([deck], tmp_path, formats=["moxfield", "archidekt", "manabox"])
    assert len(written) == 3
    assert all(path.exists() for path in written)
    assert any(path.name.endswith(".moxfield.txt") for path in written)
    assert any(path.name.endswith(".archidekt.txt") for path in written)
    assert any(path.name.endswith(".manabox.csv") for path in written)


def test_write_deck_exports_dedupes_requested_formats(tmp_path: Path) -> None:
    deck = _deck()
    written = write_deck_exports(
        [deck],
        tmp_path,
        formats=["moxfield", "moxfield", "archidekt", "archidekt"],
    )
    assert len(written) == 2
    assert sum(1 for path in written if path.name.endswith(".moxfield.txt")) == 1
    assert sum(1 for path in written if path.name.endswith(".archidekt.txt")) == 1

"""Tests for ManaBox/text collection import."""

from __future__ import annotations

from pathlib import Path

from src.collection.importer import import_csv, import_text

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _by_name(entries):
    return {entry.name: entry for entry in entries}


def test_parse_sample_csv() -> None:
    entries = import_csv(FIXTURES_DIR / "sample_collection.csv")
    cards = _by_name(entries)

    assert len(entries) == 15
    assert cards["Sol Ring"].quantity == 3
    assert cards["Sol Ring"].scryfall_id == "4cbc6901-6a4a-4d0a-83ea-7eefa3b35021"
    assert cards["Counterspell"].quantity == 3
    assert cards["Counterspell"].scryfall_id is None
    assert cards["Island"].quantity == 10


def test_parse_text_format(tmp_path: Path) -> None:
    fixture = tmp_path / "sample_collection.txt"
    fixture.write_text(
        "\n".join(
            [
                "1 Sol Ring",
                "2 Command Tower",
                "Sol Ring",
                "0 Plains",
                "",
            ]
        ),
        encoding="utf-8",
    )
    entries = import_text(fixture)
    cards = _by_name(entries)

    assert len(entries) == 2
    assert cards["Sol Ring"].quantity == 2
    assert cards["Command Tower"].quantity == 2
    assert cards["Sol Ring"].scryfall_id is None
    assert cards["Command Tower"].scryfall_id is None


def test_empty_and_malformed_rows_are_skipped(tmp_path: Path) -> None:
    csv_file = tmp_path / "malformed.csv"
    csv_file.write_text(
        "\n".join(
            [
                "Name,Set Code,Collector Number,Foil,Quantity,Scryfall ID",
                ",CMM,1,,1,aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "Negate,CMM,88,,abc,bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "Opt,CMM,89,,1,cccccccc-cccc-cccc-cccc-cccccccccccc",
            ]
        ),
        encoding="utf-8",
    )

    entries = import_csv(csv_file)
    cards = _by_name(entries)

    assert len(entries) == 1
    assert "Opt" in cards
    assert "Negate" not in cards


def test_dfc_split_and_adventure_names_are_preserved() -> None:
    entries = import_csv(FIXTURES_DIR / "sample_collection.csv")
    cards = _by_name(entries)

    assert "Delver of Secrets // Insectile Aberration" in cards
    assert "Fire // Ice" in cards
    assert "Bonecrusher Giant // Stomp" in cards


def test_binder_column_is_ignored() -> None:
    entries = import_csv(FIXTURES_DIR / "sample_collection_with_binder.csv")
    cards = _by_name(entries)

    assert len(entries) == 15
    assert cards["Sol Ring"].quantity == 3
    assert cards["Counterspell"].quantity == 3


def test_zero_quantity_rows_are_skipped() -> None:
    entries = import_csv(FIXTURES_DIR / "sample_collection.csv")
    cards = _by_name(entries)

    assert "Plains" not in cards

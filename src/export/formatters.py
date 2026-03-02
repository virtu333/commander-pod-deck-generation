"""Export decklists in various formats.

Supports ManaBox CSV (re-importable), Moxfield text, and Archidekt text.
Also generates summary reports with bracket info and key card highlights.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Iterable

from src.collection.models import Card
from src.deckbuilder.builder import BuiltDeck

SUPPORTED_EXPORT_FORMATS: frozenset[str] = frozenset({"moxfield", "archidekt", "manabox"})


def format_moxfield(deck: BuiltDeck) -> str:
    """Return a Moxfield-friendly text list."""

    lines = [f"1 {deck.commander.name}"]
    for count, card in _aggregate_cards(deck.cards):
        lines.append(f"{count} {card.name}")
    return "\n".join(lines) + "\n"


def format_archidekt(deck: BuiltDeck) -> str:
    """Return an Archidekt-friendly text list with commander section."""

    lines = ["// Commander", f"1 {deck.commander.name}", "", "// Mainboard"]
    for count, card in _aggregate_cards(deck.cards):
        lines.append(f"{count} {card.name}")
    return "\n".join(lines) + "\n"


def format_manabox(deck: BuiltDeck) -> str:
    """Return a ManaBox-compatible CSV export."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "Name",
            "Set Code",
            "Collector Number",
            "Foil",
            "Rarity",
            "Quantity",
            "Scryfall ID",
        ]
    )
    writer.writerow(
        [
            deck.commander.name,
            deck.commander.set_code,
            deck.commander.collector_number,
            "false",
            deck.commander.rarity,
            1,
            deck.commander.scryfall_id,
        ]
    )
    for count, card in _aggregate_cards(deck.cards):
        writer.writerow(
            [
                card.name,
                card.set_code,
                card.collector_number,
                "false",
                card.rarity,
                count,
                card.scryfall_id,
            ]
        )
    return buffer.getvalue()


def write_deck_exports(
    decks: list[BuiltDeck],
    output_dir: Path,
    formats: Iterable[str] = ("moxfield", "archidekt", "manabox"),
) -> list[Path]:
    """Write one file per deck per format and return written paths."""

    normalized_formats = [fmt.strip().casefold() for fmt in formats]
    unique_formats: list[str] = []
    seen: set[str] = set()
    for fmt in normalized_formats:
        if fmt in seen:
            continue
        seen.add(fmt)
        unique_formats.append(fmt)

    invalid_formats = [fmt for fmt in unique_formats if fmt not in SUPPORTED_EXPORT_FORMATS]
    if invalid_formats:
        raise ValueError(
            "Unsupported export format(s): "
            + ", ".join(sorted(set(invalid_formats)))
            + ". Supported: "
            + ", ".join(sorted(SUPPORTED_EXPORT_FORMATS))
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []
    for deck in decks:
        base_name = _slug(deck.commander.name)
        for fmt in unique_formats:
            if fmt == "moxfield":
                content = format_moxfield(deck)
                extension = ".moxfield.txt"
            elif fmt == "archidekt":
                content = format_archidekt(deck)
                extension = ".archidekt.txt"
            else:
                content = format_manabox(deck)
                extension = ".manabox.csv"

            path = output_dir / f"{base_name}{extension}"
            path.write_text(content, encoding="utf-8")
            written_paths.append(path)
    return written_paths


def _aggregate_cards(cards: list[Card]) -> list[tuple[int, Card]]:
    by_name: dict[str, tuple[Card, int]] = {}
    for card in cards:
        key = card.name.strip().casefold()
        if key in by_name:
            existing_card, count = by_name[key]
            by_name[key] = (existing_card, count + 1)
        else:
            by_name[key] = (card, 1)
    aggregated = [(count, card) for card, count in by_name.values()]
    aggregated.sort(key=lambda item: item[1].name.casefold())
    return aggregated


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_")
    return cleaned.casefold() or "deck"

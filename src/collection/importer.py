"""Import card collections from ManaBox CSV exports or plain text lists."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from collections.abc import Sequence

from src.collection.models import RawCardEntry

LOGGER = logging.getLogger(__name__)

_COLUMN_ALIASES = {
    "name": {"name", "card name"},
    "quantity": {"quantity", "qty", "count"},
    "scryfall_id": {"scryfall id", "scryfall_id", "scryfallid"},
    "set_code": {"set code", "set", "set_code"},
    "collector_number": {"collector number", "collector_number"},
    "foil": {"foil", "is foil", "foiled"},
}

_TEXT_ENTRY_RE = re.compile(
    "^\\s*(\\d+)(?:\\s+[x\\u00d7]\\s+|[x\\u00d7]\\s+|\\s+)(.+?)\\s*$",
    re.IGNORECASE,
)


def import_csv(filepath: Path) -> list[RawCardEntry]:
    """Parse a ManaBox CSV and return normalized raw entries."""

    aggregated: dict[tuple[str, ...], RawCardEntry] = {}
    with filepath.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []

        column_lookup = _build_column_lookup(reader.fieldnames)
        name_col = column_lookup.get("name")
        if name_col is None:
            LOGGER.warning("CSV %s is missing a Name column; no cards imported", filepath)
            return []

        for row_number, row in enumerate(reader, start=2):
            name = _clean(row.get(name_col))
            if not name:
                continue

            quantity_col = column_lookup.get("quantity")
            quantity = _parse_int(_clean(row.get(quantity_col))) if quantity_col else 1
            if quantity <= 0:
                continue

            scryfall_col = column_lookup.get("scryfall_id")
            set_col = column_lookup.get("set_code")
            collector_col = column_lookup.get("collector_number")
            foil_col = column_lookup.get("foil")

            scryfall_id = _none_if_empty(_clean(row.get(scryfall_col)))
            set_code = _none_if_empty(_clean(row.get(set_col)))
            collector_number = _none_if_empty(_clean(row.get(collector_col)))
            foil = _parse_bool(_clean(row.get(foil_col)))

            key = _entry_key(name=name, scryfall_id=scryfall_id, set_code=set_code)
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = RawCardEntry(
                    name=name,
                    quantity=quantity,
                    scryfall_id=scryfall_id,
                    set_code=set_code,
                    collector_number=collector_number,
                    foil=foil,
                    source_row=row_number,
                )
                continue

            existing.quantity += quantity

    return list(aggregated.values())


def import_text(filepath: Path) -> list[RawCardEntry]:
    """Parse a plain text list where each line is `1 Card Name` or `Card Name`."""

    aggregated: dict[str, RawCardEntry] = {}
    with filepath.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue

            parsed = _parse_text_entry(line)
            if parsed is None:
                continue
            quantity, name = parsed
            if quantity <= 0 or not name:
                continue

            key = name.casefold()
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = RawCardEntry(
                    name=name,
                    quantity=quantity,
                    scryfall_id=None,
                    set_code=None,
                    collector_number=None,
                    foil=False,
                    source_row=line_number,
                )
                continue

            existing.quantity += quantity

    return list(aggregated.values())


def _build_column_lookup(fieldnames: Sequence[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for original_name in fieldnames:
        normalized = _normalize_header(original_name)
        for canonical, aliases in _COLUMN_ALIASES.items():
            if normalized in aliases and canonical not in lookup:
                lookup[canonical] = original_name
                break
    return lookup


def _entry_key(
    *,
    name: str,
    scryfall_id: str | None,
    set_code: str | None,
) -> tuple[str, ...]:
    if scryfall_id:
        return ("id", scryfall_id.casefold())
    return ("name_set", name.casefold(), (set_code or "").casefold())


def _normalize_header(header: str) -> str:
    return " ".join(header.strip().casefold().split())


def _parse_int(raw: str) -> int:
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        try:
            return int(float(raw))
        except ValueError:
            return 0


def _parse_bool(raw: str) -> bool:
    return raw.casefold() in {"1", "true", "yes", "y", "foil", "foiled"}


def _parse_text_entry(line: str) -> tuple[int, str] | None:
    match = _TEXT_ENTRY_RE.match(line)
    if match:
        quantity = _parse_int(match.group(1))
        name = match.group(2).strip()
        return quantity, name
    return 1, line


def _none_if_empty(value: str) -> str | None:
    return value or None


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


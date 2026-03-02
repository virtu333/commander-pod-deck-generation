"""Validate fixture Scryfall IDs against live Scryfall card data.

This script is optional and intended for manual runs or CI integration jobs.
It is not part of the default unit test run.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import requests


DEFAULT_FIXTURES = (
    Path("tests/fixtures/sample_collection.csv"),
    Path("tests/fixtures/sample_collection_with_binder.csv"),
)


def _validate_file(path: Path) -> tuple[int, list[str]]:
    checked = 0
    failures: list[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            scryfall_id = str(row.get("Scryfall ID") or "").strip()
            if not scryfall_id:
                continue

            name = str(row.get("Name") or "").strip()
            set_code = str(row.get("Set Code") or "").strip().casefold()
            collector_number = str(row.get("Collector Number") or "").strip().casefold()
            checked += 1

            try:
                response = requests.get(
                    f"https://api.scryfall.com/cards/{scryfall_id}",
                    timeout=20,
                )
            except requests.RequestException as exc:
                failures.append(f"{path}:{row_number}: request error: {exc}")
                continue

            if response.status_code != 200:
                failures.append(
                    f"{path}:{row_number}: ID {scryfall_id} returned HTTP {response.status_code}"
                )
                continue

            payload = response.json()
            actual_name = str(payload.get("name") or "").strip()
            actual_set = str(payload.get("set") or "").strip().casefold()
            actual_collector = str(payload.get("collector_number") or "").strip().casefold()

            mismatches: list[str] = []
            if name and actual_name != name:
                mismatches.append(f"name fixture='{name}' api='{actual_name}'")
            if set_code and actual_set != set_code:
                mismatches.append(f"set fixture='{set_code}' api='{actual_set}'")
            if collector_number and actual_collector != collector_number:
                mismatches.append(
                    f"collector fixture='{collector_number}' api='{actual_collector}'"
                )
            if mismatches:
                failures.append(f"{path}:{row_number}: " + "; ".join(mismatches))

    return checked, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate fixture Scryfall IDs.")
    parser.add_argument(
        "fixtures",
        nargs="*",
        type=Path,
        help="Fixture CSV files to validate (defaults to sample fixtures).",
    )
    args = parser.parse_args()

    fixture_paths = tuple(args.fixtures) if args.fixtures else DEFAULT_FIXTURES

    total_checked = 0
    total_failures: list[str] = []

    for fixture_path in fixture_paths:
        checked, failures = _validate_file(fixture_path)
        total_checked += checked
        total_failures.extend(failures)

    if total_failures:
        print(f"Validation failed ({len(total_failures)} issues across {total_checked} rows).")
        for failure in total_failures:
            print(f"- {failure}")
        return 1

    print(f"Validation passed ({total_checked} rows checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

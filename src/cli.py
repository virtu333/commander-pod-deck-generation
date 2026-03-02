"""CLI entry point for the EDH Collection Deck Builder.

Provides commands to build decks, suggest commanders, and estimate brackets.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from src.brackets import BracketEstimator, ComboDetector, GameChangerDetector, RuleChecker
from src.collection.importer import import_csv, import_text
from src.collection.models import Card, Collection, RawCardEntry
from src.collection.resolver import CardResolver
from src.commanders import CommanderSuggester, EDHRecClient
from src.deckbuilder import DeckBuilder, MultiDeckAllocator
from src.export import SUPPORTED_EXPORT_FORMATS, write_deck_exports
from src.utils.cache import CardCache
from src.utils.scryfall import ScryfallClient, ScryfallError

app = typer.Typer(
    name="edh-builder",
    help="Build 4 balanced Commander (EDH) decks from your card collection.",
)
console = Console()


def _load_raw_entries(path: Path) -> list[RawCardEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.casefold() == ".csv":
        return import_csv(path)
    return import_text(path)


def _is_legal_commander(card: Card) -> bool:
    if card.legalities.get("commander", "").casefold() != "legal":
        return False
    type_line = card.type_line.casefold()
    if "legendary" in type_line and "creature" in type_line:
        return True
    return "can be your commander" in card.oracle_text.casefold()


def _commander_key(card: Card) -> str:
    return card.name.strip().casefold()


def _resolve_selected_commanders(
    selected_names: list[str],
    collection: Collection,
) -> list[Card]:
    selected_cards: list[Card] = []
    missing_names: list[str] = []
    invalid_commanders: list[str] = []
    seen: set[str] = set()

    for raw_name in selected_names:
        normalized = raw_name.strip()
        if not normalized:
            continue
        owned = collection.find(normalized)
        if owned is None:
            missing_names.append(raw_name)
            continue
        card = owned.card
        key = _commander_key(card)
        if key in seen:
            continue
        if not _is_legal_commander(card):
            invalid_commanders.append(card.name)
            continue
        seen.add(key)
        selected_cards.append(card)

    if missing_names:
        raise ValueError(
            "Commander(s) not found in resolved collection: "
            + ", ".join(missing_names)
        )
    if invalid_commanders:
        raise ValueError(
            "Card(s) are not commander-legal: "
            + ", ".join(sorted(set(invalid_commanders), key=str.casefold))
        )
    return selected_cards


def _resolve_selected_commanders_for_estimate(
    selected_names: list[str],
    collection: Collection,
    scryfall: ScryfallClient,
) -> list[Card]:
    """Resolve explicit commanders from decklist first, then exact-name Scryfall search."""

    selected_cards: list[Card] = []
    missing_names: list[str] = []
    invalid_commanders: list[str] = []
    seen: set[str] = set()

    for raw_name in selected_names:
        normalized = raw_name.strip()
        if not normalized:
            continue

        owned = collection.find(normalized)
        card: Card | None = owned.card if owned is not None else None
        if card is None:
            try:
                matches = scryfall.search(f'!"{normalized}"')
            except ScryfallError as exc:
                raise ValueError(
                    f"Failed to resolve commander '{raw_name}' via Scryfall: {exc}"
                ) from exc
            if matches:
                normalized_key = normalized.casefold()
                exact = next(
                    (
                        match
                        for match in matches
                        if match.name.strip().casefold() == normalized_key
                    ),
                    None,
                )
                card = exact or matches[0]

        if card is None:
            missing_names.append(raw_name)
            continue

        key = _commander_key(card)
        if key in seen:
            continue
        if not _is_legal_commander(card):
            invalid_commanders.append(card.name)
            continue
        seen.add(key)
        selected_cards.append(card)

    if missing_names:
        raise ValueError(
            "Commander(s) could not be resolved from decklist or Scryfall: "
            + ", ".join(missing_names)
        )
    if invalid_commanders:
        raise ValueError(
            "Card(s) are not commander-legal: "
            + ", ".join(sorted(set(invalid_commanders), key=str.casefold))
        )
    return selected_cards


def _resolve_collection(
    collection_path: Path,
    scryfall: ScryfallClient,
) -> Collection:
    entries = _load_raw_entries(collection_path)
    if not entries:
        raise ValueError(f"No importable rows found in file: {collection_path}")
    resolver = CardResolver(scryfall=scryfall, console=console)
    collection = resolver.resolve(entries)
    if not collection.cards:
        raise ValueError("No cards were resolved from the provided collection file.")
    return collection


def _print_actionable_error(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")


@app.command()
def build(
    collection: Path = typer.Option(..., help="Path to ManaBox CSV export"),
    commanders: list[str] = typer.Option(
        default_factory=list,
        help="Commander name(s) to use (1-4). Tool suggests the rest.",
    ),
    output_dir: Path = typer.Option(
        Path("out/decks"),
        help="Directory to write exported decklists.",
    ),
    formats: list[str] = typer.Option(
        ["moxfield", "archidekt", "manabox"],
        "--format",
        "-f",
        help="Export format(s): moxfield, archidekt, manabox.",
    ),
) -> None:
    """Build 4 Commander decks, allocate shared cards, estimate brackets, export outputs."""

    if len(commanders) > 4:
        _print_actionable_error("Provide at most 4 commanders.")
        raise typer.Exit(code=1)

    normalized_formats = [fmt.strip().casefold() for fmt in formats]
    invalid_formats = [
        fmt for fmt in normalized_formats if fmt not in SUPPORTED_EXPORT_FORMATS
    ]
    if invalid_formats:
        _print_actionable_error(
            "Unsupported format(s): "
            + ", ".join(sorted(set(invalid_formats)))
            + ". Supported: "
            + ", ".join(sorted(SUPPORTED_EXPORT_FORMATS))
        )
        raise typer.Exit(code=1)

    try:
        with CardCache() as cache:
            scryfall = ScryfallClient(cache)
            collection_obj = _resolve_collection(collection, scryfall=scryfall)

            edhrec = EDHRecClient(cache)
            suggester = CommanderSuggester(edhrec)
            selected_cards = _resolve_selected_commanders(commanders, collection_obj)
            if len(selected_cards) < 4:
                suggestions = suggester.suggest(
                    collection=collection_obj,
                    selected=selected_cards,
                    count=4,
                )
                selected_cards.extend(candidate.card for candidate in suggestions)

            deduped_commanders: list[Card] = []
            seen_commander_keys: set[str] = set()
            for card in selected_cards:
                key = _commander_key(card)
                if key in seen_commander_keys:
                    continue
                seen_commander_keys.add(key)
                deduped_commanders.append(card)

            if len(deduped_commanders) != 4:
                raise ValueError(
                    f"Unable to assemble 4 commanders. Resolved {len(deduped_commanders)}."
                )

            builder = DeckBuilder(edhrec=edhrec)
            provisional = [
                builder.build(commander=commander, collection=collection_obj)
                for commander in deduped_commanders
            ]

            allocator = MultiDeckAllocator()
            allocated = allocator.allocate(collection=collection_obj, decks=provisional)
            for deck in allocated:
                if len(deck.cards) != 99:
                    raise RuntimeError(
                        f"Allocator produced invalid deck size for {deck.commander.name}: "
                        f"{len(deck.cards)} non-commander cards"
                    )

            estimator = BracketEstimator(
                game_changers=GameChangerDetector(scryfall),
                rules=RuleChecker(),
                combo_detector=ComboDetector(cache),
            )
            estimates = [
                estimator.estimate(cards=deck.cards, commanders=[deck.commander])
                for deck in allocated
            ]
            written_paths = write_deck_exports(
                decks=allocated,
                output_dir=output_dir,
                formats=normalized_formats,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _print_actionable_error(str(exc))
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        _print_actionable_error(f"Build failed unexpectedly: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Built {len(allocated)} deck(s) from {collection}. "
        f"Exported {len(written_paths)} file(s) to {output_dir}."
    )
    for deck, estimate in zip(allocated, estimates, strict=True):
        console.print(
            f"- {deck.commander.name}: 100 cards total, "
            f"Bracket {estimate.bracket} ({estimate.confidence})"
        )


@app.command()
def suggest(
    collection: Path = typer.Option(..., help="Path to ManaBox CSV export"),
    selected: list[str] = typer.Option(
        default_factory=list,
        help="Already selected commander names to complement.",
    ),
    count: int = typer.Option(4, min=1, max=4, help="Target total commanders."),
) -> None:
    """Suggest commanders based on your collection."""

    try:
        with CardCache() as cache:
            scryfall = ScryfallClient(cache)
            collection_obj = _resolve_collection(collection, scryfall=scryfall)
            edhrec = EDHRecClient(cache)
            suggester = CommanderSuggester(edhrec)
            selected_cards = _resolve_selected_commanders(selected, collection_obj)
            suggestions = suggester.suggest(
                collection=collection_obj,
                selected=selected_cards,
                count=count,
            )
    except (FileNotFoundError, ValueError) as exc:
        _print_actionable_error(str(exc))
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        _print_actionable_error(f"Suggestion failed unexpectedly: {exc}")
        raise typer.Exit(code=1) from exc

    if not suggestions:
        console.print("No commander suggestions available for the provided collection.")
        return

    console.print(f"Top {len(suggestions)} suggestion(s):")
    for index, suggestion in enumerate(suggestions, start=1):
        console.print(
            f"{index}. {suggestion.card.name} "
            f"(score={suggestion.score:.3f}, "
            f"diversity={suggestion.color_diversity_score:.3f}, "
            f"overlap={suggestion.collection_overlap:.3f}, "
            f"buildable={suggestion.buildable_count})"
        )


@app.command(name="estimate-bracket")
def estimate_bracket(
    decklist: Path = typer.Option(..., help="Path to a decklist file"),
    commander: list[str] = typer.Option(
        [],
        "--commander",
        "-c",
        help="Commander card name(s). If omitted, auto-detect when possible.",
    ),
) -> None:
    """Estimate bracket for a decklist using local rules + Spellbook."""

    try:
        with CardCache() as cache:
            scryfall = ScryfallClient(cache)
            collection_obj = _resolve_collection(decklist, scryfall=scryfall)

            if commander:
                selected_commanders = _resolve_selected_commanders_for_estimate(
                    selected_names=commander,
                    collection=collection_obj,
                    scryfall=scryfall,
                )
            else:
                legal_commanders = [
                    owned.card
                    for owned in collection_obj.cards
                    if _is_legal_commander(owned.card)
                ]
                if len(legal_commanders) != 1:
                    raise ValueError(
                        "Unable to auto-detect a single commander. "
                        "Pass --commander explicitly."
                    )
                selected_commanders = legal_commanders

            commander_keys = {_commander_key(card) for card in selected_commanders}
            non_commander_cards: list[Card] = [
                owned.card
                for owned in collection_obj.cards
                if _commander_key(owned.card) not in commander_keys
            ]

            estimator = BracketEstimator(
                game_changers=GameChangerDetector(scryfall),
                rules=RuleChecker(),
                combo_detector=ComboDetector(cache),
            )
            estimate = estimator.estimate(
                cards=non_commander_cards,
                commanders=selected_commanders,
            )
    except (FileNotFoundError, ValueError) as exc:
        _print_actionable_error(str(exc))
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        _print_actionable_error(f"Bracket estimation failed unexpectedly: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Estimated Bracket: {estimate.bracket} ({estimate.confidence})")
    if estimate.spellbook_bracket_tag:
        console.print(f"Spellbook Tag: {estimate.spellbook_bracket_tag}")
    for reason in estimate.reasons:
        console.print(f"- {reason}")


if __name__ == "__main__":
    app()

"""End-to-end flow: import -> suggest -> build -> allocate -> estimate -> export."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from src.brackets.estimator import BracketEstimator
from src.brackets.combo_detector import SpellbookResult
from src.collection.importer import import_csv
from src.collection.models import Card
from src.collection.resolver import CardResolver
from src.commanders.suggester import CommanderSuggester
from src.deckbuilder import DeckBuilder, MultiDeckAllocator
from src.export import write_deck_exports


def _card(
    scryfall_id: str,
    name: str,
    *,
    colors: list[str],
    type_line: str,
    commander_legal: bool = True,
) -> Card:
    return Card(
        scryfall_id=scryfall_id,
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=colors,
        type_line=type_line,
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal" if commander_legal else "not_legal"},
        set_code="tst",
        collector_number="1",
        rarity="rare",
    )


class _FakeScryfallClient:
    def __init__(self, by_id: dict[str, Card]) -> None:
        self.by_id = by_id
        self.by_name: dict[str, list[Card]] = {}
        for card in by_id.values():
            key = card.name.strip().casefold()
            self.by_name.setdefault(key, []).append(card)

    def get_card_cached(self, scryfall_id: str) -> Card | None:
        return self.by_id.get(scryfall_id)

    def get_card_by_name(
        self,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
    ) -> Card | None:
        candidates = self.by_name.get(name.strip().casefold(), [])
        if set_code is not None:
            set_matches = [card for card in candidates if card.set_code.casefold() == set_code.casefold()]
            if set_matches:
                candidates = set_matches
        if collector_number is not None:
            target = collector_number.strip().casefold()
            collector_matches = [
                card for card in candidates if card.collector_number.strip().casefold() == target
            ]
            if collector_matches:
                candidates = collector_matches
        return candidates[0] if candidates else None

    def get_card(self, scryfall_id: str) -> Card | None:
        return self.by_id.get(scryfall_id)

    def search(self, query: str) -> list[Card]:
        return []


class _FakeEDHRecClient:
    def get_commander_profile(self, commander_name: str):  # noqa: ANN201
        return None

    def get_average_deck(self, commander_name: str):  # noqa: ANN201
        return None


class _FakeGameChangers:
    def detect(self, cards: list[Card]) -> tuple[list[Card], bool]:
        return [], True


class _FakeRules:
    def find_mld(self, cards: list[Card]) -> list[Card]:
        return []

    def find_extra_turns(self, cards: list[Card]) -> list[Card]:
        return []


class _FakeComboDetector:
    def estimate_bracket(self, card_names: list[str], commander_names: list[str]) -> SpellbookResult | None:
        return SpellbookResult(
            bracket_tag="O",
            bracket=2,
            game_changer_cards=[],
            mld_cards=[],
            extra_turn_cards=[],
            combos=[],
        )


def test_e2e_import_suggest_build_allocate_estimate_export(tmp_path: Path) -> None:
    cards = {
        "cmdr-a": _card("cmdr-a", "Commander A", colors=["W"], type_line="Legendary Creature"),
        "cmdr-b": _card("cmdr-b", "Commander B", colors=["U"], type_line="Legendary Creature"),
        "cmdr-c": _card("cmdr-c", "Commander C", colors=["B"], type_line="Legendary Creature"),
        "cmdr-d": _card("cmdr-d", "Commander D", colors=["R"], type_line="Legendary Creature"),
        "sol-ring": _card("sol-ring", "Sol Ring", colors=[], type_line="Artifact"),
        "arcane-signet": _card("arcane-signet", "Arcane Signet", colors=[], type_line="Artifact"),
        "white-card": _card("white-card", "White Spell", colors=["W"], type_line="Instant"),
        "blue-card": _card("blue-card", "Blue Spell", colors=["U"], type_line="Instant"),
        "black-card": _card("black-card", "Black Spell", colors=["B"], type_line="Instant"),
        "red-card": _card("red-card", "Red Spell", colors=["R"], type_line="Instant"),
    }

    csv_path = tmp_path / "collection.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Set Code,Collector Number,Foil,Rarity,Quantity,Scryfall ID",
                "Commander A,tst,1,false,rare,1,cmdr-a",
                "Commander B,tst,1,false,rare,1,cmdr-b",
                "Commander C,tst,1,false,rare,1,cmdr-c",
                "Commander D,tst,1,false,rare,1,cmdr-d",
                "Sol Ring,tst,1,false,rare,1,sol-ring",
                "Arcane Signet,tst,1,false,rare,1,arcane-signet",
                "White Spell,tst,1,false,common,1,white-card",
                "Blue Spell,tst,1,false,common,1,blue-card",
                "Black Spell,tst,1,false,common,1,black-card",
                "Red Spell,tst,1,false,common,1,red-card",
            ]
        ),
        encoding="utf-8",
    )

    entries = import_csv(csv_path)
    resolver = CardResolver(
        scryfall=_FakeScryfallClient(cards),
        console=Console(file=io.StringIO(), force_terminal=False),
    )
    collection = resolver.resolve(entries)

    suggester = CommanderSuggester(_FakeEDHRecClient())
    selected = [collection.find("Commander A").card]  # type: ignore[union-attr]
    suggestions = suggester.suggest(collection=collection, selected=selected, count=4)
    commanders = selected + [candidate.card for candidate in suggestions]
    assert len(commanders) == 4

    builder = DeckBuilder(edhrec=_FakeEDHRecClient())
    provisional = [builder.build(commander, collection) for commander in commanders]
    allocator = MultiDeckAllocator()
    allocated = allocator.allocate(collection, provisional)
    assert len(allocated) == 4
    assert all(len(deck.cards) == 99 for deck in allocated)

    owned_nonbasic: dict[str, int] = {}
    for owned in collection.cards:
        if owned.card.type_line.casefold().startswith("basic land"):
            continue
        if owned.card.name in {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}:
            continue
        owned_nonbasic[owned.card.name.casefold()] = owned_nonbasic.get(owned.card.name.casefold(), 0) + owned.quantity

    allocated_nonbasic: dict[str, int] = {}
    for deck in allocated:
        for card in deck.cards:
            key = card.name.casefold()
            if key in {"plains", "island", "swamp", "mountain", "forest", "wastes"}:
                continue
            allocated_nonbasic[key] = allocated_nonbasic.get(key, 0) + 1

    for name_key, used in allocated_nonbasic.items():
        assert used <= owned_nonbasic.get(name_key, 0)

    estimator = BracketEstimator(
        game_changers=_FakeGameChangers(),
        rules=_FakeRules(),
        combo_detector=_FakeComboDetector(),
    )
    estimates = [estimator.estimate(cards=deck.cards, commanders=[deck.commander]) for deck in allocated]
    assert all(1 <= estimate.bracket <= 5 for estimate in estimates)

    exports = write_deck_exports(
        allocated,
        output_dir=tmp_path / "exports",
        formats=["moxfield", "archidekt", "manabox"],
    )
    assert len(exports) == 12
    assert all(path.exists() for path in exports)

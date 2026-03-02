"""Tests for card resolution into Collection objects."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from src.collection.models import Card, RawCardEntry
from src.collection.resolver import CardResolver
from src.utils.scryfall import ScryfallError


def _card(card_id: str, name: str, set_code: str = "cmm") -> Card:
    return Card(
        scryfall_id=card_id,
        name=name,
        mana_cost="{1}",
        cmc=1,
        color_identity=[],
        type_line="Artifact",
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code=set_code,
        collector_number="1",
        rarity="rare",
    )


class FakeScryfall:
    def __init__(self) -> None:
        self.cached_cards_by_id: dict[str, Card] = {}
        self.cards_by_id: dict[str, Card] = {}
        self.cards_by_oracle_name: dict[tuple[str, str | None, str | None], Card] = {}
        self.search_by_query: dict[str, list[Card]] = {}
        self.error_ids: set[str] = set()
        self.error_oracle_names: set[str] = set()
        self.error_queries: set[str] = set()
        self.get_card_cached_calls: list[str] = []
        self.get_card_calls: list[str] = []
        self.get_card_by_name_calls: list[tuple[str, str | None, str | None]] = []
        self.search_queries: list[str] = []

    def get_card_cached(self, scryfall_id: str):
        self.get_card_cached_calls.append(scryfall_id)
        return self.cached_cards_by_id.get(scryfall_id)

    def get_card(self, scryfall_id: str):
        self.get_card_calls.append(scryfall_id)
        if scryfall_id in self.error_ids:
            raise ScryfallError("boom")
        return self.cards_by_id.get(scryfall_id)

    def search(self, query: str):
        self.search_queries.append(query)
        if query in self.error_queries:
            raise ScryfallError("boom")
        return self.search_by_query.get(query, [])

    def get_card_by_name(
        self,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
    ):
        self.get_card_by_name_calls.append((name, set_code, collector_number))
        if name in self.error_oracle_names:
            raise ScryfallError("boom")

        exact = self.cards_by_oracle_name.get((name, set_code, collector_number))
        if exact is not None:
            return exact
        return self.cards_by_oracle_name.get((name, None, None))


def test_resolve_entries_with_ids_success() -> None:
    fake = FakeScryfall()
    fake.cards_by_id["card-1"] = _card("card-1", "Sol Ring")
    fake.cards_by_id["card-2"] = _card("card-2", "Command Tower")

    buffer = StringIO()
    resolver = CardResolver(fake, console=Console(file=buffer, force_terminal=False))
    collection = resolver.resolve(
        [
            RawCardEntry("Sol Ring", 2, "card-1", "cmm", "1", False, source_row=2),
            RawCardEntry("Command Tower", 1, "card-2", "cmm", "2", False, source_row=3),
        ]
    )

    assert len(collection.cards) == 2
    assert collection.unresolved == []
    assert collection.card_count == 3
    sol_ring = collection.find("Sol Ring")
    assert sol_ring is not None
    assert sol_ring.quantity == 2


def test_resolve_without_ids_uses_search() -> None:
    fake = FakeScryfall()
    fake.search_by_query['!"Fire // Ice"'] = [_card("fire-ice", "Fire // Ice", set_code="mh2")]

    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))
    collection = resolver.resolve(
        [RawCardEntry("Fire // Ice", 1, None, "mh2", None, False, source_row=1)]
    )

    assert len(collection.cards) == 1
    assert fake.search_queries == ['!"Fire // Ice"']


def test_id_miss_uses_oracle_name_lookup_before_search() -> None:
    fake = FakeScryfall()
    fake.cards_by_oracle_name[("Sol Ring", "c21", "263")] = _card(
        "oracle-sol-ring",
        "Sol Ring",
        set_code="c21",
    )
    fake.search_by_query['!"Sol Ring"'] = [_card("search-sol-ring", "Sol Ring", set_code="c21")]
    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))

    collection = resolver.resolve(
        [RawCardEntry("Sol Ring", 1, "stale-id", "c21", "263", False, source_row=2)]
    )

    assert fake.get_card_cached_calls == ["stale-id"]
    assert fake.get_card_calls == []
    assert fake.get_card_by_name_calls == [("Sol Ring", "c21", "263")]
    assert fake.search_queries == []
    assert len(collection.cards) == 1
    assert collection.cards[0].card.scryfall_id == "oracle-sol-ring"


def test_id_miss_falls_back_to_exact_name_search() -> None:
    fake = FakeScryfall()
    fake.search_by_query['!"Sol Ring"'] = [_card("fallback-sol-ring", "Sol Ring", set_code="c21")]
    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))

    collection = resolver.resolve(
        [RawCardEntry("Sol Ring", 1, "stale-id", "c21", "263", False, source_row=2)]
    )

    assert fake.get_card_cached_calls == ["stale-id"]
    assert fake.get_card_calls == ["stale-id"]
    assert fake.get_card_by_name_calls == [("Sol Ring", "c21", "263")]
    assert fake.search_queries == ['!"Sol Ring"']
    assert len(collection.cards) == 1
    assert collection.cards[0].card.scryfall_id == "fallback-sol-ring"
    assert collection.unresolved == []


def test_unresolvable_card_is_returned_in_unresolved() -> None:
    fake = FakeScryfall()
    buffer = StringIO()
    resolver = CardResolver(fake, console=Console(file=buffer, force_terminal=False))
    collection = resolver.resolve(
        [RawCardEntry("Missing Card", 1, "missing-id", "cmm", None, False, source_row=9)]
    )

    assert collection.cards == []
    assert len(collection.unresolved) == 1
    unresolved = collection.unresolved[0]
    assert unresolved.name == "Missing Card"
    assert unresolved.reason == "not_found"
    assert unresolved.source_row == 9
    assert fake.get_card_cached_calls == ["missing-id"]
    assert fake.get_card_calls == ["missing-id"]
    assert fake.search_queries == ['!"Missing Card"']


def test_cold_id_cache_oracle_hit_skips_live_get_card() -> None:
    fake = FakeScryfall()
    fake.cards_by_id["stale-id"] = _card("live-sol-ring", "Sol Ring", set_code="c21")
    fake.cards_by_oracle_name[("Sol Ring", "c21", "263")] = _card(
        "oracle-sol-ring",
        "Sol Ring",
        set_code="c21",
    )
    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))

    collection = resolver.resolve(
        [RawCardEntry("Sol Ring", 1, "stale-id", "c21", "263", False, source_row=2)]
    )

    assert fake.get_card_cached_calls == ["stale-id"]
    assert fake.get_card_calls == []
    assert fake.search_queries == []
    assert len(collection.cards) == 1
    assert collection.cards[0].card.scryfall_id == "oracle-sol-ring"


def test_api_error_card_is_returned_as_unresolved_api_error() -> None:
    fake = FakeScryfall()
    fake.error_ids.add("error-id")
    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))

    collection = resolver.resolve(
        [RawCardEntry("API Error Card", 1, "error-id", "cmm", None, False, source_row=4)]
    )

    assert len(collection.unresolved) == 1
    assert collection.unresolved[0].reason == "api_error"


def test_search_api_error_is_returned_as_unresolved_api_error() -> None:
    fake = FakeScryfall()
    fake.error_queries.add('!"Explosive Entry"')
    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))

    collection = resolver.resolve(
        [RawCardEntry("Explosive Entry", 1, None, "cmm", None, False, source_row=7)]
    )

    assert len(collection.unresolved) == 1
    assert collection.unresolved[0].reason == "api_error"
    assert collection.cards == []


def test_quantity_deduplication_after_resolution() -> None:
    fake = FakeScryfall()
    shared = _card("shared-1", "Counterspell", set_code="2x2")
    fake.cards_by_id["shared-1"] = shared
    fake.search_by_query['!"Counterspell"'] = [shared]

    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))
    collection = resolver.resolve(
        [
            RawCardEntry("Counterspell", 1, "shared-1", "2x2", "48", False, source_row=2),
            RawCardEntry("Counterspell", 2, None, "2x2", "48", False, source_row=3),
        ]
    )

    assert len(collection.cards) == 1
    assert collection.cards[0].quantity == 3


def test_multi_result_disambiguation_prefers_set_then_collector() -> None:
    fake = FakeScryfall()
    candidate_a = _card("card-a", "Fire // Ice", set_code="mh2")
    candidate_a.collector_number = "283"
    candidate_b = _card("card-b", "Fire // Ice", set_code="mh2")
    candidate_b.collector_number = "290"
    candidate_c = _card("card-c", "Fire // Ice", set_code="2x2")
    candidate_c.collector_number = "290"
    fake.search_by_query['!"Fire // Ice"'] = [candidate_a, candidate_b, candidate_c]

    resolver = CardResolver(fake, console=Console(file=StringIO(), force_terminal=False))
    collection = resolver.resolve(
        [RawCardEntry("Fire // Ice", 1, None, "MH2", "290", False, source_row=3)]
    )

    assert len(collection.cards) == 1
    assert collection.cards[0].card.scryfall_id == "card-b"


def test_summary_output_includes_unresolved_count() -> None:
    fake = FakeScryfall()
    fake.cards_by_id["card-1"] = _card("card-1", "Sol Ring")
    output = StringIO()
    resolver = CardResolver(fake, console=Console(file=output, force_terminal=False))

    resolver.resolve(
        [
            RawCardEntry("Sol Ring", 1, "card-1", "cmm", "1", False, source_row=2),
            RawCardEntry("Missing Card", 1, "missing-id", "cmm", None, False, source_row=3),
        ]
    )

    summary = output.getvalue()
    assert "Resolved 1/2 cards" in summary
    assert "1 unresolved" in summary

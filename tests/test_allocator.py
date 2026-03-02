"""Tests for multi-deck allocator constraints and determinism."""

from __future__ import annotations

from copy import deepcopy

from src.collection.models import Card, Collection, OwnedCard
from src.deckbuilder import MultiDeckAllocator
from src.deckbuilder.builder import BuiltDeck


def _card(
    name: str,
    colors: list[str] | None = None,
    *,
    scryfall_id: str | None = None,
    type_line: str = "Artifact",
) -> Card:
    return Card(
        scryfall_id=scryfall_id or name.casefold().replace(" ", "-"),
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=colors or [],
        type_line=type_line,
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="tst",
        collector_number="1",
        rarity="common",
    )


def _commander(name: str, colors: list[str]) -> Card:
    return _card(name, colors, type_line="Legendary Creature")


def _owned(card: Card, quantity: int = 1) -> OwnedCard:
    return OwnedCard(card=card, quantity=quantity)


def _deck(commander: Card, cards: list[Card], scores: dict[str, float] | None = None) -> BuiltDeck:
    return BuiltDeck(
        commander=commander,
        cards=cards,
        scores=scores or {card.name.casefold(): 0.0 for card in cards},
        basics_added={},
        edhrec_available=False,
    )


def _collection(cards: list[OwnedCard]) -> Collection:
    return Collection(cards=cards, unresolved=[], import_date="2026-03-02")


def test_allocate_respects_nonbasic_copy_limits() -> None:
    allocator = MultiDeckAllocator()
    commander_a = _commander("Commander A", ["W"])
    commander_b = _commander("Commander B", ["U"])
    sol_ring = _card("Sol Ring", [])
    support_w = _card("White Support", ["W"], type_line="Instant")
    support_u = _card("Blue Support", ["U"], type_line="Instant")

    collection = _collection(
        [
            _owned(sol_ring, quantity=1),
            _owned(support_w, quantity=2),
            _owned(support_u, quantity=2),
        ]
    )
    decks = [
        _deck(commander_a, [sol_ring, support_w], scores={"sol ring": 1.0, "white support": 0.5}),
        _deck(commander_b, [sol_ring, support_u], scores={"sol ring": 0.4, "blue support": 0.5}),
    ]

    allocated = allocator.allocate(collection, decks)
    sol_ring_total = sum(
        1
        for deck in allocated
        for card in deck.cards
        if card.name == "Sol Ring"
    )
    assert sol_ring_total == 1
    assert len(allocated[0].cards) == 99
    assert len(allocated[1].cards) == 99


def test_allocator_reserves_commander_copies_across_decks() -> None:
    allocator = MultiDeckAllocator()
    shared_commander = _commander("Shared Commander", ["W"])
    other_commander = _commander("Other Commander", ["W"])
    collection = _collection([_owned(shared_commander, quantity=1)])
    decks = [
        _deck(shared_commander, []),
        _deck(other_commander, [shared_commander], scores={"shared commander": 1.0}),
    ]

    allocated = allocator.allocate(collection, decks)
    total_usage = sum(
        (1 if deck.commander.name == "Shared Commander" else 0)
        + sum(1 for card in deck.cards if card.name == "Shared Commander")
        for deck in allocated
    )
    assert total_usage == 1
    assert not any(card.name == "Shared Commander" for card in allocated[1].cards)


def test_allocator_allows_extra_copy_after_commander_reservation() -> None:
    allocator = MultiDeckAllocator()
    shared_commander = _commander("Shared Commander", ["W"])
    other_commander = _commander("Other Commander", ["W"])
    collection = _collection([_owned(shared_commander, quantity=2)])
    decks = [
        _deck(shared_commander, []),
        _deck(other_commander, [shared_commander], scores={"shared commander": 1.0}),
    ]

    allocated = allocator.allocate(collection, decks)
    total_usage = sum(
        (1 if deck.commander.name == "Shared Commander" else 0)
        + sum(1 for card in deck.cards if card.name == "Shared Commander")
        for deck in allocated
    )
    assert total_usage == 2
    assert any(card.name == "Shared Commander" for card in allocated[1].cards)


def test_allocate_prefers_higher_score_on_conflicts() -> None:
    allocator = MultiDeckAllocator()
    commander_a = _commander("Commander A", ["W"])
    commander_b = _commander("Commander B", ["W"])
    contested = _card("Smothering Tithe", ["W"], type_line="Enchantment")

    collection = _collection([_owned(contested, quantity=1)])
    decks = [
        _deck(commander_a, [contested], scores={"smothering tithe": 0.9}),
        _deck(commander_b, [contested], scores={"smothering tithe": 0.1}),
    ]

    allocated = allocator.allocate(collection, decks)
    assert any(card.name == "Smothering Tithe" for card in allocated[0].cards)
    assert not any(card.name == "Smothering Tithe" for card in allocated[1].cards)


def test_allocator_replacement_prefers_higher_scored_fallback() -> None:
    allocator = MultiDeckAllocator()
    commander_a = _commander("Commander A", ["W"])
    commander_b = _commander("Commander B", ["W"])
    contested = _card("Contested Relic", ["W"], type_line="Artifact")
    fallback_low = _card("Alpha Fallback", ["W"], type_line="Instant")
    fallback_high = _card("Zulu Fallback", ["W"], type_line="Instant")
    winner_fillers = [
        _card(f"Winner Filler {index}", ["W"], type_line="Creature")
        for index in range(1, 99)
    ]
    loser_fillers = [
        _card(f"Loser Filler {index}", ["W"], type_line="Creature")
        for index in range(1, 99)
    ]

    collection = _collection(
        [_owned(contested, quantity=1), _owned(fallback_low, quantity=1), _owned(fallback_high, quantity=1)]
        + [_owned(card, quantity=1) for card in winner_fillers]
        + [_owned(card, quantity=1) for card in loser_fillers]
    )
    deck_a_scores = {"contested relic": 1.0}
    deck_a_scores.update({card.name.casefold(): 0.5 for card in winner_fillers})
    deck_a = _deck(commander_a, [contested, *winner_fillers], scores=deck_a_scores)
    deck_b_scores = {
        "contested relic": 0.1,
        "alpha fallback": 0.2,
        "zulu fallback": 0.9,
    }
    deck_b_scores.update({card.name.casefold(): 0.5 for card in loser_fillers})
    deck_b = _deck(commander_b, [contested, *loser_fillers], scores=deck_b_scores)

    allocated = allocator.allocate(collection, [deck_a, deck_b])
    deck_b_names = {card.name for card in allocated[1].cards}
    assert "Zulu Fallback" in deck_b_names
    assert "Alpha Fallback" not in deck_b_names


def test_allocate_basics_are_unlimited() -> None:
    allocator = MultiDeckAllocator()
    commander = _commander("Mono Red", ["R"])
    collection = _collection([])
    decks = [_deck(commander, [])]

    allocated = allocator.allocate(collection, decks)
    assert len(allocated[0].cards) == 99
    assert sum(1 for card in allocated[0].cards if card.name == "Mountain") == 99


def test_allocator_is_deterministic() -> None:
    allocator = MultiDeckAllocator()
    commander_a = _commander("Commander A", ["W", "U"])
    commander_b = _commander("Commander B", ["W", "U"])
    card_a = _card("Arcane Signet", [])
    card_b = _card("Counterspell", ["U"], type_line="Instant")
    collection = _collection([_owned(card_a, 2), _owned(card_b, 2)])
    decks = [
        _deck(commander_a, [card_a, card_b], scores={"arcane signet": 0.7, "counterspell": 0.8}),
        _deck(commander_b, [card_a, card_b], scores={"arcane signet": 0.6, "counterspell": 0.9}),
    ]

    first = allocator.allocate(collection, decks)
    second = allocator.allocate(collection, decks)

    first_names = [[card.name for card in deck.cards] for deck in first]
    second_names = [[card.name for card in deck.cards] for deck in second]
    assert first_names == second_names


def test_allocator_does_not_mutate_collection_quantities() -> None:
    allocator = MultiDeckAllocator()
    commander = _commander("Commander A", ["G"])
    ramp = _card("Cultivate", ["G"], type_line="Sorcery")
    collection = _collection([_owned(ramp, 1)])
    before = deepcopy(collection.cards)

    allocator.allocate(collection, [_deck(commander, [ramp])])

    assert [owned.quantity for owned in collection.cards] == [owned.quantity for owned in before]
    assert [owned.card.name for owned in collection.cards] == [owned.card.name for owned in before]


def test_allocator_enforces_color_identity_legality() -> None:
    allocator = MultiDeckAllocator()
    commander = _commander("Mono White", ["W"])
    legal = _card("Swords to Plowshares", ["W"], type_line="Instant")
    illegal = _card("Lightning Bolt", ["R"], type_line="Instant")
    collection = _collection([_owned(legal, 1), _owned(illegal, 1)])
    deck = _deck(commander, [legal, illegal], scores={"swords to plowshares": 0.8, "lightning bolt": 0.9})

    allocated = allocator.allocate(collection, [deck])[0]
    names = {card.name for card in allocated.cards}
    assert "Swords to Plowshares" in names
    assert "Lightning Bolt" not in names

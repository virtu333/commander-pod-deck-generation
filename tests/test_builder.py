"""Tests for single-deck builder behavior."""

from __future__ import annotations

from src.collection.models import Card, Collection, OwnedCard
from src.commanders.edhrec_client import CommanderProfile, EDHRecCard
from src.deckbuilder import DeckBuilder, DeckTemplate


class FakeEDHRecClient:
    def __init__(
        self,
        *,
        profiles: dict[str, CommanderProfile | None] | None = None,
        avg_decks: dict[str, list[str] | None] | None = None,
    ) -> None:
        self.profiles = profiles or {}
        self.avg_decks = avg_decks or {}
        self.profile_calls: list[str] = []
        self.avg_calls: list[str] = []

    def get_commander_profile(self, commander_name: str) -> CommanderProfile | None:
        key = commander_name.strip().casefold()
        self.profile_calls.append(key)
        return self.profiles.get(key)

    def get_average_deck(self, commander_name: str) -> list[str] | None:
        key = commander_name.strip().casefold()
        self.avg_calls.append(key)
        return self.avg_decks.get(key)


def _card(
    name: str,
    colors: list[str] | None = None,
    *,
    type_line: str = "Creature",
    scryfall_id: str | None = None,
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


def _owned(card: Card, quantity: int = 1) -> OwnedCard:
    return OwnedCard(card=card, quantity=quantity)


def _collection(cards: list[OwnedCard]) -> Collection:
    return Collection(cards=cards, unresolved=[], import_date="2026-03-02")


def _support_cards(
    prefix: str,
    count: int,
    *,
    colors: list[str] | None = None,
    type_line: str = "Creature",
) -> list[OwnedCard]:
    return [
        _owned(_card(f"{prefix} {index}", colors or [], type_line=type_line))
        for index in range(1, count + 1)
    ]


def _profile(
    commander_name: str,
    cards: list[tuple[str, float, float]],
) -> CommanderProfile:
    edhrec_cards = [
        EDHRecCard(
            name=name,
            scryfall_id=f"id-{index}",
            synergy=synergy,
            inclusion_rate=inclusion_rate,
            category="Top Cards",
        )
        for index, (name, synergy, inclusion_rate) in enumerate(cards, start=1)
    ]
    return CommanderProfile(commander_name=commander_name, cards=edhrec_cards, num_decks=100)


def test_build_produces_99_cards() -> None:
    commander = _card("Azorius Commander", ["W", "U"], type_line="Legendary Creature")
    collection = _collection(_support_cards("Spell", 70, colors=["W"]) + _support_cards("Land", 10, type_line="Land"))
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert len(built.cards) == 99
    assert all(card.name != commander.name for card in built.cards)


def test_commander_excluded_from_deck() -> None:
    commander = _card("Korvold", ["B", "R", "G"], type_line="Legendary Creature")
    collection = _collection([_owned(commander)] + _support_cards("Support", 80, colors=["B"]))
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert all(card.name != "Korvold" for card in built.cards)


def test_color_identity_filtering() -> None:
    commander = _card("WU Commander", ["W", "U"], type_line="Legendary Creature")
    good = _card("Azorius Signet", ["W", "U"], type_line="Artifact")
    off_color = _card("Lightning Bolt", ["R"], type_line="Instant")
    collection = _collection([_owned(good), _owned(off_color)])
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)
    names = {card.name for card in built.cards}

    assert "Azorius Signet" in names
    assert "Lightning Bolt" not in names


def test_colorless_commander_only_colorless_and_wastes() -> None:
    commander = _card("Kozilek", [], type_line="Legendary Creature")
    colorless = _card("Mind Stone", [], type_line="Artifact")
    colored = _card("Swords to Plowshares", ["W"], type_line="Instant")
    collection = _collection([_owned(colorless), _owned(colored)])
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert "Swords to Plowshares" not in {card.name for card in built.cards}
    assert set(built.basics_added) == {"Wastes"}


def test_basic_lands_distributed_evenly() -> None:
    commander = _card("Esper Commander", ["W", "U", "B"], type_line="Legendary Creature")
    collection = _collection([])
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert built.basics_added == {"Plains": 33, "Island": 33, "Swamp": 33}


def test_basic_lands_remainder_follows_wubrg_order() -> None:
    commander = _card("Gruul Commander", ["R", "G"], type_line="Legendary Creature")
    collection = _collection(_support_cards("Spell", 92, colors=["R"]))
    builder = DeckBuilder(edhrec=None, template=DeckTemplate(target_lands=7))

    built = builder.build(commander, collection)

    assert built.basics_added == {"Mountain": 4, "Forest": 3}


def test_edhrec_scores_prioritize_high_synergy() -> None:
    commander = _card("Synergy Commander", ["W"], type_line="Legendary Creature")
    high = _card("High Synergy", ["W"], type_line="Instant")
    low = _card("Low Synergy", ["W"], type_line="Instant")
    fake = FakeEDHRecClient(
        profiles={
            "synergy commander": _profile(
                "Synergy Commander",
                [
                    ("High Synergy", 1.0, 1.0),
                    ("Low Synergy", -1.0, 0.0),
                ],
            )
        },
        avg_decks={"synergy commander": None},
    )
    collection = _collection([_owned(high), _owned(low)])
    builder = DeckBuilder(edhrec=fake, template=DeckTemplate(target_lands=98))

    built = builder.build(commander, collection)

    assert built.cards[0].name == "High Synergy"


def test_avg_deck_only_cards_score_above_unscored() -> None:
    commander = _card("Average Deck Commander", ["U"], type_line="Legendary Creature")
    avg_card = _card("Avg Deck Card", ["U"], type_line="Sorcery")
    unscored = _card("Unscored Card", ["U"], type_line="Sorcery")
    fake = FakeEDHRecClient(
        profiles={"average deck commander": None},
        avg_decks={"average deck commander": ["1 Avg Deck Card"]},
    )
    collection = _collection([_owned(avg_card), _owned(unscored)])
    builder = DeckBuilder(edhrec=fake, template=DeckTemplate(target_lands=98))

    built = builder.build(commander, collection)

    assert built.cards[0].name == "Avg Deck Card"
    assert built.scores["avg deck card"] == 0.25


def test_build_without_edhrec() -> None:
    commander = _card("No EDHREC Commander", ["G"], type_line="Legendary Creature")
    collection = _collection(_support_cards("Spell", 40, colors=["G"]))
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert len(built.cards) == 99
    assert built.edhrec_available is False


def test_edhrec_returns_none_gracefully() -> None:
    commander = _card("Unavailable Commander", ["G"], type_line="Legendary Creature")
    fake = FakeEDHRecClient(
        profiles={"unavailable commander": None},
        avg_decks={"unavailable commander": None},
    )
    collection = _collection(_support_cards("Spell", 15, colors=["G"]))
    builder = DeckBuilder(edhrec=fake)

    built = builder.build(commander, collection)

    assert len(built.cards) == 99
    assert built.edhrec_available is False


def test_duplicate_printings_deduplicated() -> None:
    commander = _card("Mono White", ["W"], type_line="Legendary Creature")
    print_a = _card("Swords to Plowshares", ["W"], type_line="Instant", scryfall_id="a")
    print_b = _card("Swords to Plowshares", ["W"], type_line="Instant", scryfall_id="b")
    collection = _collection([_owned(print_a), _owned(print_b)] + _support_cards("White Card", 70, colors=["W"]))
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert sum(1 for card in built.cards if card.name == "Swords to Plowshares") == 1


def test_basic_lands_excluded_from_candidates() -> None:
    commander = _card("White Commander", ["W"], type_line="Legendary Creature")
    collection = _collection(
        _support_cards("Spell", 99, colors=["W"])
        + [_owned(_card("Plains", ["W"], type_line="Basic Land")), _owned(_card("Forest", ["G"], type_line="Basic Land"))]
    )
    builder = DeckBuilder(edhrec=None, template=DeckTemplate(target_lands=0))

    built = builder.build(commander, collection)

    assert "Plains" not in {card.name for card in built.cards}
    assert "Forest" not in {card.name for card in built.cards}


def test_small_collection_fills_with_basics() -> None:
    commander = _card("Jund Commander", ["B", "R", "G"], type_line="Legendary Creature")
    collection = _collection(_support_cards("Spell", 20, colors=["B"]))
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    assert len(built.cards) == 99
    assert sum(built.basics_added.values()) == 79


def test_scores_dict_populated_for_all_cards() -> None:
    commander = _card("Score Commander", ["U"], type_line="Legendary Creature")
    cards = _support_cards("Spell", 10, colors=["U"])
    collection = _collection(cards)
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)

    for card in built.cards:
        assert card.name.casefold() in built.scores


def test_five_color_commander_accepts_all() -> None:
    commander = _card("Five Color", ["W", "U", "B", "R", "G"], type_line="Legendary Creature")
    pool = [
        _owned(_card("White Card", ["W"], type_line="Instant")),
        _owned(_card("Blue Card", ["U"], type_line="Instant")),
        _owned(_card("Black Card", ["B"], type_line="Instant")),
        _owned(_card("Red Card", ["R"], type_line="Instant")),
        _owned(_card("Green Card", ["G"], type_line="Instant")),
    ]
    collection = _collection(pool)
    builder = DeckBuilder(edhrec=None)

    built = builder.build(commander, collection)
    names = {card.name for card in built.cards}

    assert {"White Card", "Blue Card", "Black Card", "Red Card", "Green Card"}.issubset(names)


def test_nonbasic_lands_scored_by_edhrec() -> None:
    commander = _card("Land Commander", ["G"], type_line="Legendary Creature")
    boseiju = _card("Boseiju, Who Endures", ["G"], type_line="Legendary Land")
    tranquil = _card("Tranquil Thicket", ["G"], type_line="Land")
    fake = FakeEDHRecClient(
        profiles={
            "land commander": _profile(
                "Land Commander",
                [("Boseiju, Who Endures", 1.0, 1.0)],
            )
        },
        avg_decks={"land commander": None},
    )
    collection = _collection(
        _support_cards("Spell", 98, colors=["G"])
        + [_owned(boseiju), _owned(tranquil)]
    )
    builder = DeckBuilder(edhrec=fake, template=DeckTemplate(target_lands=1))

    built = builder.build(commander, collection)
    names = {card.name for card in built.cards}

    assert "Boseiju, Who Endures" in names
    assert "Tranquil Thicket" not in names

"""Data models for collection import and card resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Card:
    """Canonical card data resolved from Scryfall."""

    scryfall_id: str
    name: str
    mana_cost: str
    cmc: float
    color_identity: list[str]
    type_line: str
    oracle_text: str
    keywords: list[str]
    legalities: dict[str, str]
    set_code: str
    collector_number: str
    rarity: str
    card_faces: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class RawCardEntry:
    """Raw import row before Scryfall resolution."""

    name: str
    quantity: int
    scryfall_id: str | None
    set_code: str | None
    collector_number: str | None
    foil: bool
    source_row: int | None = None


@dataclass(slots=True)
class UnresolvedCard:
    """Card that failed resolution and needs manual review."""

    name: str
    reason: str
    source_row: int | None
    scryfall_id: str | None
    set_code: str | None


@dataclass(slots=True)
class OwnedCard:
    """A resolved card and quantity owned."""

    card: Card
    quantity: int


@dataclass(slots=True)
class Collection:
    """Resolved collection artifact passed to downstream slices."""

    cards: list[OwnedCard] = field(default_factory=list)
    unresolved: list[UnresolvedCard] = field(default_factory=list)
    import_date: str = ""

    @property
    def card_count(self) -> int:
        """Return total cards including quantities."""

        return sum(owned.quantity for owned in self.cards)

    @property
    def unique_count(self) -> int:
        """Return distinct resolved card count."""

        return len(self.cards)

    def by_color_identity(self, colors: list[str]) -> list[OwnedCard]:
        """Filter cards by exact color identity (order-insensitive)."""

        target = set(colors)
        return [
            owned
            for owned in self.cards
            if set(owned.card.color_identity) == target
        ]

    def find(self, name: str) -> OwnedCard | None:
        """Find a resolved card by case-insensitive name match."""

        normalized = name.strip().casefold()
        for owned in self.cards:
            if owned.card.name.strip().casefold() == normalized:
                return owned
        return None


@dataclass(slots=True)
class ComboInfo:
    """A combo detected by Commander Spellbook."""

    combo_id: str
    card_names: list[str]
    description: str
    bracket_tag: str
    bracket: int
    is_two_card: bool
    mana_needed: str


@dataclass(slots=True)
class BracketEstimate:
    """Bracket estimation result for a single deck.

    `bracket` is the estimated deck bracket in the range 1-5.
    """

    bracket: int
    confidence: str
    game_changer_cards: list[str]
    mld_cards: list[str]
    extra_turn_cards: list[str]
    combos: list[ComboInfo]
    reasons: list[str]
    spellbook_bracket_tag: str | None

"""Multi-deck card allocation (constraint satisfaction).

Resolves conflicts when multiple decks want the same card but the user
only owns limited copies. Optimizes for bracket balance across all 4 decks,
not just individual deck quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.collection.models import Card, Collection
from src.deckbuilder.builder import BASIC_LANDS, BuiltDeck

_COLOR_TO_BASIC: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
_WUBRG_ORDER: tuple[str, ...] = ("W", "U", "B", "R", "G")
_BASIC_NAMES: frozenset[str] = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"})


@dataclass(slots=True)
class _RequestedCard:
    deck_index: int
    card: Card
    score: float


class MultiDeckAllocator:
    """Allocate card copies across multiple built decks deterministically."""

    def allocate(self, collection: Collection, decks: list[BuiltDeck]) -> list[BuiltDeck]:
        if not decks:
            return []

        owned_limits, collection_cards_by_name = self._owned_nonbasic_limits(collection)
        allocatable_limits = self._apply_commander_reservations(owned_limits, decks)
        requests = self._collect_requests(decks)
        assigned_by_deck = self._resolve_requests(allocatable_limits, requests)
        remaining_limits = self._remaining_limits(allocatable_limits, assigned_by_deck)

        allocated: list[BuiltDeck] = []
        for deck_index, built_deck in enumerate(decks):
            selected_cards = assigned_by_deck.get(deck_index, []).copy()
            selected_keys = {self._name_key(card.name) for card in selected_cards}
            selected_cards = self._fill_with_collection_cards(
                commander=built_deck.commander,
                selected_cards=selected_cards,
                selected_keys=selected_keys,
                source_scores=built_deck.scores,
                collection_cards_by_name=collection_cards_by_name,
                remaining_limits=remaining_limits,
            )

            basics_needed = max(99 - len(selected_cards), 0)
            basics, basics_added = self._generate_basics(
                basics_needed,
                self._normalize_colors(built_deck.commander.color_identity),
            )
            selected_cards.extend(basics)
            final_cards = selected_cards[:99]

            final_scores: dict[str, float] = {}
            for card in final_cards:
                key = self._name_key(card.name)
                final_scores[key] = built_deck.scores.get(key, 0.0)

            allocated.append(
                BuiltDeck(
                    commander=built_deck.commander,
                    cards=final_cards,
                    scores=final_scores,
                    basics_added=basics_added,
                    edhrec_available=built_deck.edhrec_available,
                )
            )
        return allocated

    def _apply_commander_reservations(
        self,
        owned_limits: dict[str, int],
        decks: list[BuiltDeck],
    ) -> dict[str, int]:
        """Reserve one non-basic copy per selected commander before allocation."""

        remaining = dict(owned_limits)
        reservations: dict[str, int] = {}

        for built_deck in decks:
            commander = built_deck.commander
            if self._is_basic(commander):
                continue
            key = self._name_key(commander.name)
            reservations[key] = reservations.get(key, 0) + 1

        for name_key, reserved in reservations.items():
            owned = remaining.get(name_key, 0)
            remaining[name_key] = max(owned - reserved, 0)

        return remaining

    def _collect_requests(self, decks: list[BuiltDeck]) -> dict[str, list[_RequestedCard]]:
        requests: dict[str, list[_RequestedCard]] = {}
        for deck_index, built_deck in enumerate(decks):
            commander_key = self._name_key(built_deck.commander.name)
            seen_names: set[str] = set()
            for card in built_deck.cards:
                name_key = self._name_key(card.name)
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                if name_key == commander_key:
                    continue
                if self._is_basic(card):
                    continue
                if not self._is_legal_for_commander(card, built_deck.commander):
                    continue
                requests.setdefault(name_key, []).append(
                    _RequestedCard(
                        deck_index=deck_index,
                        card=card,
                        score=built_deck.scores.get(name_key, 0.0),
                    )
                )
        return requests

    def _resolve_requests(
        self,
        owned_limits: dict[str, int],
        requests: dict[str, list[_RequestedCard]],
    ) -> dict[int, list[Card]]:
        assigned: dict[int, list[Card]] = {}
        for name_key in sorted(requests):
            contenders = requests[name_key]
            limit = owned_limits.get(name_key, 0)
            contenders.sort(
                key=lambda contender: (
                    -contender.score,
                    contender.deck_index,
                    contender.card.name.casefold(),
                )
            )
            for contender in contenders[:limit]:
                assigned.setdefault(contender.deck_index, []).append(contender.card)
        for deck_index in assigned:
            assigned[deck_index].sort(key=lambda card: card.name.casefold())
        return assigned

    @staticmethod
    def _remaining_limits(
        owned_limits: dict[str, int],
        assigned_by_deck: dict[int, list[Card]],
    ) -> dict[str, int]:
        remaining = dict(owned_limits)
        for cards in assigned_by_deck.values():
            for card in cards:
                key = card.name.strip().casefold()
                if key in remaining:
                    remaining[key] = max(remaining[key] - 1, 0)
        return remaining

    def _fill_with_collection_cards(
        self,
        commander: Card,
        selected_cards: list[Card],
        selected_keys: set[str],
        source_scores: dict[str, float],
        collection_cards_by_name: dict[str, Card],
        remaining_limits: dict[str, int],
    ) -> list[Card]:
        ranked_candidates = sorted(
            collection_cards_by_name.items(),
            key=lambda item: (
                -source_scores.get(item[0], 0.0),
                item[1].name.casefold(),
            ),
        )
        commander_key = self._name_key(commander.name)
        for name_key, card in ranked_candidates:
            if len(selected_cards) >= 99:
                break
            if name_key == commander_key:
                continue
            if name_key in selected_keys:
                continue
            if remaining_limits.get(name_key, 0) <= 0:
                continue
            if not self._is_legal_for_commander(card, commander):
                continue
            selected_cards.append(card)
            selected_keys.add(name_key)
            remaining_limits[name_key] = remaining_limits.get(name_key, 0) - 1
        return selected_cards

    def _owned_nonbasic_limits(
        self,
        collection: Collection,
    ) -> tuple[dict[str, int], dict[str, Card]]:
        limits: dict[str, int] = {}
        cards_by_name: dict[str, Card] = {}
        sorted_owned = sorted(
            collection.cards,
            key=lambda owned: (
                owned.card.name.casefold(),
                owned.card.scryfall_id.casefold(),
            ),
        )
        for owned in sorted_owned:
            if owned.quantity <= 0:
                continue
            card = owned.card
            if self._is_basic(card):
                continue
            name_key = self._name_key(card.name)
            limits[name_key] = limits.get(name_key, 0) + owned.quantity
            cards_by_name.setdefault(name_key, card)
        return limits, cards_by_name

    @staticmethod
    def _name_key(name: str) -> str:
        return name.strip().casefold()

    @staticmethod
    def _is_basic(card: Card) -> bool:
        return card.name.strip() in _BASIC_NAMES

    @staticmethod
    def _normalize_colors(colors: list[str]) -> frozenset[str]:
        normalized = {
            str(color).strip().upper()
            for color in colors
            if str(color).strip().upper() in _COLOR_TO_BASIC
        }
        return frozenset(normalized)

    def _is_legal_for_commander(self, card: Card, commander: Card) -> bool:
        commander_colors = self._normalize_colors(commander.color_identity)
        card_colors = self._normalize_colors(card.color_identity)
        return card_colors.issubset(commander_colors)

    @staticmethod
    def _generate_basics(
        count: int,
        colors: frozenset[str],
    ) -> tuple[list[Card], dict[str, int]]:
        if count <= 0:
            return [], {}
        if not colors:
            return [BASIC_LANDS["Wastes"] for _ in range(count)], {"Wastes": count}

        basic_cycle = [
            _COLOR_TO_BASIC[color]
            for color in _WUBRG_ORDER
            if color in colors
        ]
        if not basic_cycle:
            return [BASIC_LANDS["Wastes"] for _ in range(count)], {"Wastes": count}

        per_basic = count // len(basic_cycle)
        remainder = count % len(basic_cycle)
        basics_added: dict[str, int] = {name: per_basic for name in basic_cycle}
        for index in range(remainder):
            basics_added[basic_cycle[index]] += 1

        basics: list[Card] = []
        for name in basic_cycle:
            basics.extend([BASIC_LANDS[name]] * basics_added[name])
        return basics, basics_added

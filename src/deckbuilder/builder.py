"""Core single-deck builder for a commander and owned collection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.collection.models import Card, Collection
from src.commanders.edhrec_client import CommanderProfile, EDHRecClient
from src.deckbuilder.templates import DEFAULT_TEMPLATE, DeckTemplate

_COLOR_TO_BASIC: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
_BASIC_LAND_NAMES: frozenset[str] = frozenset({*_COLOR_TO_BASIC.values(), "Wastes"})
_WUBRG_ORDER: tuple[str, ...] = ("W", "U", "B", "R", "G")


@dataclass(slots=True)
class BuiltDeck:
    """Built 100-card deck artifact (commander + 99 non-commander cards)."""

    commander: Card
    cards: list[Card]
    scores: dict[str, float]
    basics_added: dict[str, int]
    edhrec_available: bool


@dataclass(slots=True)
class _ScoringIndex:
    scores: dict[str, float]
    avg_deck_names: frozenset[str]
    available: bool


def _normalize_colors(colors: list[str]) -> frozenset[str]:
    normalized = {
        str(color).strip().upper()
        for color in colors
        if str(color).strip().upper() in _COLOR_TO_BASIC
    }
    return frozenset(normalized)


def _is_basic_land(card: Card) -> bool:
    return card.name.strip() in _BASIC_LAND_NAMES


def _is_land(card: Card) -> bool:
    return "land" in card.type_line.casefold()


def _make_basic_land(name: str) -> Card:
    color_identity: list[str] = []
    for color, basic_name in _COLOR_TO_BASIC.items():
        if basic_name == name:
            color_identity = [color]
            break

    return Card(
        scryfall_id=f"basic-{name.casefold()}",
        name=name,
        mana_cost="",
        cmc=0.0,
        color_identity=color_identity,
        type_line="Basic Land",
        oracle_text="",
        keywords=[],
        legalities={"commander": "legal"},
        set_code="",
        collector_number="",
        rarity="basic",
    )


BASIC_LANDS: dict[str, Card] = {
    name: _make_basic_land(name)
    for name in sorted(_BASIC_LAND_NAMES, key=str.casefold)
}


def _parse_avg_deck_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None

    match = re.match(r"^\d+\s*[xX]?\s+(.+)$", stripped)
    if match:
        name = match.group(1).strip()
        return name or None
    return stripped


def _clamp_0_1(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _build_scoring_index(
    profile: CommanderProfile | None,
    avg_deck: list[str] | None,
) -> _ScoringIndex:
    scores: dict[str, float] = {}
    avg_deck_names: set[str] = set()
    available = profile is not None or avg_deck is not None

    if profile is not None:
        for edhrec_card in profile.cards:
            normalized_name = edhrec_card.name.strip().casefold()
            if not normalized_name:
                continue

            normalized_synergy = _clamp_0_1((edhrec_card.synergy + 1.0) / 2.0)
            inclusion_rate = _clamp_0_1(edhrec_card.inclusion_rate)
            score = (0.5 * normalized_synergy) + (0.5 * inclusion_rate)
            current = scores.get(normalized_name, 0.0)
            scores[normalized_name] = max(current, score)

    if avg_deck is not None:
        for line in avg_deck:
            parsed_name = _parse_avg_deck_line(line)
            if parsed_name is None:
                continue
            normalized_name = parsed_name.casefold()
            avg_deck_names.add(normalized_name)
            if normalized_name not in scores:
                scores[normalized_name] = 0.25

    return _ScoringIndex(
        scores=scores,
        avg_deck_names=frozenset(avg_deck_names),
        available=available,
    )


class DeckBuilder:
    """Build a 99-card deck from owned cards for a commander."""

    def __init__(
        self,
        edhrec: EDHRecClient | None = None,
        template: DeckTemplate = DEFAULT_TEMPLATE,
    ) -> None:
        self.edhrec = edhrec
        self.template = template

    def build(self, commander: Card, collection: Collection) -> BuiltDeck:
        scoring = self._fetch_edhrec_data(commander)
        candidates = self._filter_candidates(commander=commander, collection=collection)
        nonlands, nonbasic_lands = self._partition(candidates)

        ranked_nonlands = self._score_and_sort(nonlands, scoring)
        ranked_nonbasic_lands = self._score_and_sort(nonbasic_lands, scoring)

        target_lands = min(max(self.template.target_lands, 0), 99)
        nonland_target = 99 - target_lands

        selected_nonlands = [card for card, _ in ranked_nonlands[:nonland_target]]
        land_slots = 99 - len(selected_nonlands)

        selected_nonbasic_lands = [
            card for card, _ in ranked_nonbasic_lands[:land_slots]
        ]
        remaining_basics = land_slots - len(selected_nonbasic_lands)

        basics, basics_added = self._generate_basics(
            count=remaining_basics,
            colors=_normalize_colors(commander.color_identity),
        )

        cards = selected_nonlands + selected_nonbasic_lands + basics

        scores: dict[str, float] = {}
        for card in cards:
            key = card.name.strip().casefold()
            scores[key] = scoring.scores.get(key, 0.0)

        return BuiltDeck(
            commander=commander,
            cards=cards,
            scores=scores,
            basics_added=basics_added,
            edhrec_available=scoring.available,
        )

    def _fetch_edhrec_data(self, commander: Card) -> _ScoringIndex:
        if self.edhrec is None:
            return _ScoringIndex(scores={}, avg_deck_names=frozenset(), available=False)

        profile = self.edhrec.get_commander_profile(commander.name)
        avg_deck = self.edhrec.get_average_deck(commander.name)
        return _build_scoring_index(profile=profile, avg_deck=avg_deck)

    @staticmethod
    def _filter_candidates(commander: Card, collection: Collection) -> list[Card]:
        commander_colors = _normalize_colors(commander.color_identity)
        commander_name = commander.name.strip().casefold()
        commander_id = commander.scryfall_id.strip()

        seen_names: set[str] = set()
        candidates: list[Card] = []
        for owned in collection.cards:
            if owned.quantity <= 0:
                continue

            card = owned.card
            normalized_name = card.name.strip().casefold()
            if not normalized_name:
                continue
            if normalized_name == commander_name:
                continue
            if commander_id and card.scryfall_id.strip() == commander_id:
                continue
            if normalized_name in seen_names:
                continue
            if _is_basic_land(card):
                continue

            card_colors = _normalize_colors(card.color_identity)
            if not card_colors.issubset(commander_colors):
                continue

            candidates.append(card)
            seen_names.add(normalized_name)

        return candidates

    @staticmethod
    def _partition(candidates: list[Card]) -> tuple[list[Card], list[Card]]:
        nonlands: list[Card] = []
        nonbasic_lands: list[Card] = []
        for card in candidates:
            if _is_land(card):
                nonbasic_lands.append(card)
            else:
                nonlands.append(card)
        return nonlands, nonbasic_lands

    @staticmethod
    def _score_and_sort(
        cards: list[Card],
        scoring: _ScoringIndex,
    ) -> list[tuple[Card, float]]:
        ranked = [
            (card, scoring.scores.get(card.name.strip().casefold(), 0.0))
            for card in cards
        ]
        ranked.sort(key=lambda item: (-item[1], item[0].name.casefold()))
        return ranked

    @staticmethod
    def _generate_basics(
        count: int,
        colors: frozenset[str],
    ) -> tuple[list[Card], dict[str, int]]:
        if count <= 0:
            return [], {}

        if not colors:
            wastes_added = {"Wastes": count}
            return [BASIC_LANDS["Wastes"] for _ in range(count)], wastes_added

        basic_cycle = [
            _COLOR_TO_BASIC[color]
            for color in _WUBRG_ORDER
            if color in colors
        ]
        if not basic_cycle:
            wastes_added = {"Wastes": count}
            return [BASIC_LANDS["Wastes"] for _ in range(count)], wastes_added

        per_basic = count // len(basic_cycle)
        remainder = count % len(basic_cycle)

        basics_added: dict[str, int] = {name: per_basic for name in basic_cycle}
        for index in range(remainder):
            basic_name = basic_cycle[index]
            basics_added[basic_name] += 1

        basics: list[Card] = []
        for basic_name in basic_cycle:
            basics.extend([BASIC_LANDS[basic_name]] * basics_added[basic_name])

        return basics, basics_added

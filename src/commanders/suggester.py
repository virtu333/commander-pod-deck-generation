"""Commander suggestion engine.

Suggestions are ranked by three signals:
- color diversity across selected commanders,
- EDHREC profile overlap with owned cards,
- buildability from the collection (in-color card depth).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.collection.models import Card, Collection
from src.commanders.edhrec_client import CommanderProfile, EDHRecClient

ALL_COLORS: frozenset[str] = frozenset({"W", "U", "B", "R", "G"})


@dataclass(slots=True)
class CommanderCandidate:
    """A scored commander suggestion."""

    card: Card
    score: float
    collection_overlap: float
    color_diversity_score: float
    buildable_count: int
    buildable_score: float
    synergy_cards_in_collection: int
    total_synergy_cards: int


@dataclass(slots=True)
class _CandidateMetrics:
    card: Card
    buildable_count: int
    buildable_score: float
    collection_overlap: float = 0.0
    synergy_cards_in_collection: int = 0
    total_synergy_cards: int = 0


def _is_legal_commander(card: Card) -> bool:
    legality = card.legalities.get("commander", "").casefold()
    if legality != "legal":
        return False

    type_line = card.type_line.casefold()
    if "legendary" in type_line and "creature" in type_line:
        return True

    oracle_text = card.oracle_text.casefold()
    return "can be your commander" in oracle_text


def _color_diversity(candidate_colors: list[str], covered: set[str]) -> float:
    normalized_candidate_colors = {
        str(color).strip().upper()
        for color in candidate_colors
        if str(color).strip().upper() in ALL_COLORS
    }
    uncovered = ALL_COLORS - covered
    if not uncovered:
        return 0.0
    new_colors = normalized_candidate_colors - covered
    return len(new_colors) / len(uncovered)


class CommanderSuggester:
    """Suggest commanders from a resolved collection."""

    _DIVERSITY_WEIGHT: float = 0.35
    _OVERLAP_WEIGHT: float = 0.45
    _BUILDABILITY_WEIGHT: float = 0.20

    def __init__(self, edhrec: EDHRecClient) -> None:
        self.edhrec = edhrec

    def find_commanders_in_collection(self, collection: Collection) -> list[Card]:
        """Return legal commander cards from the collection (deduplicated)."""

        deduped: dict[str, Card] = {}
        for owned_card in collection.cards:
            if owned_card.quantity <= 0:
                continue
            card = owned_card.card
            if not _is_legal_commander(card):
                continue
            deduped[self._commander_key(card)] = card
        return sorted(deduped.values(), key=lambda card: card.name.casefold())

    def suggest(
        self,
        collection: Collection,
        selected: list[Card] | None = None,
        count: int = 4,
        max_edhrec_lookups: int = 15,
        min_buildable_cards: int = 40,
    ) -> list[CommanderCandidate]:
        """Suggest commanders to reach ``count`` total commanders."""

        selected_cards = selected or []
        selected_keys = {self._commander_key(card) for card in selected_cards}
        desired_total = max(count, 0)
        needed = max(desired_total - len(selected_keys), 0)
        if needed <= 0:
            return []

        selected_names = {
            selected_card.name.strip().casefold()
            for selected_card in selected_cards
        }
        pool = [
            card
            for card in self.find_commanders_in_collection(collection)
            if self._commander_key(card) not in selected_keys
            and card.name.strip().casefold() not in selected_names
        ]
        if not pool:
            return []

        collection_names = {
            owned.card.name.strip().casefold()
            for owned in collection.cards
            if owned.quantity > 0 and owned.card.name.strip()
        }
        covered_colors = self._covered_colors(selected_cards)
        metrics = self._initial_metrics(
            candidates=pool,
            collection=collection,
            covered_colors=covered_colors,
        )
        self._hydrate_overlap_metrics(
            metrics=metrics,
            collection_names=collection_names,
            covered_colors=covered_colors,
            max_edhrec_lookups=max_edhrec_lookups,
        )

        suggestions: list[CommanderCandidate] = []
        remaining = metrics[:]
        while remaining and len(suggestions) < needed:
            eligible = [
                metric for metric in remaining if metric.buildable_count >= min_buildable_cards
            ]
            scoring_pool = eligible if eligible else remaining

            scored = []
            for metric in scoring_pool:
                diversity = _color_diversity(metric.card.color_identity, covered_colors)
                composite = self._composite_score(metric, diversity)
                scored.append((metric, diversity, composite))

            scored.sort(
                key=lambda item: (
                    -item[2],  # composite score
                    -item[1],  # diversity score
                    -item[0].collection_overlap,
                    -item[0].buildable_score,
                    -item[0].buildable_count,
                    item[0].card.name.casefold(),
                )
            )
            chosen_metric, diversity, composite = scored[0]
            suggestions.append(
                CommanderCandidate(
                    card=chosen_metric.card,
                    score=composite,
                    collection_overlap=chosen_metric.collection_overlap,
                    color_diversity_score=diversity,
                    buildable_count=chosen_metric.buildable_count,
                    buildable_score=chosen_metric.buildable_score,
                    synergy_cards_in_collection=chosen_metric.synergy_cards_in_collection,
                    total_synergy_cards=chosen_metric.total_synergy_cards,
                )
            )
            covered_colors.update(self._normalize_colors(chosen_metric.card.color_identity))
            remaining = [
                metric
                for metric in remaining
                if self._commander_key(metric.card) != self._commander_key(chosen_metric.card)
            ]

        return suggestions

    def _initial_metrics(
        self,
        candidates: list[Card],
        collection: Collection,
        covered_colors: set[str],
    ) -> list[_CandidateMetrics]:
        metrics: list[_CandidateMetrics] = []
        for candidate in candidates:
            buildable_count = self._buildable_count(candidate, collection)
            metrics.append(
                _CandidateMetrics(
                    card=candidate,
                    buildable_count=buildable_count,
                    buildable_score=min(buildable_count / 99.0, 1.0),
                )
            )
        metrics.sort(
            key=lambda metric: (
                -metric.buildable_count,
                -_color_diversity(metric.card.color_identity, covered_colors),
                metric.card.name.casefold(),
            )
        )
        return metrics

    def _hydrate_overlap_metrics(
        self,
        metrics: list[_CandidateMetrics],
        collection_names: set[str],
        covered_colors: set[str],
        max_edhrec_lookups: int,
    ) -> None:
        lookup_limit = max(max_edhrec_lookups, 0)
        for metric in metrics[:lookup_limit]:
            profile = self.edhrec.get_commander_profile(metric.card.name)
            overlap, in_collection, total = self._overlap_from_profile(profile, collection_names)
            metric.collection_overlap = overlap
            metric.synergy_cards_in_collection = in_collection
            metric.total_synergy_cards = total

        # Deterministic ordering before greedy picks.
        metrics.sort(
            key=lambda metric: (
                -metric.buildable_count,
                -metric.collection_overlap,
                -_color_diversity(metric.card.color_identity, covered_colors),
                metric.card.name.casefold(),
            )
        )

    @staticmethod
    def _overlap_from_profile(
        profile: CommanderProfile | None,
        collection_names: set[str],
    ) -> tuple[float, int, int]:
        if profile is None or not profile.cards:
            return 0.0, 0, 0

        unique_synergy_names: set[str] = set()
        for profile_card in profile.cards:
            normalized_name = profile_card.name.strip().casefold()
            if normalized_name:
                unique_synergy_names.add(normalized_name)

        total = len(unique_synergy_names)
        if total == 0:
            return 0.0, 0, 0

        in_collection = sum(1 for card_name in unique_synergy_names if card_name in collection_names)
        return in_collection / total, in_collection, total

    def _buildable_count(self, commander: Card, collection: Collection) -> int:
        commander_colors = self._normalize_colors(commander.color_identity)
        commander_key = self._card_identity_key(commander)
        commander_name = commander.name.strip().casefold()

        buildable = 0
        seen_names: set[str] = set()
        for owned_card in collection.cards:
            if owned_card.quantity <= 0:
                continue
            card = owned_card.card
            card_name = card.name.strip().casefold()
            if not card_name:
                continue
            if self._card_identity_key(card) == commander_key or card_name == commander_name:
                continue
            if card_name in seen_names:
                continue
            card_colors = self._normalize_colors(card.color_identity)
            if card_colors.issubset(commander_colors):
                buildable += 1
                seen_names.add(card_name)
        return buildable

    def _covered_colors(self, selected_cards: list[Card]) -> set[str]:
        covered: set[str] = set()
        for selected_card in selected_cards:
            covered.update(self._normalize_colors(selected_card.color_identity))
        return covered

    def _composite_score(self, metric: _CandidateMetrics, diversity: float) -> float:
        return (
            self._DIVERSITY_WEIGHT * diversity
            + self._OVERLAP_WEIGHT * metric.collection_overlap
            + self._BUILDABILITY_WEIGHT * metric.buildable_score
        )

    @staticmethod
    def _normalize_colors(colors: list[str]) -> set[str]:
        normalized: set[str] = set()
        for color in colors:
            color_code = str(color).strip().upper()
            if color_code in ALL_COLORS:
                normalized.add(color_code)
        return normalized

    @staticmethod
    def _card_identity_key(card: Card) -> str:
        scryfall_id = card.scryfall_id.strip()
        if scryfall_id:
            return scryfall_id
        return card.name.strip().casefold()

    @staticmethod
    def _commander_key(card: Card) -> str:
        return card.name.strip().casefold()

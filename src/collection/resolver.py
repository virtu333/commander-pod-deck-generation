"""Resolve raw imported cards into canonical collection objects.

Summary output is quantity-based (`resolved_quantity / total_quantity`), while
`Collection.unresolved` remains entry-based for manual remediation workflows.
"""

from __future__ import annotations

import logging
from datetime import date

from rich.console import Console

from src.collection.models import Card, Collection, OwnedCard, RawCardEntry, UnresolvedCard
from src.utils.scryfall import ScryfallClient, ScryfallError

LOGGER = logging.getLogger(__name__)


class CardResolver:
    """Resolve entries using local cache/Oracle first, then live Scryfall fallback."""

    def __init__(self, scryfall: ScryfallClient, console: Console | None = None) -> None:
        self.scryfall = scryfall
        self.console = console or Console()

    def resolve(self, entries: list[RawCardEntry]) -> Collection:
        """Resolve all raw entries into a Collection with unresolved artifacts."""

        resolved: dict[str, OwnedCard] = {}
        unresolved: list[UnresolvedCard] = []
        total_quantity = sum(max(entry.quantity, 0) for entry in entries)
        resolved_quantity = 0

        for entry in entries:
            if entry.quantity <= 0:
                continue

            try:
                card = self._resolve_entry(entry)
            except ScryfallError as exc:
                LOGGER.warning("Scryfall error resolving '%s': %s", entry.name, exc)
                unresolved.append(
                    UnresolvedCard(
                        name=entry.name,
                        reason="api_error",
                        source_row=entry.source_row,
                        scryfall_id=entry.scryfall_id,
                        set_code=entry.set_code,
                    )
                )
                continue

            if card is None:
                unresolved.append(
                    UnresolvedCard(
                        name=entry.name,
                        reason="not_found",
                        source_row=entry.source_row,
                        scryfall_id=entry.scryfall_id,
                        set_code=entry.set_code,
                    )
                )
                LOGGER.warning("Unable to resolve card '%s' (row %s)", entry.name, entry.source_row)
                continue

            existing = resolved.get(card.scryfall_id)
            if existing is None:
                resolved[card.scryfall_id] = OwnedCard(card=card, quantity=entry.quantity)
            else:
                existing.quantity += entry.quantity
            resolved_quantity += entry.quantity

        # Quantity-based summary for user visibility; unresolved artifacts are entry-based.
        unresolved_quantity = max(total_quantity - resolved_quantity, 0)
        collection = Collection(
            cards=list(resolved.values()),
            unresolved=unresolved,
            import_date=date.today().isoformat(),
        )
        self.console.print(
            "Resolved "
            f"{resolved_quantity}/{total_quantity} cards "
            f"({unresolved_quantity} unresolved - see collection.unresolved)"
        )
        return collection

    def _resolve_entry(self, entry: RawCardEntry) -> Card | None:
        if entry.scryfall_id:
            card = self.scryfall.get_card_cached(entry.scryfall_id)
            if card is not None:
                return card

        card = self.scryfall.get_card_by_name(
            entry.name,
            set_code=entry.set_code,
            collector_number=entry.collector_number,
        )
        if card is not None:
            return card

        if entry.scryfall_id:
            # Live ID fallback only when local cache and Oracle lookup both miss.
            card = self.scryfall.get_card(entry.scryfall_id)
            if card is not None:
                return card

        return self._resolve_by_name(entry)

    def _resolve_by_name(self, entry: RawCardEntry) -> Card | None:
        escaped_name = entry.name.replace('"', '\\"')
        results = self.scryfall.search(f'!"{escaped_name}"')
        if not results:
            return None
        if len(results) == 1:
            return results[0]

        candidates = results

        if entry.set_code:
            target_set = entry.set_code.casefold()
            set_matches = [
                card for card in candidates if card.set_code.casefold() == target_set
            ]
            if set_matches:
                candidates = set_matches

        if entry.collector_number:
            target_collector = entry.collector_number.strip().casefold()
            collector_matches = [
                card
                for card in candidates
                if card.collector_number.strip().casefold() == target_collector
            ]
            if collector_matches:
                candidates = collector_matches

        return candidates[0]

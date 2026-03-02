"""Game Changer detection with graceful degradation on API failures."""

from __future__ import annotations

import logging

from src.collection.models import Card
from src.utils.scryfall import ScryfallClient, ScryfallError

LOGGER = logging.getLogger(__name__)


class GameChangerDetector:
    """Detect Game Changer cards in a card list."""

    def __init__(self, scryfall: ScryfallClient) -> None:
        self.scryfall = scryfall
        self._game_changer_names: set[str] | None = None

    def detect(self, cards: list[Card]) -> tuple[list[Card], bool]:
        """Return `(matched_cards, success)`; success=False when list fetch fails."""

        if self._game_changer_names is None:
            try:
                game_changers = self.scryfall.get_game_changers()
            except ScryfallError as exc:
                LOGGER.warning("Unable to load Game Changers: %s", exc)
                return [], False
            self._game_changer_names = {
                card.name.strip().casefold() for card in game_changers
            }

        matched = [
            card
            for card in cards
            if card.name.strip().casefold() in self._game_changer_names
        ]
        return matched, True

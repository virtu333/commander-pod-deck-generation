"""Scryfall API client with rate limiting and caching.

Handles individual card lookups by Scryfall ID and search queries
(e.g., is:gamechanger). Respects 50-100ms rate limits.
Caches responses in SQLite.

Bulk data download (for commander suggestion) added in Slice 3.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src.collection.models import Card
from src.utils.cache import CardCache

LOGGER = logging.getLogger(__name__)


class ScryfallError(Exception):
    """Raised for Scryfall API/network failures."""


class ScryfallClient:
    """HTTP client for Scryfall with local SQLite-backed caching."""

    BASE_URL = "https://api.scryfall.com"

    def __init__(
        self,
        cache: CardCache,
        min_request_gap_seconds: float = 0.1,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.cache = cache
        self.min_request_gap_seconds = min_request_gap_seconds
        self.timeout_seconds = timeout_seconds
        self._last_request_time: float | None = None

    def get_card(self, scryfall_id: str) -> Card | None:
        """Fetch a card by Scryfall ID, returning None when not found."""

        cached = self.cache.get_card(scryfall_id)
        if cached is not None:
            return self._parse_card(cached)

        data = self._request(f"{self.BASE_URL}/cards/{scryfall_id}")
        if data is None:
            return None

        self.cache.put_card(scryfall_id, data)
        return self._parse_card(data)

    def search(self, query: str) -> list[Card]:
        """Run a Scryfall search query, handling pagination."""

        cards: list[Card] = []
        url = f"{self.BASE_URL}/cards/search"
        params: dict[str, Any] | None = {"q": query}

        while url:
            payload = self._request(url, params=params)
            if payload is None:
                return []

            for card_data in payload.get("data", []):
                if not isinstance(card_data, dict):
                    continue
                card_id = card_data.get("id")
                if isinstance(card_id, str) and card_id:
                    self.cache.put_card(card_id, card_data)
                cards.append(self._parse_card(card_data))

            if payload.get("has_more"):
                next_page = payload.get("next_page")
                if not isinstance(next_page, str) or not next_page:
                    break
                url = next_page
                params = None
            else:
                break

        return cards

    def get_game_changers(self) -> list[Card]:
        """Return the current Game Changers list (cached for 7 days)."""

        cache_key = "game_changers"
        cached_ids = self.cache.get(cache_key)
        if isinstance(cached_ids, list):
            card_ids = [str(card_id) for card_id in cached_ids]
            raw_cards = self.cache.get_cards_bulk(card_ids)
            resolved_by_index: list[Card | None] = [None] * len(card_ids)
            missing_ids: list[str] = []

            for index, card_id in enumerate(card_ids):
                cached_raw = raw_cards.get(card_id)
                if cached_raw is None:
                    missing_ids.append(card_id)
                    continue
                resolved_by_index[index] = self._parse_card(cached_raw)

            fetched_missing: dict[str, Card | None] = {}
            for card_id in dict.fromkeys(missing_ids):
                fetched_missing[card_id] = self.get_card(card_id)

            for index, card_id in enumerate(card_ids):
                if resolved_by_index[index] is not None:
                    continue
                resolved_by_index[index] = fetched_missing.get(card_id)

            return [card for card in resolved_by_index if card is not None]

        cards = self.search("is:gamechanger")
        self.cache.put(cache_key, [card.scryfall_id for card in cards], ttl_hours=24 * 7)
        return cards

    def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Issue a GET request with 100ms rate limiting and 429 retry handling."""

        max_rate_limit_retries = 3
        attempts = 0
        while True:
            self._respect_rate_limit()
            try:
                response = requests.get(url, params=params, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                raise ScryfallError(f"Scryfall request failed: {exc}") from exc

            if response.status_code == 404:
                return None

            if response.status_code == 429:
                attempts += 1
                if attempts > max_rate_limit_retries:
                    raise ScryfallError("Scryfall rate limit retries exceeded")
                retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
                LOGGER.warning("Scryfall rate limit hit; retrying in %ss", retry_after)
                time.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                raise ScryfallError(
                    f"Scryfall server error: HTTP {response.status_code}"
                )

            if not response.ok:
                raise ScryfallError(
                    f"Scryfall request failed: HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise ScryfallError("Invalid JSON returned by Scryfall") from exc

            if not isinstance(payload, dict):
                raise ScryfallError("Unexpected Scryfall response format")

            return payload

    def _respect_rate_limit(self) -> None:
        now = time.monotonic()
        if self._last_request_time is not None:
            elapsed = now - self._last_request_time
            wait_for = self.min_request_gap_seconds - elapsed
            if wait_for > 0:
                time.sleep(wait_for)
        self._last_request_time = time.monotonic()

    @staticmethod
    def _retry_after_seconds(header_value: str | None) -> float:
        if header_value is None:
            return 1.0
        try:
            value = float(header_value)
        except ValueError:
            return 1.0
        return max(value, 0.1)

    @staticmethod
    def _parse_card(data: dict[str, Any]) -> Card:
        card_faces = [
            face for face in data.get("card_faces", []) if isinstance(face, dict)
        ]
        first_face: dict[str, Any] = card_faces[0] if card_faces else {}
        layout = str(data.get("layout", ""))
        is_dfc_layout = layout in {"transform", "modal_dfc", "double_faced_token"}
        oracle_text = ScryfallClient._oracle_text(data, card_faces)
        if is_dfc_layout and first_face.get("name"):
            name = str(first_face.get("name", ""))
        else:
            name = str(data.get("name") or first_face.get("name", ""))

        return Card(
            scryfall_id=str(data.get("id", "")),
            name=name,
            mana_cost=str(data.get("mana_cost") or first_face.get("mana_cost", "")),
            cmc=float(data.get("cmc", 0.0) or 0.0),
            color_identity=[
                str(color) for color in data.get("color_identity", []) if color
            ],
            type_line=str(data.get("type_line") or first_face.get("type_line", "")),
            oracle_text=oracle_text,
            keywords=[str(keyword) for keyword in data.get("keywords", []) if keyword],
            legalities={
                str(fmt): str(status)
                for fmt, status in data.get("legalities", {}).items()
            },
            set_code=str(data.get("set", "")),
            collector_number=str(data.get("collector_number", "")),
            rarity=str(data.get("rarity", "")),
            card_faces=card_faces,
        )

    @staticmethod
    def _oracle_text(data: dict[str, Any], card_faces: list[dict[str, Any]]) -> str:
        if card_faces:
            face_texts = [
                str(face.get("oracle_text", "")).strip()
                for face in card_faces
                if str(face.get("oracle_text", "")).strip()
            ]
            if face_texts:
                return "\n\n".join(face_texts)
        return str(data.get("oracle_text", ""))

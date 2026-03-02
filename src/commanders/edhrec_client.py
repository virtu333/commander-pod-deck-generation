"""EDHREC data client with local caching and defensive parsing.

The EDHREC JSON shape is unofficial and can drift. This client tolerates
missing/extra fields, stores only successfully parsed payloads in cache, and
falls back gracefully by returning ``None`` on external failures.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from pyedhrec import EDHRec

from src.utils.cache import CardCache

LOGGER = logging.getLogger(__name__)

_KNOWN_SYNERGY_TAGS: frozenset[str] = frozenset(
    {
        "newcards",
        "highsynergycards",
        "topcards",
        "gamechangers",
        "creatures",
        "instants",
        "sorceries",
        "utilityartifacts",
        "enchantments",
        "planeswalkers",
        "utilitylands",
        "manaartifacts",
    }
)
_EXCLUDED_CARDLIST_TAGS: frozenset[str] = frozenset({"lands"})


@dataclass(slots=True)
class EDHRecCard:
    """A card from an EDHREC commander profile."""

    name: str
    scryfall_id: str
    synergy: float
    inclusion_rate: float
    category: str


@dataclass(slots=True)
class CommanderProfile:
    """Normalized EDHREC profile used by commander suggestion."""

    commander_name: str
    cards: list[EDHRecCard]
    num_decks: int


class EDHRecClient:
    """Fetch and cache EDHREC commander data via ``pyedhrec``."""

    _CACHE_TTL_HOURS = 24

    def __init__(
        self,
        cache: CardCache,
        edhrec: EDHRec | None = None,
        rate_limit_seconds: float = 0.15,
    ) -> None:
        self.cache = cache
        self.edhrec = edhrec or EDHRec()
        self.rate_limit_seconds = rate_limit_seconds
        self._last_request_time: float | None = None
        self._unknown_tags_logged: set[str] = set()

    def get_commander_profile(self, commander_name: str) -> CommanderProfile | None:
        """Return parsed commander profile or ``None`` when unavailable."""

        normalized = self._normalize_commander_name(commander_name)
        if not normalized:
            return None

        cache_key = f"edhrec:profile:{normalized}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            profile = self._deserialize_profile(cached)
            if profile is not None:
                return profile
            LOGGER.warning("Invalid cached EDHREC profile for '%s'; refetching", normalized)
            self.cache.delete(cache_key)

        self._respect_rate_limit()
        try:
            raw = self.edhrec.get_commander_data(commander_name.strip())
        except Exception as exc:  # noqa: BLE001 - pyedhrec can fail with many exception types
            LOGGER.warning("EDHREC commander profile request failed for '%s': %s", commander_name, exc)
            return None

        profile = self._parse_profile(raw, fallback_name=commander_name.strip())
        if profile is None:
            return None

        self.cache.put(
            cache_key,
            self._serialize_profile(profile),
            ttl_hours=self._CACHE_TTL_HOURS,
        )
        return profile

    def get_average_deck(self, commander_name: str) -> list[str] | None:
        """Return average deck lines like ``['1 Sol Ring', ...]`` or ``None``."""

        normalized = self._normalize_commander_name(commander_name)
        if not normalized:
            return None

        cache_key = f"edhrec:avgdeck:{normalized}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            parsed_cached = self._parse_decklist(cached)
            if parsed_cached is not None:
                return parsed_cached
            LOGGER.warning("Invalid cached EDHREC average deck for '%s'; refetching", normalized)
            self.cache.delete(cache_key)

        self._respect_rate_limit()
        try:
            raw = self.edhrec.get_commanders_average_deck(commander_name.strip())
        except Exception as exc:  # noqa: BLE001 - pyedhrec can fail with many exception types
            LOGGER.warning("EDHREC average deck request failed for '%s': %s", commander_name, exc)
            return None

        decklist = self._parse_decklist(raw)
        if decklist is None:
            return None

        self.cache.put(cache_key, decklist, ttl_hours=self._CACHE_TTL_HOURS)
        return decklist

    def _parse_profile(self, payload: Any, fallback_name: str) -> CommanderProfile | None:
        if not isinstance(payload, dict):
            return None

        container = payload.get("container")
        if not isinstance(container, dict):
            return None
        json_dict = container.get("json_dict")
        if not isinstance(json_dict, dict):
            return None
        cardlists = json_dict.get("cardlists", [])
        if not isinstance(cardlists, list):
            return None

        commander_name = self._extract_commander_name(payload, fallback_name)
        num_decks = self._coerce_int(payload.get("num_decks_avg"), default=0)

        merged: dict[str, dict[str, Any]] = {}
        for cardlist in cardlists:
            if not isinstance(cardlist, dict):
                continue

            tag = self._normalize_tag(cardlist.get("tag"))
            if not tag or tag in _EXCLUDED_CARDLIST_TAGS:
                continue
            self._log_unknown_tag(tag)

            category = self._safe_text(cardlist.get("header"), fallback=tag)
            raw_cardviews = cardlist.get("cardviews")
            if not isinstance(raw_cardviews, list):
                continue

            for raw_cardview in raw_cardviews:
                parsed_card = self._parse_cardview(raw_cardview, category=category)
                if parsed_card is None:
                    continue

                key = parsed_card.name.casefold()
                existing = merged.get(key)
                if existing is None:
                    merged[key] = {
                        "name": parsed_card.name,
                        "scryfall_id": parsed_card.scryfall_id,
                        "synergy": parsed_card.synergy,
                        "inclusion_rate": parsed_card.inclusion_rate,
                        "categories": {category},
                    }
                    continue

                # Deterministic merge: retain highest useful values across categories.
                existing["synergy"] = max(existing["synergy"], parsed_card.synergy)
                existing["inclusion_rate"] = max(
                    existing["inclusion_rate"],
                    parsed_card.inclusion_rate,
                )
                if not existing["scryfall_id"] and parsed_card.scryfall_id:
                    existing["scryfall_id"] = parsed_card.scryfall_id
                existing["categories"].add(category)

        cards: list[EDHRecCard] = []
        for key in sorted(merged):
            entry = merged[key]
            categories = sorted(entry["categories"], key=str.casefold)
            cards.append(
                EDHRecCard(
                    name=entry["name"],
                    scryfall_id=entry["scryfall_id"],
                    synergy=float(entry["synergy"]),
                    inclusion_rate=float(entry["inclusion_rate"]),
                    category=" / ".join(categories),
                )
            )

        return CommanderProfile(
            commander_name=commander_name,
            cards=cards,
            num_decks=num_decks,
        )

    def _parse_cardview(self, payload: Any, category: str) -> EDHRecCard | None:
        if not isinstance(payload, dict):
            return None

        name = self._safe_text(payload.get("name"), fallback="")
        if not name:
            return None

        scryfall_id = self._safe_text(payload.get("id"), fallback="")
        synergy = self._clamp(
            self._coerce_float(payload.get("synergy"), default=0.0),
            minimum=-1.0,
            maximum=1.0,
        )
        inclusion_rate = self._compute_inclusion_rate(payload)

        return EDHRecCard(
            name=name,
            scryfall_id=scryfall_id,
            synergy=synergy,
            inclusion_rate=inclusion_rate,
            category=category,
        )

    def _compute_inclusion_rate(self, payload: dict[str, Any]) -> float:
        inclusion = self._coerce_float(payload.get("inclusion"), default=0.0)
        potential = self._coerce_float(payload.get("potential_decks"), default=0.0)
        if potential > 0:
            return self._clamp(inclusion / potential, minimum=0.0, maximum=1.0)
        if 0.0 <= inclusion <= 1.0:
            return inclusion
        return 0.0

    def _parse_decklist(self, payload: Any) -> list[str] | None:
        raw_decklist: Any
        if isinstance(payload, dict):
            raw_decklist = payload.get("decklist")
            if raw_decklist is None:
                raw_decklist = payload.get("deck")
        else:
            raw_decklist = payload

        if not isinstance(raw_decklist, list):
            return None

        parsed = [
            line.strip()
            for line in raw_decklist
            if isinstance(line, str) and line.strip()
        ]
        if not parsed:
            return None
        return parsed

    def _serialize_profile(self, profile: CommanderProfile) -> dict[str, Any]:
        return {
            "commander_name": profile.commander_name,
            "num_decks": profile.num_decks,
            "cards": [
                {
                    "name": card.name,
                    "scryfall_id": card.scryfall_id,
                    "synergy": card.synergy,
                    "inclusion_rate": card.inclusion_rate,
                    "category": card.category,
                }
                for card in profile.cards
            ],
        }

    def _deserialize_profile(self, payload: Any) -> CommanderProfile | None:
        if not isinstance(payload, dict):
            return None

        commander_name = self._safe_text(payload.get("commander_name"), fallback="")
        if not commander_name:
            return None
        num_decks = self._coerce_int(payload.get("num_decks"), default=0)

        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list):
            return None

        cards: list[EDHRecCard] = []
        for raw_card in raw_cards:
            if not isinstance(raw_card, dict):
                return None
            name = self._safe_text(raw_card.get("name"), fallback="")
            if not name:
                return None
            cards.append(
                EDHRecCard(
                    name=name,
                    scryfall_id=self._safe_text(raw_card.get("scryfall_id"), fallback=""),
                    synergy=self._clamp(
                        self._coerce_float(raw_card.get("synergy"), default=0.0),
                        minimum=-1.0,
                        maximum=1.0,
                    ),
                    inclusion_rate=self._clamp(
                        self._coerce_float(raw_card.get("inclusion_rate"), default=0.0),
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    category=self._safe_text(raw_card.get("category"), fallback=""),
                )
            )

        return CommanderProfile(commander_name=commander_name, cards=cards, num_decks=num_decks)

    def _respect_rate_limit(self) -> None:
        now = time.monotonic()
        if self._last_request_time is not None:
            elapsed = now - self._last_request_time
            wait_for = self.rate_limit_seconds - elapsed
            if wait_for > 0:
                time.sleep(wait_for)
        self._last_request_time = time.monotonic()

    def _extract_commander_name(self, payload: dict[str, Any], fallback_name: str) -> str:
        value: Any = None
        container = payload.get("container")
        if isinstance(container, dict):
            json_dict = container.get("json_dict")
            if isinstance(json_dict, dict):
                card = json_dict.get("card")
                if isinstance(card, dict):
                    value = card.get("name")
        commander_name = self._safe_text(value, fallback=fallback_name)
        if commander_name:
            return commander_name
        return fallback_name

    def _log_unknown_tag(self, tag: str) -> None:
        if tag in _KNOWN_SYNERGY_TAGS or tag in self._unknown_tags_logged:
            return
        self._unknown_tags_logged.add(tag)
        LOGGER.info("Including unknown EDHREC cardlist tag '%s' in profile parsing", tag)

    @staticmethod
    def _normalize_commander_name(commander_name: str) -> str:
        return commander_name.strip().casefold()

    @staticmethod
    def _normalize_tag(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        tag = value.strip().casefold()
        if not tag:
            return None
        return tag

    @staticmethod
    def _safe_text(value: Any, fallback: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return fallback

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.strip()))
            except ValueError:
                return default
        return default

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(value, maximum))

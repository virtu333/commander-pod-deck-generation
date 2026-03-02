"""Commander Spellbook API integration for bracket and combo signals."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import requests

from src.collection.models import ComboInfo
from src.utils.cache import CardCache

LOGGER = logging.getLogger(__name__)

TAG_TO_BRACKET: dict[str, int] = {"R": 4, "S": 3, "P": 3, "O": 2, "C": 2, "E": 1}


class SpellbookError(Exception):
    """Internal exception for Spellbook request/response failures."""


@dataclass(slots=True)
class SpellbookResult:
    bracket_tag: str
    bracket: int
    game_changer_cards: list[str]
    mld_cards: list[str]
    extra_turn_cards: list[str]
    combos: list[ComboInfo]


class ComboDetector:
    """Estimate bracket and combo signals from Commander Spellbook."""

    ESTIMATE_URL = "https://backend.commanderspellbook.com/estimate-bracket"
    _COMBO_FIELDS: tuple[tuple[str, bool], ...] = (
        ("twoCardCombos", True),
        ("lockCombos", False),
        ("controlAllOpponentsCombos", False),
        ("controlSomeOpponentsCombos", False),
        ("skipTurnsCombos", False),
        ("extraTurnsCombos", False),
        ("massLandDenialCombos", False),
    )

    def __init__(self, cache: CardCache, timeout_seconds: float = 30.0) -> None:
        self.cache = cache
        self.timeout_seconds = timeout_seconds

    def estimate_bracket(
        self,
        card_names: list[str],
        commander_names: list[str],
    ) -> SpellbookResult | None:
        """Return Spellbook result or None on any failure/unknown tag."""

        request_payload = {
            "main": self._entries_for_request(card_names),
            "commanders": self._entries_for_request(commander_names),
        }
        normalized_payload = {
            "main": self._entries_for_hash(card_names),
            "commanders": self._entries_for_hash(commander_names),
        }
        cache_key = self._cache_key(normalized_payload)

        cached = self.cache.get(cache_key)
        if isinstance(cached, dict):
            cached_result = self._parse_result(cached)
            if cached_result is not None:
                return cached_result
            LOGGER.warning(
                "Ignoring cached Spellbook payload with invalid shape/tag for key %s",
                cache_key,
            )
            self.cache.delete(cache_key)
        elif cached is not None:
            LOGGER.warning(
                "Ignoring cached Spellbook payload with unexpected type %s for key %s",
                type(cached).__name__,
                cache_key,
            )
            self.cache.delete(cache_key)

        try:
            payload = self._request(request_payload)
        except SpellbookError as exc:
            LOGGER.warning("Spellbook estimate request failed: %s", exc)
            return None

        result = self._parse_result(payload)
        if result is None:
            return None

        self.cache.put(cache_key, payload, ttl_hours=1)
        return result

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                self.ESTIMATE_URL,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SpellbookError(f"network failure: {exc}") from exc

        if not response.ok:
            raise SpellbookError(f"HTTP {response.status_code}")

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise SpellbookError("invalid JSON payload") from exc

        if not isinstance(response_payload, dict):
            raise SpellbookError("unexpected response shape")
        return response_payload

    def _parse_result(self, payload: dict[str, Any]) -> SpellbookResult | None:
        raw_tag = payload.get("bracketTag")
        tag = self._normalize_tag(raw_tag)
        if tag is None:
            LOGGER.warning(
                "Unknown or missing Spellbook bracketTag '%s'; treating as unavailable",
                raw_tag,
            )
            return None

        return SpellbookResult(
            bracket_tag=tag,
            bracket=TAG_TO_BRACKET[tag],
            game_changer_cards=self._extract_card_names(payload.get("gameChangerCards")),
            mld_cards=self._extract_card_names(payload.get("massLandDenialCards")),
            extra_turn_cards=self._extract_card_names(payload.get("extraTurnCards")),
            combos=self._extract_combos(payload, fallback_tag=tag),
        )

    def _extract_combos(self, payload: dict[str, Any], fallback_tag: str) -> list[ComboInfo]:
        combos: list[ComboInfo] = []
        for field_name, fallback_is_two_card in self._COMBO_FIELDS:
            raw_items = payload.get(field_name)
            if not isinstance(raw_items, list):
                continue
            for raw_item in raw_items:
                combo = self._parse_combo_item(
                    raw_item,
                    fallback_tag=fallback_tag,
                    fallback_is_two_card=fallback_is_two_card,
                )
                if combo is not None:
                    combos.append(combo)
        return combos

    def _parse_combo_item(
        self,
        raw_item: Any,
        fallback_tag: str,
        fallback_is_two_card: bool,
    ) -> ComboInfo | None:
        if not isinstance(raw_item, dict):
            return None

        variant = raw_item.get("variant") if isinstance(raw_item.get("variant"), dict) else {}
        combo_obj = (
            variant.get("combo")
            if isinstance(variant.get("combo"), dict)
            else raw_item.get("combo")
            if isinstance(raw_item.get("combo"), dict)
            else {}
        )

        tag = (
            self._normalize_tag(raw_item.get("bracketTag"))
            or self._normalize_tag(variant.get("bracketTag"))
            or self._normalize_tag(combo_obj.get("bracketTag"))
            or fallback_tag
        )
        bracket = TAG_TO_BRACKET.get(tag, TAG_TO_BRACKET[fallback_tag])

        card_names = self._extract_combo_card_names(raw_item, variant, combo_obj)
        description = self._first_string(
            raw_item.get("description"),
            variant.get("description"),
            combo_obj.get("description"),
            default="",
        )
        mana_needed = self._first_string(
            raw_item.get("manaNeeded"),
            variant.get("manaNeeded"),
            combo_obj.get("manaNeeded"),
            raw_item.get("mana"),
            variant.get("mana"),
            combo_obj.get("mana"),
            default="",
        )
        combo_id = self._first_string(
            raw_item.get("id"),
            variant.get("id"),
            combo_obj.get("id"),
            default=self._synthetic_combo_id(card_names, description),
        )
        is_two_card = bool(
            fallback_is_two_card
            or raw_item.get("definitelyTwoCard")
            or variant.get("definitelyTwoCard")
        )

        return ComboInfo(
            combo_id=combo_id,
            card_names=card_names,
            description=description,
            bracket_tag=tag,
            bracket=bracket,
            is_two_card=is_two_card,
            mana_needed=mana_needed,
        )

    @staticmethod
    def _extract_card_names(raw_cards: Any) -> list[str]:
        if not isinstance(raw_cards, list):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for raw_card in raw_cards:
            if isinstance(raw_card, str):
                name = raw_card.strip()
            elif isinstance(raw_card, dict):
                if isinstance(raw_card.get("name"), str):
                    name = raw_card["name"].strip()
                elif (
                    isinstance(raw_card.get("card"), dict)
                    and isinstance(raw_card["card"].get("name"), str)
                ):
                    name = str(raw_card["card"]["name"]).strip()
                else:
                    continue
            else:
                continue

            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _extract_combo_card_names(self, *sources: Any) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for source in sources:
            for name in self._walk_card_names(source):
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                names.append(name)
        return names

    def _walk_card_names(self, value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            collected: list[str] = []
            for item in value:
                collected.extend(self._walk_card_names(item))
            return collected
        if isinstance(value, dict):
            names: list[str] = []
            if isinstance(value.get("name"), str):
                name = value["name"].strip()
                if name:
                    names.append(name)
            for key in ("card", "cards", "uses", "cardNames", "pieces"):
                if key in value:
                    names.extend(self._walk_card_names(value[key]))
            return names
        return []

    def _entries_for_request(self, card_names: list[str]) -> list[dict[str, Any]]:
        return self._aggregate_entries(card_names, casefold_labels=False)

    def _entries_for_hash(self, card_names: list[str]) -> list[dict[str, Any]]:
        return self._aggregate_entries(card_names, casefold_labels=True)

    @staticmethod
    def _aggregate_entries(
        names: list[str],
        casefold_labels: bool,
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        for raw_name in names:
            if not isinstance(raw_name, str):
                continue
            cleaned = raw_name.strip()
            if not cleaned:
                continue
            key = cleaned.casefold()
            counts[key] = counts.get(key, 0) + 1
            if casefold_labels:
                labels[key] = key
            elif key not in labels:
                labels[key] = cleaned

        return [
            {"card": labels[key], "quantity": counts[key]}
            for key in sorted(counts)
        ]

    @staticmethod
    def _cache_key(normalized_payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            normalized_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"spellbook:{digest}"

    @staticmethod
    def _normalize_tag(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        if normalized in TAG_TO_BRACKET:
            return normalized
        return None

    @staticmethod
    def _first_string(*values: Any, default: str) -> str:
        for value in values:
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
        return default

    @staticmethod
    def _synthetic_combo_id(card_names: list[str], description: str) -> str:
        base = "|".join(card_names) + "|" + description
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
        return f"combo-{digest}"

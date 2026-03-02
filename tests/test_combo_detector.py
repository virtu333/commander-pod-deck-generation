"""Tests for Commander Spellbook combo detector integration."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from src.brackets.combo_detector import ComboDetector, TAG_TO_BRACKET
from src.utils.cache import CardCache


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


def _success_payload(tag: str = "S") -> dict:
    return {
        "bracketTag": tag,
        "gameChangerCards": [{"name": "Sol Ring"}],
        "massLandDenialCards": [{"name": "Armageddon"}],
        "extraTurnCards": [{"name": "Time Warp"}],
        "twoCardCombos": [
            {
                "id": "combo-1",
                "bracketTag": "P",
                "description": "Infinite mana",
                "definitelyTwoCard": True,
                "manaNeeded": "{3}",
                "cards": [{"name": "Basalt Monolith"}, {"name": "Rings of Brighthearth"}],
            }
        ],
        "lockCombos": [],
        "controlAllOpponentsCombos": [],
        "controlSomeOpponentsCombos": [],
        "skipTurnsCombos": [],
        "extraTurnsCombos": [],
        "massLandDenialCombos": [],
    }


def test_successful_api_call_parsed_correctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)

    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        lambda url, json=None, timeout=None: FakeResponse(200, _success_payload("S")),
    )

    result = detector.estimate_bracket(
        card_names=["Sol Ring", "Basalt Monolith", "Rings of Brighthearth"],
        commander_names=["Urza, Lord High Artificer"],
    )

    assert result is not None
    assert result.bracket_tag == "S"
    assert result.bracket == 3
    assert result.game_changer_cards == ["Sol Ring"]
    assert result.mld_cards == ["Armageddon"]
    assert result.extra_turn_cards == ["Time Warp"]
    assert len(result.combos) == 1
    assert result.combos[0].combo_id == "combo-1"
    assert result.combos[0].is_two_card is True


def test_network_error_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)

    def raise_request_error(url, json=None, timeout=None):  # noqa: ANN001
        raise requests.RequestException("boom")

    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        raise_request_error,
    )

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])
    assert result is None


def test_http_500_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)
    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        lambda url, json=None, timeout=None: FakeResponse(500),
    )

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])
    assert result is None


def test_unknown_bracket_tag_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)
    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        lambda url, json=None, timeout=None: FakeResponse(200, _success_payload("X")),
    )

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])
    assert result is None


def test_cached_response_skips_second_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)
    calls: list[int] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        calls.append(1)
        return FakeResponse(200, _success_payload("P"))

    monkeypatch.setattr("src.brackets.combo_detector.requests.post", fake_post)

    first = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])
    second = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])

    assert first is not None
    assert second is not None
    assert len(calls) == 1


def test_invalid_cached_payload_refetches_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)

    normalized_payload = {
        "main": detector._entries_for_hash(["Sol Ring"]),
        "commanders": detector._entries_for_hash(["Omnath, Locus of Creation"]),
    }
    cache_key = detector._cache_key(normalized_payload)
    cache.put(cache_key, {"bracketTag": "X"}, ttl_hours=1)

    calls: list[int] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        calls.append(1)
        return FakeResponse(200, _success_payload("S"))

    monkeypatch.setattr("src.brackets.combo_detector.requests.post", fake_post)

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])

    assert result is not None
    assert result.bracket_tag == "S"
    assert len(calls) == 1


def test_invalid_cached_payload_then_live_failure_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)

    normalized_payload = {
        "main": detector._entries_for_hash(["Sol Ring"]),
        "commanders": detector._entries_for_hash(["Omnath, Locus of Creation"]),
    }
    cache_key = detector._cache_key(normalized_payload)
    cache.put(cache_key, {"bracketTag": "X"}, ttl_hours=1)

    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        lambda url, json=None, timeout=None: FakeResponse(500),
    )

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])

    assert result is None
    assert cache.get(cache_key) is None


def test_invalid_cached_payload_is_not_reused_next_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)

    normalized_payload = {
        "main": detector._entries_for_hash(["Sol Ring"]),
        "commanders": detector._entries_for_hash(["Omnath, Locus of Creation"]),
    }
    cache_key = detector._cache_key(normalized_payload)
    cache.put(cache_key, {"bracketTag": "X"}, ttl_hours=1)

    calls: list[int] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        calls.append(1)
        return FakeResponse(200, _success_payload("S"))

    monkeypatch.setattr("src.brackets.combo_detector.requests.post", fake_post)

    first = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])
    second = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])

    assert first is not None
    assert second is not None
    assert len(calls) == 1
    assert isinstance(cache.get(cache_key), dict)


def test_cache_key_is_order_insensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    detector = ComboDetector(cache)
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        calls.append(json or {})
        return FakeResponse(200, _success_payload("O"))

    monkeypatch.setattr("src.brackets.combo_detector.requests.post", fake_post)

    first = detector.estimate_bracket(
        ["Sol Ring", "Arcane Signet", "Sol Ring"],
        ["Atraxa, Praetors' Voice", "Ishai, Ojutai Dragonspeaker"],
    )
    second = detector.estimate_bracket(
        ["Arcane Signet", "sol ring", "Sol Ring"],
        ["ishai, ojutai dragonspeaker", "Atraxa, Praetors' Voice"],
    )

    assert first is not None
    assert second is not None
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("tag", "expected_bracket"),
    [
        ("R", 4),
        ("S", 3),
        ("P", 3),
        ("O", 2),
        ("C", 2),
        ("E", 1),
    ],
)
def test_tag_to_bracket_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tag: str,
    expected_bracket: int,
) -> None:
    cache = CardCache(str(tmp_path / f"{tag}.db"))
    detector = ComboDetector(cache)
    monkeypatch.setattr(
        "src.brackets.combo_detector.requests.post",
        lambda url, json=None, timeout=None: FakeResponse(200, _success_payload(tag)),
    )

    result = detector.estimate_bracket(["Sol Ring"], ["Omnath, Locus of Creation"])

    assert result is not None
    assert result.bracket == expected_bracket
    assert TAG_TO_BRACKET[tag] == expected_bracket

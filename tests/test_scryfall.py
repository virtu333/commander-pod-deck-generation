"""Tests for the Scryfall API client."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from src.utils.cache import CardCache
from src.utils.scryfall import ScryfallClient, ScryfallError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self._payload


def _card_payload(card_id: str, name: str, set_code: str = "cmm") -> dict:
    return {
        "id": card_id,
        "name": name,
        "layout": "normal",
        "mana_cost": "{1}",
        "cmc": 1,
        "color_identity": [],
        "type_line": "Artifact",
        "oracle_text": "Test text",
        "keywords": [],
        "legalities": {"commander": "legal"},
        "set": set_code,
        "collector_number": "1",
        "rarity": "rare",
    }


def test_get_card_and_cache_hit_skip_second_api_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache)
    calls: list[str] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls.append(url)
        return FakeResponse(200, _card_payload("card-1", "Sol Ring"))

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)

    first = client.get_card("card-1")
    second = client.get_card("card-1")

    assert first is not None
    assert first.name == "Sol Ring"
    assert second is not None
    assert len(calls) == 1


def test_search_handles_pagination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache)

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        if "page=2" in url:
            return FakeResponse(
                200,
                {
                    "object": "list",
                    "data": [_card_payload("card-2", "Command Tower")],
                    "has_more": False,
                },
            )
        return FakeResponse(
            200,
            {
                "object": "list",
                "data": [_card_payload("card-1", "Sol Ring")],
                "has_more": True,
                "next_page": "https://api.scryfall.com/cards/search?page=2",
            },
        )

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)
    cards = client.search("mana rock")

    assert [card.name for card in cards] == ["Sol Ring", "Command Tower"]


def test_404_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache)

    monkeypatch.setattr(
        "src.utils.scryfall.requests.get",
        lambda url, params=None, timeout=None: FakeResponse(404),
    )

    assert client.get_card("missing-card") is None


def test_network_error_raises_scryfall_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache)

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        raise requests.RequestException("network down")

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)

    with pytest.raises(ScryfallError):
        client.get_card("card-1")


def test_rate_limiting_sleeps_between_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.1)
    sleeps: list[float] = []
    monotonic_values = iter([0.0, 0.0, 0.05, 0.05, 0.2, 0.2])

    monkeypatch.setattr("src.utils.scryfall.requests.get", lambda url, params=None, timeout=None: FakeResponse(200, _card_payload(url, "Card")))
    monkeypatch.setattr("src.utils.scryfall.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("src.utils.scryfall.time.sleep", lambda seconds: sleeps.append(seconds))

    client.get_card("card-1")
    client.get_card("card-2")

    assert sleeps
    assert any(seconds > 0 for seconds in sleeps)


def test_dfc_parsing_uses_front_face_name_and_combines_oracle_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache)
    payload = {
        "id": "dfc-1",
        "name": "Delver of Secrets // Insectile Aberration",
        "layout": "transform",
        "cmc": 1,
        "color_identity": ["U"],
        "type_line": "Creature - Human Wizard",
        "oracle_text": "",
        "keywords": [],
        "legalities": {"commander": "legal"},
        "set": "isd",
        "collector_number": "51",
        "rarity": "common",
        "card_faces": [
            {"name": "Delver of Secrets", "mana_cost": "{U}", "oracle_text": "At the beginning of your upkeep..."},
            {"name": "Insectile Aberration", "mana_cost": "", "oracle_text": "Flying"},
        ],
    }

    monkeypatch.setattr("src.utils.scryfall.requests.get", lambda url, params=None, timeout=None: FakeResponse(200, payload))
    card = client.get_card("dfc-1")

    assert card is not None
    assert card.name == "Delver of Secrets"
    assert "At the beginning of your upkeep..." in card.oracle_text
    assert "Flying" in card.oracle_text


def test_429_retry_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)
    responses = iter(
        [
            FakeResponse(429, headers={"Retry-After": "0.25"}),
            FakeResponse(200, _card_payload("card-1", "Sol Ring")),
        ]
    )
    sleeps: list[float] = []

    monkeypatch.setattr("src.utils.scryfall.requests.get", lambda url, params=None, timeout=None: next(responses))
    monkeypatch.setattr("src.utils.scryfall.time.sleep", lambda seconds: sleeps.append(seconds))

    card = client.get_card("card-1")
    assert card is not None
    assert card.name == "Sol Ring"
    assert any(abs(value - 0.25) < 1e-9 for value in sleeps)


def test_429_retry_exhaustion_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)

    monkeypatch.setattr(
        "src.utils.scryfall.requests.get",
        lambda url, params=None, timeout=None: FakeResponse(429),
    )
    monkeypatch.setattr("src.utils.scryfall.time.sleep", lambda seconds: None)

    with pytest.raises(ScryfallError, match="rate limit retries exceeded"):
        client.get_card("card-1")


def test_get_game_changers_cache_miss_populates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)
    payload = {
        "object": "list",
        "data": [
            _card_payload("gc-1", "Game Changer One"),
            _card_payload("gc-2", "Game Changer Two"),
        ],
        "has_more": False,
    }
    calls: list[str] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls.append(url)
        return FakeResponse(200, payload)

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)

    cards = client.get_game_changers()
    assert [card.scryfall_id for card in cards] == ["gc-1", "gc-2"]
    assert cache.get("game_changers") == ["gc-1", "gc-2"]
    assert len(calls) == 1


def test_get_game_changers_cache_hit_uses_local_cache_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)
    cache.put("game_changers", ["gc-1", "gc-2"], ttl_hours=24 * 7)
    cache.put_card("gc-1", _card_payload("gc-1", "Game Changer One"))
    cache.put_card("gc-2", _card_payload("gc-2", "Game Changer Two"))

    def fail_get(url, params=None, timeout=None):  # noqa: ANN001
        raise AssertionError("network call should not happen on full cache hit")

    monkeypatch.setattr("src.utils.scryfall.requests.get", fail_get)

    cards = client.get_game_changers()
    assert [card.scryfall_id for card in cards] == ["gc-1", "gc-2"]


def test_get_game_changers_hydrates_missing_cached_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)
    cache.put("game_changers", ["gc-1", "gc-2"], ttl_hours=24 * 7)
    cache.put_card("gc-1", _card_payload("gc-1", "Game Changer One"))
    calls: list[str] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls.append(url)
        return FakeResponse(200, _card_payload("gc-2", "Game Changer Two"))

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)

    cards = client.get_game_changers()
    assert [card.scryfall_id for card in cards] == ["gc-1", "gc-2"]
    assert len(calls) == 1


def test_get_game_changers_preserves_cached_id_order_during_hydration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    client = ScryfallClient(cache, min_request_gap_seconds=0.0)
    cache.put("game_changers", ["gc-1", "gc-2", "gc-3"], ttl_hours=24 * 7)
    cache.put_card("gc-2", _card_payload("gc-2", "Game Changer Two"))
    cache.put_card("gc-3", _card_payload("gc-3", "Game Changer Three"))

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        return FakeResponse(200, _card_payload("gc-1", "Game Changer One"))

    monkeypatch.setattr("src.utils.scryfall.requests.get", fake_get)

    cards = client.get_game_changers()
    assert [card.scryfall_id for card in cards] == ["gc-1", "gc-2", "gc-3"]

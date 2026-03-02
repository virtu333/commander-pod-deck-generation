"""Tests for SQLite cache behavior."""

from __future__ import annotations

from pathlib import Path
import threading

from src.utils.cache import CardCache


def _sample_card(card_id: str, name: str) -> dict:
    return {
        "id": card_id,
        "name": name,
        "cmc": 1,
        "color_identity": ["W"],
        "type_line": "Artifact",
        "oracle_text": "",
        "keywords": [],
        "legalities": {"commander": "legal"},
        "set": "cmm",
        "collector_number": "1",
        "rarity": "rare",
    }


def test_store_and_retrieve_card(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    payload = _sample_card("card-1", "Sol Ring")

    cache.put_card("card-1", payload)
    loaded = cache.get_card("card-1")

    assert loaded is not None
    assert loaded["id"] == "card-1"
    assert loaded["name"] == "Sol Ring"


def test_bulk_get_returns_found_subset(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    cache.put_card("card-1", _sample_card("card-1", "Sol Ring"))
    cache.put_card("card-2", _sample_card("card-2", "Arcane Signet"))

    loaded = cache.get_cards_bulk(["card-1", "card-2", "missing"])

    assert set(loaded) == {"card-1", "card-2"}
    assert loaded["card-2"]["name"] == "Arcane Signet"


def test_kv_cache_ttl(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))

    cache.put("game_changers", {"count": 53}, ttl_hours=24)
    assert cache.get("game_changers") == {"count": 53}

    cache.put("expired", {"count": 1}, ttl_hours=0)
    assert cache.get("expired") is None


def test_clear_wipes_all_tables(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    cache.put_card("card-1", _sample_card("card-1", "Sol Ring"))
    cache.put("game_changers", [1, 2, 3], ttl_hours=24)

    cache.clear()

    assert cache.get_card("card-1") is None
    assert cache.get("game_changers") is None


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "context-cache.db"
    with CardCache(str(db_path)) as cache:
        cache.put_card("card-1", _sample_card("card-1", "Sol Ring"))

    db_path.unlink()
    assert not db_path.exists()


def test_close_is_idempotent(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "cache.db"))
    cache.close()
    cache.close()


def test_cache_accessible_from_another_thread(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "threaded-cache.db"))
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            cache.put_card("card-thread", _sample_card("card-thread", "Arcane Signet"))
            loaded = cache.get_card("card-thread")
            assert loaded is not None
            assert loaded["name"] == "Arcane Signet"
            cache.put("thread:key", {"ok": True}, ttl_hours=1)
            assert cache.get("thread:key") == {"ok": True}
        except BaseException as exc:  # pragma: no cover - assertion proxy for threads
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []

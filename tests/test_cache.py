"""Tests for SQLite cache behavior."""

from __future__ import annotations

from pathlib import Path
import threading

import pytest

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


def _oracle_card(card_id: str, name: str, *, layout: str = "normal") -> dict:
    payload = _sample_card(card_id, name)
    payload["layout"] = layout
    return payload


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
    cache.replace_oracle_cards([_oracle_card("oracle-1", "Oracle Card")])

    cache.clear()

    assert cache.get_card("card-1") is None
    assert cache.get("game_changers") is None
    assert cache.get_oracle_cards_by_name("Oracle Card") == []
    assert not cache.has_complete_oracle_data()


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


def test_replace_oracle_cards_supports_full_and_front_face_lookup(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "oracle.db"))
    inserted = cache.replace_oracle_cards(
        [
            {
                "id": "dfc-1",
                "name": "Delver of Secrets // Insectile Aberration",
                "layout": "transform",
                "set": "isd",
                "collector_number": "51",
                "card_faces": [
                    {"name": "Delver of Secrets"},
                    {"name": "Insectile Aberration"},
                ],
            },
            _oracle_card("normal-1", "Sol Ring"),
        ]
    )

    assert inserted == 2
    assert cache.has_complete_oracle_data()
    full_name_matches = cache.get_oracle_cards_by_name(
        "Delver of Secrets // Insectile Aberration"
    )
    front_face_matches = cache.get_oracle_cards_by_name("  delver of secrets  ")

    assert [card["id"] for card in full_name_matches] == ["dfc-1"]
    assert [card["id"] for card in front_face_matches] == ["dfc-1"]


def test_replace_oracle_cards_is_atomic_when_load_fails(tmp_path: Path) -> None:
    cache = CardCache(str(tmp_path / "oracle-atomic.db"))
    cache.replace_oracle_cards([_oracle_card("old-1", "Sol Ring")])

    with pytest.raises(ValueError, match="missing required field: id"):
        cache.replace_oracle_cards(
            [
                _oracle_card("new-1", "Arcane Signet"),
                {"name": "Bad Entry"},
            ]
        )

    assert cache.has_complete_oracle_data()
    assert [card["id"] for card in cache.get_oracle_cards_by_name("Sol Ring")] == [
        "old-1"
    ]
    assert cache.get_oracle_cards_by_name("Arcane Signet") == []

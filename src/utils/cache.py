"""SQLite cache for Scryfall card data and generic TTL key-value entries.

Contract note:
- `CardCache.get_card(...) -> dict | None` returns raw Scryfall JSON.
- `ScryfallClient.get_card(...) -> Card | None` parses that JSON into a dataclass.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class CardCache:
    """Simple SQLite-backed cache used across slices."""

    def __init__(self, db_path: str = "data/edh_cache.db") -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._closed = False
        self._ensure_parent_dir()
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._enable_wal()
        self._create_tables()

    def __enter__(self) -> CardCache:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Best-effort cleanup only.
            pass

    def _ensure_parent_dir(self) -> None:
        if self.db_path == ":memory:":
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _enable_wal(self) -> None:
        # WAL improves mixed read/write access for a local cache.
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    scryfall_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_cache (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    ttl_hours INTEGER NOT NULL
                );
                """
            )

    def get_card(self, scryfall_id: str) -> dict[str, Any] | None:
        """Return cached raw Scryfall card JSON by ID (`dict | None`)."""

        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM cards WHERE scryfall_id = ?",
                (scryfall_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["data_json"])

    def put_card(self, scryfall_id: str, data: dict[str, Any]) -> None:
        """Store raw Scryfall card JSON."""

        payload = json.dumps(data)
        cached_at = self._now_iso()
        name = str(data.get("name", ""))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cards (scryfall_id, name, data_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scryfall_id) DO UPDATE SET
                    name=excluded.name,
                    data_json=excluded.data_json,
                    cached_at=excluded.cached_at
                """,
                (scryfall_id, name, payload, cached_at),
            )

    def get_cards_bulk(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return a mapping of cached IDs to card JSON payloads."""

        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        query = (
            "SELECT scryfall_id, data_json FROM cards "
            f"WHERE scryfall_id IN ({placeholders})"
        )
        with self._lock:
            rows = self._conn.execute(query, unique_ids).fetchall()
        return {row["scryfall_id"]: json.loads(row["data_json"]) for row in rows}

    def get(self, key: str) -> Any | None:
        """Return a generic cache value if present and not expired."""

        with self._lock:
            row = self._conn.execute(
                "SELECT value_json, cached_at, ttl_hours FROM kv_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if self._is_expired(row["cached_at"], int(row["ttl_hours"])):
                with self._conn:
                    self._conn.execute("DELETE FROM kv_cache WHERE key = ?", (key,))
                return None
        return json.loads(row["value_json"])

    def put(self, key: str, value: Any, ttl_hours: int = 24) -> None:
        """Store a generic cache value with an expiration TTL in hours."""

        payload = json.dumps(value)
        cached_at = self._now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO kv_cache (key, value_json, cached_at, ttl_hours)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    cached_at=excluded.cached_at,
                    ttl_hours=excluded.ttl_hours
                """,
                (key, payload, cached_at, ttl_hours),
            )

    def delete(self, key: str) -> None:
        """Delete a generic cached key if present."""

        with self._lock, self._conn:
            self._conn.execute("DELETE FROM kv_cache WHERE key = ?", (key,))

    def clear(self) -> None:
        """Clear all cached values."""

        with self._lock, self._conn:
            self._conn.execute("DELETE FROM cards")
            self._conn.execute("DELETE FROM kv_cache")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _is_expired(cached_at: str, ttl_hours: int) -> bool:
        cached = datetime.fromisoformat(cached_at)
        if cached.tzinfo is None:
            cached = cached.replace(tzinfo=timezone.utc)
        expires_at = cached + timedelta(hours=ttl_hours)
        return datetime.now(timezone.utc) >= expires_at

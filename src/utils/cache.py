"""SQLite cache for Scryfall card data and generic TTL key-value entries.

Contract note:
- `CardCache.get_card(...) -> dict | None` returns raw Scryfall JSON.
- `ScryfallClient.get_card(...) -> Card | None` parses that JSON into a dataclass.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, cast


class CardCache:
    """Simple SQLite-backed cache used across slices."""

    _ORACLE_STATE_SINGLETON = 1
    _ORACLE_DFC_LAYOUTS = {"transform", "modal_dfc", "double_faced_token"}

    def __init__(self, db_path: str = "data/edh_cache.db") -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._closed = False
        self._ensure_parent_dir()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._enable_wal()
        self._create_tables()

    def __enter__(self) -> CardCache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oracle_cards (
                    scryfall_id TEXT PRIMARY KEY,
                    name_key TEXT NOT NULL,
                    front_name_key TEXT,
                    set_code TEXT NOT NULL,
                    collector_number TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_oracle_cards_name_key
                ON oracle_cards(name_key);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_oracle_cards_front_name_key
                ON oracle_cards(front_name_key);
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oracle_bulk_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    is_complete INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    loaded_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                INSERT INTO oracle_bulk_state (singleton, is_complete, row_count, loaded_at)
                VALUES (?, 0, 0, ?)
                ON CONFLICT(singleton) DO NOTHING;
                """,
                (self._ORACLE_STATE_SINGLETON, self._now_iso()),
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
        return cast(dict[str, Any], json.loads(row["data_json"]))

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

    def replace_oracle_cards(self, cards: Iterable[dict[str, Any]]) -> int:
        """Replace Oracle bulk data atomically, returning inserted row count."""

        inserted_count = 0
        loaded_at = self._now_iso()
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    UPDATE oracle_bulk_state
                    SET is_complete = 0, row_count = 0, loaded_at = ?
                    WHERE singleton = ?
                    """,
                    (loaded_at, self._ORACLE_STATE_SINGLETON),
                )
                self._conn.execute("DELETE FROM oracle_cards")
                for card in cards:
                    row = self._oracle_row(card)
                    self._conn.execute(
                        """
                        INSERT INTO oracle_cards (
                            scryfall_id,
                            name_key,
                            front_name_key,
                            set_code,
                            collector_number,
                            data_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    inserted_count += 1
                if inserted_count <= 0:
                    raise ValueError(
                        "Oracle bulk data must contain at least one valid card entry."
                    )
                self._conn.execute(
                    """
                    UPDATE oracle_bulk_state
                    SET is_complete = 1, row_count = ?, loaded_at = ?
                    WHERE singleton = ?
                    """,
                    (inserted_count, loaded_at, self._ORACLE_STATE_SINGLETON),
                )
        except sqlite3.Error as exc:
            raise ValueError(f"Failed to write Oracle bulk data: {exc}") from exc

        return inserted_count

    def has_complete_oracle_data(self) -> bool:
        """Return True only when Oracle data is fully loaded and row-count consistent."""

        with self._lock:
            return self._oracle_dataset_ready_locked()

    def get_oracle_cards_by_name(self, name: str) -> list[dict[str, Any]]:
        """Return Oracle candidate cards by normalized name or DFC front-face name."""

        normalized = self._normalize_name_key(name)
        if not normalized:
            return []

        with self._lock:
            if not self._oracle_dataset_ready_locked():
                return []
            rows = self._conn.execute(
                """
                SELECT data_json
                FROM oracle_cards
                WHERE name_key = ? OR front_name_key = ?
                ORDER BY lower(set_code), lower(collector_number), scryfall_id
                """,
                (normalized, normalized),
            ).fetchall()

        cards: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["data_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                cards.append(payload)
        return cards

    def clear(self) -> None:
        """Clear all cached values."""

        with self._lock, self._conn:
            self._conn.execute("DELETE FROM cards")
            self._conn.execute("DELETE FROM kv_cache")
            self._conn.execute("DELETE FROM oracle_cards")
            self._conn.execute(
                """
                UPDATE oracle_bulk_state
                SET is_complete = 0, row_count = 0, loaded_at = ?
                WHERE singleton = ?
                """,
                (self._now_iso(), self._ORACLE_STATE_SINGLETON),
            )

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

    def _oracle_dataset_ready_locked(self) -> bool:
        state_row = self._conn.execute(
            """
            SELECT is_complete, row_count
            FROM oracle_bulk_state
            WHERE singleton = ?
            """,
            (self._ORACLE_STATE_SINGLETON,),
        ).fetchone()
        if state_row is None:
            return False
        if int(state_row["is_complete"]) != 1:
            return False
        expected_row_count = int(state_row["row_count"])
        if expected_row_count <= 0:
            return False

        actual_row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM oracle_cards"
        ).fetchone()
        actual_row_count = int(actual_row["count"]) if actual_row is not None else 0
        return actual_row_count == expected_row_count

    def _oracle_row(
        self,
        card: dict[str, Any],
    ) -> tuple[str, str, str | None, str, str, str]:
        if not isinstance(card, dict):
            raise ValueError("Oracle bulk JSON must contain only object entries.")

        scryfall_id = str(card.get("id", "")).strip()
        if not scryfall_id:
            raise ValueError("Oracle card entry is missing required field: id")

        name = str(card.get("name", "")).strip()
        if not name:
            raise ValueError(f"Oracle card {scryfall_id} is missing required field: name")

        name_key = self._normalize_name_key(name)
        if not name_key:
            raise ValueError(
                f"Oracle card {scryfall_id} has an empty normalized name key."
            )

        layout = str(card.get("layout", "")).strip().casefold()
        front_name_key = self._oracle_front_name_key(card, layout)
        set_code = str(card.get("set", "")).strip()
        collector_number = str(card.get("collector_number", "")).strip()
        payload = json.dumps(card)
        return (
            scryfall_id,
            name_key,
            front_name_key,
            set_code,
            collector_number,
            payload,
        )

    @staticmethod
    def _oracle_front_name_key(card: dict[str, Any], layout: str) -> str | None:
        if layout not in CardCache._ORACLE_DFC_LAYOUTS:
            return None
        card_faces = card.get("card_faces", [])
        if not isinstance(card_faces, list):
            return None
        for face in card_faces:
            if not isinstance(face, dict):
                continue
            name = str(face.get("name", "")).strip()
            if name:
                return CardCache._normalize_name_key(name)
        return None

    @staticmethod
    def _normalize_name_key(name: str) -> str:
        return " ".join(name.strip().split()).casefold()

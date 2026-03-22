"""SQLite-backed JSON storage helpers for SmokeBot.

This module provides read_json/write_json helpers with the same call shape as
legacy file-based JSON functions, while storing data in a single SQLite file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

_DB_PATH = Path(os.environ.get("SMOKEBOT_DB_PATH", "smokebot_data.sqlite3"))
DEFAULT_JSON_KEYS = (
    "reaction_roles.json",
    "ticket_data.json",
    "pinned_messages.json",
    "giveaways.json",
    "snippets.json",
    "auto_replies.json",
    "script_triggers.json",
    "ticket_categories.json",
)
_MISSING = object()


class SQLiteJSONStorage:
    """Simple key/value JSON store backed by a single SQLite database file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS json_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def read(self, key: str, default_factory: Callable[[], Any] | Any):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM json_store WHERE key = ?", (key,)
            ).fetchone()

        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                pass

        migrated = self._load_legacy_json(key)
        if migrated is not _MISSING:
            self.write(key, migrated)
            return migrated

        data = default_factory() if callable(default_factory) else default_factory
        self.write(key, data)
        return data

    def has_key(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM json_store WHERE key = ? LIMIT 1", (key,)
            ).fetchone()
        return row is not None

    def migrate_legacy_file(self, key: str, overwrite: bool = False) -> bool:
        if not overwrite and self.has_key(key):
            return False

        migrated = self._load_legacy_json(key)
        if migrated is _MISSING:
            return False

        self.write(key, migrated)
        return True

    def _load_legacy_json(self, key: str) -> Any:
        legacy_path = Path(key)
        if not legacy_path.is_file():
            return _MISSING

        with legacy_path.open("r", encoding="utf-8") as legacy_file:
            return json.load(legacy_file)

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def write(self, key: str, data: Any):
        serialized = json.dumps(data, indent=2)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO json_store (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (key, serialized),
            )
            self._conn.commit()


_storage = SQLiteJSONStorage(_DB_PATH)


def read_json(path: str, default_factory: Callable[[], Any] | Any):
    """Read JSON content for a logical key from SQLite storage."""
    return _storage.read(path, default_factory)


def write_json(path: str, data: Any):
    """Write JSON content for a logical key to SQLite storage."""
    _storage.write(path, data)


def migrate_legacy_json_files(paths: Iterable[str] | None = None) -> dict[str, Any]:
    """Import legacy JSON files into SQLite when the key is not already present."""
    migrated = []
    errors: dict[str, str] = {}

    for path in paths or DEFAULT_JSON_KEYS:
        try:
            if _storage.migrate_legacy_file(path):
                migrated.append(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors[path] = str(exc)

    return {"migrated": migrated, "errors": errors}


def close_storage():
    """Close the shared SQLite connection."""
    _storage.close()

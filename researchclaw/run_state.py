"""Run-state persistence backends.

The JSON backend preserves the existing ``progress.json`` contract while giving
future backends, such as SQLite, a small interface to implement.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class RunStateBackend(Protocol):
    """Persistence interface for structured run progress snapshots."""

    def read_progress(self, run_dir: Path) -> dict[str, object] | None:
        """Return the latest progress snapshot, or ``None`` when unavailable."""

    def write_progress(self, run_dir: Path, payload: dict[str, object]) -> None:
        """Persist the latest progress snapshot for a run."""


class JsonRunStateBackend:
    """Store run state in the legacy ``progress.json`` file."""

    filename = "progress.json"

    def progress_path(self, run_dir: Path) -> Path:
        return run_dir / self.filename

    def read_progress(self, run_dir: Path) -> dict[str, object] | None:
        progress_path = self.progress_path(run_dir)
        if not progress_path.exists():
            return None
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug(
                "Failed to read progress snapshot %s: %s",
                progress_path,
                exc,
                exc_info=True,
            )
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def write_progress(self, run_dir: Path, payload: dict[str, object]) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.progress_path(run_dir).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )


class SQLiteRunStateBackend:
    """Store run progress snapshots in a local SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    def read_progress(self, run_dir: Path) -> dict[str, object] | None:
        self._ensure_schema()
        run_key = self._run_key(run_dir)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM run_progress WHERE run_key = ?",
                (run_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError as exc:
            logger.debug(
                "Failed to decode SQLite progress snapshot for %s: %s",
                run_key,
                exc,
                exc_info=True,
            )
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def write_progress(self, run_dir: Path, payload: dict[str, object]) -> None:
        self._ensure_schema()
        run_dir.mkdir(parents=True, exist_ok=True)
        run_key = self._run_key(run_dir)
        payload_json = json.dumps(payload, indent=2)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_progress (run_key, payload_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (run_key, payload_json),
            )

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_progress (
                    run_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _run_key(run_dir: Path) -> str:
        return str(run_dir.resolve())


DEFAULT_RUN_STATE_BACKEND = JsonRunStateBackend()

"""Run-state persistence backends.

The JSON backend preserves the existing ``progress.json`` contract while giving
future backends, such as SQLite, a small interface to implement.
"""

from __future__ import annotations

import json
import logging
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


DEFAULT_RUN_STATE_BACKEND = JsonRunStateBackend()


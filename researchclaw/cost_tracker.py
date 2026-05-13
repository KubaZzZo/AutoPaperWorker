"""Lightweight process-wide LLM cost tracker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class CostEntry:
    """One recorded API cost event."""

    timestamp: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] | None = None


class CostTracker:
    """Track accumulated LLM cost and optionally persist JSONL entries."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path is not None else None
        self._entries: list[CostEntry] = []
        self._lock = Lock()
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return sum(entry.cost_usd for entry in self._entries)

    def record(
        self,
        provider: str,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        **metadata: Any,
    ) -> CostEntry:
        """Record an API usage cost event."""

        entry = CostEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider=provider,
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cost_usd=float(cost_usd or 0.0),
            metadata=metadata or None,
        )
        with self._lock:
            self._entries.append(entry)
            if self.log_path is not None:
                payload = asdict(entry)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return entry

    def check_budget(self, max_budget_usd: float) -> bool:
        """Return True if current spend is within ``max_budget_usd``."""

        if max_budget_usd <= 0:
            return True
        return self.total_cost_usd <= float(max_budget_usd)


_GLOBAL_TRACKER = CostTracker()


def get_global_tracker() -> CostTracker:
    """Return the process-wide tracker used by pipeline/HITL budget checks."""

    return _GLOBAL_TRACKER


def reset_global_tracker(log_path: str | Path | None = None) -> CostTracker:
    """Reset the process-wide tracker, primarily for tests."""

    global _GLOBAL_TRACKER
    _GLOBAL_TRACKER = CostTracker(log_path=log_path)
    return _GLOBAL_TRACKER

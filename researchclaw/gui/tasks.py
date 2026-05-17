"""Background task helpers for GUI callbacks."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class TaskEvent:
    kind: str
    payload: object


def run_background(func: Callable[[], T], events: queue.Queue[TaskEvent]) -> threading.Thread:
    """Run ``func`` on a daemon thread and push result/error events."""
    def _target() -> None:
        try:
            events.put(TaskEvent("result", func()))
        except Exception as exc:  # pragma: no cover - defensive GUI bridge
            events.put(TaskEvent("error", str(exc)))

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread

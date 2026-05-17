"""Append-only JSONL event log for pipeline runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class EventType(str, Enum):
    """Pipeline event categories consumed by the runner."""

    PIPELINE_START = "pipeline_start"
    PIPELINE_END = "pipeline_end"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    STAGE_FAIL = "stage_fail"


@dataclass(frozen=True)
class PipelineEvent:
    """A single pipeline event payload."""

    type: EventType
    timestamp: str
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type.value
        payload.update(payload.pop("data"))
        return payload


def create_event(event_type: EventType, run_id: str, **data: Any) -> PipelineEvent:
    """Create a timestamped pipeline event."""

    return PipelineEvent(
        type=event_type,
        timestamp=datetime.now(UTC).isoformat(),
        run_id=run_id,
        data=data,
    )


class EventLog:
    """Append events to ``events.jsonl`` under a run directory."""

    def __init__(self, log_dir: str | Path, filename: str = "events.jsonl") -> None:
        self.log_dir = Path(log_dir)
        self.path = self.log_dir / filename
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: PipelineEvent | dict[str, Any]) -> Path:
        payload = event.to_dict() if isinstance(event, PipelineEvent) else dict(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return self.path

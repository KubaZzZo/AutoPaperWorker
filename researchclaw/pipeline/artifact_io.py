"""Pipeline artifact lookup and stage metadata I/O helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from researchclaw.pipeline.stages import NEXT_STAGE, Stage, StageStatus

logger = logging.getLogger(__name__)


def utcnow_iso() -> str:
    """Return the current UTC time in the pipeline metadata format."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_stage_meta(
    stage_dir: Path,
    stage: Stage,
    run_id: str,
    result: Any,
    *,
    timestamp_factory: Callable[[], str] = utcnow_iso,
) -> None:
    if result.status is StageStatus.DONE:
        next_stage = NEXT_STAGE[stage]
    else:
        next_stage = stage
    meta = {
        "stage_id": f"{int(stage):02d}-{stage.name.lower()}",
        "run_id": run_id,
        "status": result.status.value,
        "decision": result.decision,
        "output_artifacts": list(result.artifacts),
        "evidence_refs": list(result.evidence_refs),
        "error": result.error,
        "ts": timestamp_factory(),
        "next_stage": int(next_stage) if next_stage is not None else None,
    }
    (stage_dir / "decision.json").write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )


def read_best_analysis(run_dir: Path) -> str:
    """Read analysis.md from the promoted best Stage 14 iteration when present."""
    best = run_dir / "analysis_best.md"
    if best.exists():
        return best.read_text(encoding="utf-8")
    return read_prior_artifact(run_dir, "analysis.md") or ""


def read_prior_artifact(
    run_dir: Path,
    filename: str,
    *,
    diagnostic_logger: logging.Logger | None = None,
) -> str | None:
    log = diagnostic_logger or logger
    for stage_subdir in sorted(run_dir.glob("stage-*"), key=_stage_sort_key, reverse=True):
        candidate = stage_subdir / filename
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                log.warning("Cannot read %s: %s - skipping", candidate, exc)
                continue
        if filename.endswith("/") and (stage_subdir / filename.rstrip("/")).is_dir():
            return str(stage_subdir / filename.rstrip("/"))
    return None


def find_prior_file(run_dir: Path, filename: str) -> Path | None:
    """Return the newest prior artifact path without reading its content."""
    for stage_subdir in sorted(run_dir.glob("stage-*"), key=_stage_sort_key, reverse=True):
        candidate = stage_subdir / filename
        if candidate.is_file():
            return candidate
    return None


def load_hardware_profile(
    run_dir: Path,
    *,
    diagnostic_logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """Load hardware_profile.json from a prior stage when available."""
    raw = read_prior_artifact(
        run_dir,
        "hardware_profile.json",
        diagnostic_logger=diagnostic_logger,
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _stage_sort_key(p: Path) -> tuple[str, int]:
    name = p.name
    if "_v" in name:
        base, _, ver = name.rpartition("_v")
        try:
            return (base, -int(ver))
        except ValueError:
            return (name, -999)
    return (name, 0)

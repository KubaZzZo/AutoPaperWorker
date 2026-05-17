from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.run_state import DEFAULT_RUN_STATE_BACKEND, RunStateBackend

if TYPE_CHECKING:
    from researchclaw.pipeline.executor import StageResult

logger = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_total_cost_usd(run_dir: Path) -> float:
    cost_log = run_dir / "cost_log.jsonl"
    if not cost_log.exists():
        return 0.0
    total = 0.0
    try:
        for line in cost_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if isinstance(entry, dict):
                total += float(entry.get("cost_usd") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return total
    return total


def _relative_artifact_path(run_dir: Path, value: object) -> str | None:
    if not value:
        return None
    path = Path(str(value))
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except (OSError, ValueError):
        try:
            return path.relative_to(run_dir).as_posix()
        except ValueError:
            return path.as_posix()


def _collect_experiment_run_progress(run_dir: Path) -> list[dict[str, object]]:
    runs_dir = run_dir / "stage-12" / "runs"
    if not runs_dir.exists():
        return []

    runs: list[dict[str, object]] = []
    for run_path in sorted(runs_dir.glob("run-*.json")):
        try:
            payload = json.loads(run_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "Failed to read experiment run progress %s: %s",
                run_path,
                exc,
                exc_info=True,
            )
            continue
        if not isinstance(payload, dict):
            continue

        run: dict[str, object] = {
            "run_id": str(payload.get("run_id") or run_path.stem),
            "status": str(payload.get("status") or "unknown"),
        }
        elapsed = payload.get("elapsed_sec")
        if isinstance(elapsed, int | float):
            run["elapsed_sec"] = round(float(elapsed), 3)

        for key in ("stdout_log", "stderr_log"):
            rel_path = _relative_artifact_path(run_dir, payload.get(key))
            if rel_path is not None:
                run[key] = rel_path

        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            run["metrics"] = metrics

        updated_at = payload.get("completed_at") or payload.get("updated_at")
        if updated_at:
            run["updated_at"] = str(updated_at)

        runs.append(run)
    return runs


def write_progress_snapshot(
    *,
    run_dir: Path,
    run_id: str,
    results: list[StageResult],
    current_stage: Stage,
    total_stages: int,
    status: str = "running",
    elapsed_sec: float | None = None,
    run_state_backend: RunStateBackend = DEFAULT_RUN_STATE_BACKEND,
) -> None:
    """Write a single-file progress snapshot for dashboards and monitors."""
    cost_summary: dict[str, object] | None = None
    try:
        from researchclaw.cost_tracker import write_cost_summary

        cost_summary_path = write_cost_summary(run_dir)
        cost_summary = json.loads(cost_summary_path.read_text(encoding="utf-8"))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        cost_summary = None

    last = results[-1] if results else None
    done_count = sum(1 for item in results if item.status == StageStatus.DONE)
    failed_count = sum(1 for item in results if item.status == StageStatus.FAILED)
    paused_count = sum(1 for item in results if item.status == StageStatus.PAUSED)
    blocked_count = sum(
        1 for item in results if item.status == StageStatus.BLOCKED_APPROVAL
    )
    payload: dict[str, object] = {
        "run_id": run_id,
        "status": status,
        "current_stage": int(current_stage),
        "current_stage_name": current_stage.name,
        "total_stages": total_stages,
        "stages_done": done_count,
        "stages_failed": failed_count,
        "stages_paused": paused_count,
        "stages_blocked": blocked_count,
        "percent": round(int(current_stage) / total_stages * 100, 1)
        if total_stages
        else 0.0,
        "cost_usd": round(_read_total_cost_usd(run_dir), 6),
        "updated_at": utcnow_iso(),
    }
    if cost_summary is not None:
        payload["cost_summary"] = cost_summary
    if elapsed_sec is not None:
        payload["elapsed_sec"] = round(elapsed_sec, 3)
    experiment_runs = _collect_experiment_run_progress(run_dir)
    if experiment_runs:
        payload["experiment_runs"] = experiment_runs
    if last is not None:
        event_type = "stage_end" if last.status == StageStatus.DONE else "stage_fail"
        payload["last_event"] = {
            "type": event_type,
            "stage": int(last.stage),
            "stage_name": last.stage.name,
            "status": last.status.value,
            "decision": last.decision,
            "error": last.error,
            "artifacts": list(last.artifacts),
        }
    run_state_backend.write_progress(run_dir, payload)

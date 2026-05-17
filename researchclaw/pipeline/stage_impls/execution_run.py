"""Stage 12 sandbox run result persistence and status classification."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from researchclaw.pipeline.contracts import ExperimentRunContract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxRunPersistence:
    """Summary of persisted Stage 12 sandbox run outputs."""

    run_status: str
    effective_metrics: dict[str, Any]
    structured_results: dict[str, Any] | None


def persist_sandbox_run_result(
    *,
    stage_dir: Path,
    runs_dir: Path,
    result: Any,
    stdout_log_path: Path,
    stderr_log_path: Path,
    time_budget_sec: int | float,
    parse_metrics: Callable[[str], dict[str, Any]],
    timestamp_factory: Callable[[], str],
    diagnostic_logger: logging.Logger | None = None,
    environment: dict[str, Any] | None = None,
) -> SandboxRunPersistence:
    """Persist sandbox result artifacts and classify the run status."""
    log = diagnostic_logger or logger
    structured_results = _load_structured_results(runs_dir, log)
    effective_metrics = result.metrics
    if not effective_metrics and result.stdout:
        effective_metrics = parse_metrics(result.stdout)

    stdout_has_failure = bool(
        result.stdout
        and not effective_metrics
        and any(
            sig in result.stdout
            for sig in ("FAIL:", "NaN/divergence", "Traceback (most recent")
        )
    )
    if result.returncode == 0 and not result.timed_out and not stdout_has_failure:
        run_status = "completed"
    elif result.timed_out and effective_metrics:
        run_status = "partial"
        log.warning(
            "Experiment timed out but captured %d partial metrics",
            len(effective_metrics),
        )
    else:
        run_status = "failed"
        if stdout_has_failure:
            log.warning("Experiment exited cleanly but stdout contains failure signals")

    if run_status == "completed" and result.elapsed_sec and result.elapsed_sec < 5.0:
        log.warning(
            "Stage 12: Experiment completed in %.2fs - benchmark may be trivially easy. "
            "Consider increasing task difficulty.",
            result.elapsed_sec,
        )

    run_payload = ExperimentRunContract(
        run_id="run-1",
        task_id="sandbox-main",
        status=run_status,
        metrics=effective_metrics,
        elapsed_sec=result.elapsed_sec,
        stdout=result.stdout,
        stderr=result.stderr,
        stdout_log=str(stdout_log_path),
        stderr_log=str(stderr_log_path),
        timed_out=result.timed_out,
        completed_at=timestamp_factory(),
        environment=environment or {},
        structured_results=structured_results,
    ).to_payload()
    if structured_results is None and effective_metrics:
        auto_results = {"source": "stdout_parsed", "metrics": effective_metrics}
        (runs_dir / "results.json").write_text(
            json.dumps(auto_results, indent=2),
            encoding="utf-8",
        )
        log.info(
            "Stage 12: Auto-generated results.json from stdout metrics (%d keys)",
            len(effective_metrics),
        )
    (runs_dir / "run-1.json").write_text(
        json.dumps(run_payload, indent=2),
        encoding="utf-8",
    )

    _write_time_budget_warning(
        stage_dir=stage_dir,
        result=result,
        time_budget_sec=time_budget_sec,
        log=log,
    )
    _warn_low_seed_counts(structured_results, log)

    return SandboxRunPersistence(
        run_status=run_status,
        effective_metrics=effective_metrics,
        structured_results=structured_results,
    )


def _load_structured_results(
    runs_dir: Path,
    log: logging.Logger,
) -> dict[str, Any] | None:
    results_json_path = runs_dir / "sandbox" / "_project" / "results.json"
    if not results_json_path.exists():
        return None
    try:
        results_text = results_json_path.read_text(encoding="utf-8")
        structured_results = json.loads(results_text)
        (runs_dir / "results.json").write_text(results_text, encoding="utf-8")
        return structured_results if isinstance(structured_results, dict) else None
    except (json.JSONDecodeError, OSError):
        log.debug(
            "Failed to read sandbox structured results: %s",
            results_json_path,
            exc_info=True,
        )
        return None


def _write_time_budget_warning(
    *,
    stage_dir: Path,
    result: Any,
    time_budget_sec: int | float,
    log: logging.Logger,
) -> None:
    if not (
        result.timed_out
        or (result.elapsed_sec and result.elapsed_sec > time_budget_sec * 0.9)
    ):
        return

    stdout = result.stdout or ""
    completed_conditions = set()
    completed_seeds = 0
    for line in stdout.splitlines():
        if "condition=" in line and "seed=" in line:
            completed_seeds += 1
            cond_match = re.match(r".*condition=(\S+)", line)
            if cond_match:
                completed_conditions.add(cond_match.group(1))
    time_budget_warning = {
        "timed_out": result.timed_out,
        "elapsed_sec": result.elapsed_sec,
        "budget_sec": time_budget_sec,
        "conditions_completed": sorted(completed_conditions),
        "total_seed_runs": completed_seeds,
        "warning": (
            f"Experiment used {result.elapsed_sec:.0f}s of "
            f"{time_budget_sec}s budget. "
            f"Only {len(completed_conditions)} conditions completed "
            f"({completed_seeds} seed-runs). Consider increasing "
            f"time_budget_sec for more complete results."
        ),
    }
    log.warning("Stage 12: %s", time_budget_warning["warning"])
    (stage_dir / "time_budget_warning.json").write_text(
        json.dumps(time_budget_warning, indent=2),
        encoding="utf-8",
    )


def _warn_low_seed_counts(
    structured_results: dict[str, Any] | None,
    log: logging.Logger,
) -> None:
    if not structured_results:
        return
    sr_conditions = structured_results.get(
        "conditions",
        structured_results.get("per_condition", {}),
    )
    if not isinstance(sr_conditions, dict):
        return
    for cname, cdata in sr_conditions.items():
        if isinstance(cdata, dict):
            seeds_run = cdata.get("seeds_run", cdata.get("n_seeds", 0))
            if isinstance(seeds_run, (int, float)) and 0 < seeds_run < 3:
                log.warning(
                    "Stage 12: Condition '%s' ran only %d seed(s) - "
                    "minimum 3 required for statistical validity",
                    cname,
                    int(seeds_run),
                )

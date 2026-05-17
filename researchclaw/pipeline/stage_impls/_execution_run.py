"""Stages 11-12: resource planning and experiment execution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _chat_with_prompt,
    _ensure_sandbox_deps,
    _get_evolution_overlay,
    _parse_metrics_from_stdout,
    _read_prior_artifact,
    _safe_filename,
    _safe_json_loads,
    _utcnow_iso,
)
from researchclaw.pipeline.stage_impls.execution_run import persist_sandbox_run_result
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger("researchclaw.pipeline.stage_impls._execution")

def _execute_resource_planning(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    exp_plan = _read_prior_artifact(run_dir, "exp_plan.yaml") or ""
    schedule: dict[str, Any] | None = None
    if llm is not None:
        _pm = prompts or PromptManager()
        _overlay = _get_evolution_overlay(run_dir, "resource_planning")
        sp = _pm.for_stage("resource_planning", evolution_overlay=_overlay, exp_plan=exp_plan)
        resp = _chat_with_prompt(
            llm,
            sp.system,
            sp.user,
            json_mode=sp.json_mode,
            max_tokens=sp.max_tokens,
        )
        parsed = _safe_json_loads(resp.content, {})
        if isinstance(parsed, dict):
            schedule = parsed
    if schedule is None:
        schedule = {
            "tasks": [
                {
                    "id": "baseline",
                    "name": "Run baseline",
                    "depends_on": [],
                    "gpu_count": 1,
                    "estimated_minutes": 20,
                    "priority": "high",
                },
                {
                    "id": "proposed",
                    "name": "Run proposed method",
                    "depends_on": ["baseline"],
                    "gpu_count": 1,
                    "estimated_minutes": 30,
                    "priority": "high",
                },
            ],
            "total_gpu_budget": 1,
            "generated": _utcnow_iso(),
        }
    schedule.setdefault("generated", _utcnow_iso())
    (stage_dir / "schedule.json").write_text(
        json.dumps(schedule, indent=2), encoding="utf-8"
    )
    return StageResult(
        stage=Stage.RESOURCE_PLANNING,
        status=StageStatus.DONE,
        artifacts=("schedule.json",),
        evidence_refs=("stage-11/schedule.json",),
    )


def _execute_experiment_run(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
    cancel_event: Any | None = None,
) -> StageResult:
    from researchclaw.experiment.factory import create_sandbox
    from researchclaw.experiment.runner import ExperimentRunner

    schedule_text = _read_prior_artifact(run_dir, "schedule.json") or "{}"
    # Try multi-file experiment directory first, fall back to single file
    exp_dir_path = _read_prior_artifact(run_dir, "experiment/")
    code_text = ""
    if exp_dir_path and Path(exp_dir_path).is_dir():
        main_path = Path(exp_dir_path) / "main.py"
        if main_path.exists():
            try:
                code_text = main_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.debug(
                    "Failed to read multi-file experiment entry point: %s",
                    main_path,
                    exc_info=True,
                )
                code_text = ""
    if not code_text:
        code_text = _read_prior_artifact(run_dir, "experiment.py") or ""

    runs_dir = stage_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    mode = config.experiment.mode
    if mode in ("sandbox", "docker"):
        # P7: Auto-install missing dependencies before subprocess sandbox
        if mode == "sandbox":
            _all_code = code_text
            if exp_dir_path and Path(exp_dir_path).is_dir():
                for _pyf in Path(exp_dir_path).glob("*.py"):
                    try:
                        _all_code += "\n" + _pyf.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        logger.debug(
                            "Failed to read experiment file during dependency scan: %s",
                            _pyf,
                            exc_info=True,
                        )
            _ensure_sandbox_deps(_all_code, config.experiment.sandbox.python_path)

        sandbox = create_sandbox(config.experiment, runs_dir / "sandbox")
        stdout_log_path = runs_dir / "run-1.stdout.log"
        stderr_log_path = runs_dir / "run-1.stderr.log"
        # Use run_project for multi-file, run for single-file
        if exp_dir_path and Path(exp_dir_path).is_dir():
            result = sandbox.run_project(
                Path(exp_dir_path),
                timeout_sec=config.experiment.time_budget_sec,
                cancel_event=cancel_event,
                stdout_path=stdout_log_path,
                stderr_path=stderr_log_path,
            )
        else:
            result = sandbox.run(
                code_text,
                timeout_sec=config.experiment.time_budget_sec,
                cancel_event=cancel_event,
                stdout_path=stdout_log_path,
                stderr_path=stderr_log_path,
            )
        _env_info: dict[str, Any] = {}
        try:
            from researchclaw.experiment.environment import (
                capture_environment,
                write_reproducibility_artifacts,
            )
            _env_info = capture_environment()
            _artifacts = write_reproducibility_artifacts(stage_dir, _env_info)
            logger.info(
                "Stage 12: Reproducibility artifacts - %s",
                list(_artifacts.keys()),
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.debug("Environment capture failed (non-fatal)", exc_info=True)

        persist_sandbox_run_result(
            stage_dir=stage_dir,
            runs_dir=runs_dir,
            result=result,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            time_budget_sec=config.experiment.time_budget_sec,
            parse_metrics=_parse_metrics_from_stdout,
            timestamp_factory=_utcnow_iso,
            diagnostic_logger=logger,
            environment=_env_info,
        )
    elif mode == "simulated":
        schedule = _safe_json_loads(schedule_text, {})
        tasks = schedule.get("tasks", []) if isinstance(schedule, dict) else []
        if not isinstance(tasks, list):
            tasks = []
        for idx, task in enumerate(tasks or [{"id": "task-1", "name": "simulated"}]):
            task_id = (
                str(task.get("id", f"task-{idx + 1}"))
                if isinstance(task, dict)
                else f"task-{idx + 1}"
            )
            payload = {
                "run_id": f"run-{idx + 1}",
                "task_id": task_id,
                "status": "simulated",
                "key_metrics": {
                    config.experiment.metric_key: round(0.3 + idx * 0.03, 4),
                    "secondary_metric": round(0.6 - idx * 0.04, 4),
                },
                "notes": "Simulated run result",
                "completed_at": _utcnow_iso(),
            }
            run_id = str(payload["run_id"])
            (runs_dir / f"{_safe_filename(run_id)}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
    else:
        runner = ExperimentRunner(config.experiment, runs_dir / "workspace")
        history = runner.run_loop(code_text, run_id=f"exp-{run_dir.name}", llm=llm)
        runner.save_history(stage_dir / "experiment_history.json")
        for item in history.results:
            payload = {
                "run_id": f"run-{item.iteration}",
                "task_id": item.run_id,
                "status": "completed" if item.error is None else "failed",
                "metrics": item.metrics,
                "primary_metric": item.primary_metric,
                "improved": item.improved,
                "kept": item.kept,
                "elapsed_sec": item.elapsed_sec,
                "error": item.error,
                "completed_at": _utcnow_iso(),
            }
            run_id = str(payload["run_id"])
            (runs_dir / f"{_safe_filename(run_id)}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
    return StageResult(
        stage=Stage.EXPERIMENT_RUN,
        status=StageStatus.DONE,
        artifacts=("runs/",),
        evidence_refs=("stage-12/runs/",),
    )


__all__ = ["_execute_experiment_run", "_execute_resource_planning"]

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.pipeline.executor import StageResult
from researchclaw.pipeline.stages import STAGE_SEQUENCE, Stage, StageStatus

StageExecutor = Callable[..., StageResult]


def prepare_parallel_hypothesis_branches(run_dir: Path) -> Path | None:
    """Create branch input directories from Stage 8 hypothesis branch plan."""
    plan_path = run_dir / "stage-08" / "hypothesis_branches.json"
    if not plan_path.exists():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return None

    branches = plan.get("branches")
    if not isinstance(branches, list) or not branches:
        return None

    branches_root = run_dir / "branches"
    branches_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "selection_metric": plan.get("selection_metric", "primary_metric"),
        "status": "prepared",
        "branches": [],
    }
    manifest_branches: list[dict[str, object]] = []
    for item in branches:
        if not isinstance(item, dict):
            continue
        branch_id = str(item.get("branch_id", "")).strip()
        hypothesis = str(item.get("hypothesis", "")).strip()
        if not branch_id or not hypothesis:
            continue
        branch_dir = branches_root / branch_id
        branch_stage_dir = branch_dir / "stage-08"
        branch_stage_dir.mkdir(parents=True, exist_ok=True)
        (branch_stage_dir / "hypotheses.md").write_text(
            hypothesis + "\n",
            encoding="utf-8",
        )
        branch_meta = {
            "branch_id": branch_id,
            "rank": item.get("rank"),
            "hypothesis": hypothesis,
            "status": "prepared",
            "stage_range": [9, 15],
        }
        (branch_dir / "branch.json").write_text(
            json.dumps(branch_meta, indent=2),
            encoding="utf-8",
        )
        manifest_branches.append(branch_meta)

    if not manifest_branches:
        return None
    manifest["branches"] = manifest_branches
    manifest_path = branches_root / "branch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def branch_stage_range() -> tuple[Stage, ...]:
    return tuple(
        stage
        for stage in STAGE_SEQUENCE
        if Stage.EXPERIMENT_DESIGN <= stage <= Stage.RESEARCH_DECISION
    )


def copy_branch_context(run_dir: Path, branch_dir: Path) -> None:
    """Copy main run context needed before branch-local experiment stages."""
    for path in run_dir.iterdir():
        if path.name == "branches":
            continue
        if not path.name.startswith("stage-"):
            continue
        try:
            stage_num = int(path.name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        if stage_num >= int(Stage.EXPERIMENT_DESIGN):
            continue
        target = branch_dir / path.name
        if target.exists():
            continue
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)


def metric_from_mapping(data: object, metric_key: str) -> float | None:
    if not isinstance(data, dict):
        return None
    direct = data.get(metric_key)
    if isinstance(direct, int | float):
        return float(direct)
    if isinstance(direct, dict):
        for key in ("mean", "max", "min", "value"):
            value = direct.get(key)
            if isinstance(value, int | float):
                return float(value)
    metrics_summary = data.get("metrics_summary")
    if isinstance(metrics_summary, dict):
        metric_data = metrics_summary.get(metric_key)
        if isinstance(metric_data, int | float):
            return float(metric_data)
        if isinstance(metric_data, dict):
            for key in ("mean", "max", "min", "value"):
                value = metric_data.get(key)
                if isinstance(value, int | float):
                    return float(value)
    condition_summaries = data.get("condition_summaries")
    if isinstance(condition_summaries, dict):
        values: list[float] = []
        for summary in condition_summaries.values():
            if not isinstance(summary, dict):
                continue
            metrics = summary.get("metrics")
            if isinstance(metrics, dict):
                value = metrics.get(metric_key)
                if isinstance(value, int | float):
                    values.append(float(value))
        if values:
            return sum(values) / len(values)
    return None


def read_branch_selection_score(branch_dir: Path, metric_key: str) -> float | None:
    for path in (
        branch_dir / "results.json",
        branch_dir / f"stage-{int(Stage.RESULT_ANALYSIS):02d}" / "experiment_summary.json",
    ):
        if not path.exists():
            continue
        try:
            score = metric_from_mapping(
                json.loads(path.read_text(encoding="utf-8")),
                metric_key,
            )
        except (json.JSONDecodeError, OSError):
            continue
        if score is not None:
            return score
    return None


def promote_branch_outputs(branch_dir: Path, run_dir: Path) -> None:
    """Promote branch-local experiment outputs for downstream paper stages."""
    for stage in branch_stage_range():
        src = branch_dir / f"stage-{int(stage):02d}"
        if not src.exists():
            continue
        dst = run_dir / src.name
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    for name in (
        "results.json",
        "experiment_summary.json",
        "verified_registry.json",
    ):
        src = branch_dir / name
        if src.exists() and src.is_file():
            shutil.copy2(src, run_dir / name)


def execute_parallel_hypothesis_branches(
    *,
    run_dir: Path,
    run_id: str,
    config: RCConfig,
    adapters: AdapterBundle,
    auto_approve_gates: bool,
    cancel_event: threading.Event | None,
    execute_stage: StageExecutor,
) -> Path | None:
    """Run Stage 9-15 for prepared hypothesis branches and promote the best."""
    manifest_path = run_dir / "branches" / "branch_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    branches = manifest.get("branches")
    if not isinstance(branches, list) or len(branches) < 2:
        return None

    metric_key = str(
        manifest.get("selection_metric")
        or getattr(config.experiment, "metric_key", "primary_metric")
        or "primary_metric"
    )
    metric_direction = getattr(config.experiment, "metric_direction", "maximize")
    maximize = metric_direction != "minimize"
    completed: list[dict[str, object]] = []

    for branch in branches:
        if cancel_event is not None and cancel_event.is_set():
            break
        if not isinstance(branch, dict):
            continue
        branch_id = str(branch.get("branch_id", "")).strip()
        if not branch_id:
            continue
        branch_dir = run_dir / "branches" / branch_id
        if not branch_dir.is_dir():
            continue
        copy_branch_context(run_dir, branch_dir)
        branch["status"] = "running"
        branch_results: list[dict[str, object]] = []
        for stage in branch_stage_range():
            result = execute_stage(
                stage,
                run_dir=branch_dir,
                run_id=f"{run_id}:{branch_id}",
                config=config,
                adapters=adapters,
                auto_approve_gates=auto_approve_gates,
                cancel_event=cancel_event,
            )
            branch_results.append({
                "stage": int(stage),
                "name": stage.name,
                "status": result.status.value,
                "artifacts": list(result.artifacts),
                "error": result.error,
            })
            if result.status != StageStatus.DONE:
                branch["status"] = result.status.value
                break
        else:
            branch["status"] = "completed"

        score = read_branch_selection_score(branch_dir, metric_key)
        branch["selection_score"] = score
        branch["stage_results"] = branch_results
        (branch_dir / "branch.json").write_text(
            json.dumps(branch, indent=2),
            encoding="utf-8",
        )
        if branch.get("status") == "completed" and score is not None:
            completed.append(branch)

    if not completed:
        manifest["status"] = "completed_no_selection"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return None

    best = sorted(
        completed,
        key=lambda item: float(item.get("selection_score", 0.0)),
        reverse=maximize,
    )[0]
    best_branch_id = str(best["branch_id"])
    manifest["status"] = "completed"
    manifest["best_branch_id"] = best_branch_id
    manifest["selection_metric"] = metric_key
    manifest["metric_direction"] = metric_direction
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    promote_branch_outputs(run_dir / "branches" / best_branch_id, run_dir)
    return manifest_path

"""Small helpers for Stage 13 iterative refinement."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from researchclaw.config import RCConfig
from researchclaw.pipeline._helpers import StageResult, _utcnow_iso
from researchclaw.pipeline.stages import Stage, StageStatus

logger = logging.getLogger("researchclaw.pipeline.stage_impls._execution")


def _write_project(target_dir: Path, project_files: dict[str, str]) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for fname, code in project_files.items():
        (target_dir / fname).write_text(code, encoding="utf-8")


def _files_to_context(project_files: dict[str, str]) -> str:
    parts = []
    for fname, code in sorted(project_files.items()):
        parts.append(f"```filename:{fname}\n{code}\n```")
    return "\n\n".join(parts)


def _handle_simulated_iterative_refine(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
) -> StageResult:
    logger.info(
        "Stage 13: Skipping iterative refinement in simulated mode "
        "(no real code execution available)"
    )
    final_dir = stage_dir / "experiment_final"
    for stage_num in (12, 10):
        src_dir = run_dir / f"stage-{stage_num:02d}" / "experiment"
        if src_dir.is_dir():
            if final_dir.exists():
                shutil.rmtree(final_dir)
            shutil.copytree(src_dir, final_dir)
            break
        src_file = run_dir / f"stage-{stage_num:02d}" / "experiment.py"
        if src_file.is_file():
            (stage_dir / "experiment_final.py").write_text(
                src_file.read_text(encoding="utf-8"), encoding="utf-8"
            )
            break

    log: dict[str, Any] = {
        "generated": _utcnow_iso(),
        "mode": "simulated",
        "skipped": True,
        "skip_reason": "Iterative refinement not meaningful in simulated mode",
        "metric_key": config.experiment.metric_key,
    }
    (stage_dir / "refinement_log.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8"
    )
    return StageResult(
        stage=Stage.ITERATIVE_REFINE,
        status=StageStatus.DONE,
        artifacts=("refinement_log.json",),
        evidence_refs=(),
    )


def _handle_llm_unavailable_iterative_refine(
    stage_dir: Path,
    best_files: dict[str, str],
    best_metric: float | None,
    log: dict[str, Any],
) -> StageResult:
    logger.info("Stage 13: LLM unavailable, saving original experiment as final")
    final_dir = stage_dir / "experiment_final"
    _write_project(final_dir, best_files)
    if "main.py" in best_files:
        (stage_dir / "experiment_final.py").write_text(
            best_files["main.py"], encoding="utf-8"
        )
    log.update(
        {
            "converged": True,
            "stop_reason": "llm_unavailable",
            "best_metric": best_metric,
            "best_version": "experiment_final/",
            "iterations": [
                {
                    "iteration": 0,
                    "version_dir": "experiment_final/",
                    "source": "fallback_original",
                    "metric": best_metric,
                }
            ],
        }
    )
    (stage_dir / "refinement_log.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8"
    )
    artifacts = ("refinement_log.json", "experiment_final/")
    return StageResult(
        stage=Stage.ITERATIVE_REFINE,
        status=StageStatus.DONE,
        artifacts=artifacts,
        evidence_refs=tuple(f"stage-13/{a}" for a in artifacts),
    )


__all__ = [
    "_files_to_context",
    "_handle_llm_unavailable_iterative_refine",
    "_handle_simulated_iterative_refine",
    "_write_project",
]

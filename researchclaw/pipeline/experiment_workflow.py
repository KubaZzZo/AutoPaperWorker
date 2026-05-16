from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from researchclaw.config import RCConfig
from researchclaw.pipeline.progress import utcnow_iso

ProgressReporter = Callable[[str], None]

logger = logging.getLogger(__name__)


def _report_progress(reporter: ProgressReporter | None, message: str) -> None:
    if reporter is not None:
        reporter(message)


def run_experiment_diagnosis(
    run_dir: Path,
    config: RCConfig,
    run_id: str,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> None:
    """Run experiment diagnosis after Stage 14 and save reports."""
    try:
        from researchclaw.pipeline.experiment_diagnosis import (
            assess_experiment_quality,
            diagnose_experiment,
        )

        summary_path = None
        for candidate in sorted(run_dir.glob("stage-14*/experiment_summary.json")):
            summary_path = candidate
        if not summary_path or not summary_path.exists():
            return

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        stdout, stderr = "", ""
        runs_dir = None
        for candidate_runs in sorted(run_dir.glob("stage-1[23]*/runs"), reverse=True):
            if candidate_runs.is_dir():
                runs_dir = candidate_runs
                break
        if runs_dir is None:
            runs_dir = summary_path.parent / "runs"
        if runs_dir.is_dir():
            for run_file in sorted(runs_dir.glob("*.json"))[:5]:
                try:
                    run_data = json.loads(run_file.read_text(encoding="utf-8"))
                    if isinstance(run_data, dict):
                        stdout += run_data.get("stdout", "") + "\n"
                        stderr += run_data.get("stderr", "") + "\n"
                except (json.JSONDecodeError, OSError):
                    continue

        plan = None
        for candidate in sorted(run_dir.glob("stage-09*/exp_plan.yaml")):
            try:
                import yaml as _yaml_diag

                plan = _yaml_diag.safe_load(candidate.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, UnicodeError):
                logger.warning(
                    "Experiment diagnosis plan YAML load failed: %s",
                    candidate,
                    exc_info=True,
                )
        if plan is None:
            for candidate in sorted(run_dir.glob("stage-09*/experiment_design.json")):
                try:
                    plan = json.loads(candidate.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug(
                        "Failed to read experiment design JSON for diagnosis from %s: %s",
                        candidate,
                        exc,
                        exc_info=True,
                    )

        ref_log = None
        for candidate in sorted(run_dir.glob("stage-13*/refinement_log.json")):
            try:
                ref_log = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug(
                    "Failed to read refinement log for diagnosis from %s: %s",
                    candidate,
                    exc,
                    exc_info=True,
                )

        diag = diagnose_experiment(
            experiment_summary=summary,
            experiment_plan=plan,
            refinement_log=ref_log,
            stdout=stdout.strip(),
            stderr=stderr.strip(),
        )
        qa = assess_experiment_quality(summary, ref_log)

        diag_report = {
            "diagnosis": diag.to_dict(),
            "quality_assessment": {
                "mode": qa.mode.value,
                "sufficient": qa.sufficient,
                "repair_possible": qa.repair_possible,
                "deficiency_types": [d.type.value for d in qa.deficiencies],
            },
            "repair_needed": not qa.sufficient,
            "generated": utcnow_iso(),
        }
        (run_dir / "experiment_diagnosis.json").write_text(
            json.dumps(diag_report, indent=2), encoding="utf-8"
        )

        if not qa.sufficient:
            from researchclaw.pipeline.experiment_repair import build_repair_prompt

            code: dict[str, str] = {}
            for glob_pattern in (
                "stage-13*/experiment_final/*.py",
                "stage-10*/experiment/*.py",
                "stage-10*/*.py",
            ):
                for candidate in sorted(run_dir.glob(glob_pattern)):
                    try:
                        code[candidate.name] = candidate.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        logger.debug(
                            "Failed to read experiment code for repair prompt from %s: %s",
                            candidate,
                            exc,
                            exc_info=True,
                        )
                if code:
                    break

            repair_prompt = build_repair_prompt(
                diag, code, time_budget_sec=config.experiment.time_budget_sec
            )
            (run_dir / "repair_prompt.txt").write_text(
                repair_prompt, encoding="utf-8"
            )
            logger.info(
                "[%s] Experiment diagnosis: mode=%s, deficiencies=%d - repair prompt saved",
                run_id,
                qa.mode.value,
                len(diag.deficiencies),
            )
            _report_progress(
                progress_reporter,
                f"[{run_id}] Experiment diagnosis: {qa.mode.value} "
                f"({len(diag.deficiencies)} issues found, repair needed)",
            )
        else:
            logger.info(
                "[%s] Experiment diagnosis: mode=%s, sufficient=True - quality OK",
                run_id,
                qa.mode.value,
            )
            _report_progress(
                progress_reporter,
                f"[{run_id}] Experiment diagnosis: {qa.mode.value} - quality OK",
            )

    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("Experiment diagnosis failed: %s", exc, exc_info=True)


def run_experiment_repair(
    run_dir: Path,
    config: RCConfig,
    run_id: str,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> None:
    """Execute the experiment repair loop when diagnosis finds quality issues."""
    try:
        from researchclaw.pipeline.experiment_repair import run_repair_loop

        repair_result = run_repair_loop(
            run_dir=run_dir,
            config=config,
            run_id=run_id,
            progress_reporter=progress_reporter,
        )

        (run_dir / "experiment_repair_result.json").write_text(
            json.dumps(repair_result.to_dict(), indent=2), encoding="utf-8"
        )

        if repair_result.best_experiment_summary:
            from researchclaw.pipeline.experiment_repair import (
                _summary_quality_score,
            )

            best_path = run_dir / "stage-14" / "experiment_summary.json"
            existing_score = 0.0
            if best_path.exists():
                try:
                    existing = json.loads(best_path.read_text(encoding="utf-8"))
                    existing_score = _summary_quality_score(existing)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug(
                        "Failed to read existing stage-14 summary before repair promotion from %s: %s",
                        best_path,
                        exc,
                        exc_info=True,
                    )

            repair_score = _summary_quality_score(
                repair_result.best_experiment_summary
            )

            if repair_score > existing_score:
                best_path.write_text(
                    json.dumps(repair_result.best_experiment_summary, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "[%s] Promoted repair results to stage-14 "
                    "(score %.1f > %.1f, success=%s)",
                    run_id,
                    repair_score,
                    existing_score,
                    repair_result.success,
                )
            else:
                logger.info(
                    "[%s] Kept existing stage-14 summary (score %.1f >= "
                    "repair score %.1f)",
                    run_id,
                    existing_score,
                    repair_score,
                )

        if repair_result.success:
            run_experiment_diagnosis(
                run_dir,
                config,
                run_id,
                progress_reporter=progress_reporter,
            )
        else:
            logger.info(
                "[%s] Repair loop completed without reaching full_paper quality "
                "(best mode: %s, %d cycles)",
                run_id,
                repair_result.final_mode.value,
                repair_result.total_cycles,
            )

    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("[%s] Experiment repair failed: %s", run_id, exc, exc_info=True)
        _report_progress(
            progress_reporter,
            f"[{run_id}] Experiment repair failed: {exc}",
        )

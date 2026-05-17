from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
import time as _time
from pathlib import Path
from typing import Callable

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.evolution import EvolutionStore, extract_lessons
from researchclaw.knowledge.base import write_stage_to_kb
from researchclaw.pipeline.checkpoint import (
    read_checkpoint as _read_checkpoint,
    resume_from_checkpoint as _resume_from_checkpoint,
    write_checkpoint,
    write_heartbeat,
)
from researchclaw.pipeline.executor import StageResult, execute_stage
from researchclaw.pipeline.experiment_workflow import (
    run_experiment_diagnosis,
    run_experiment_repair,
)
from researchclaw.pipeline.parallel_branches import (
    branch_stage_range,
    copy_branch_context,
    execute_parallel_hypothesis_branches,
    metric_from_mapping,
    prepare_parallel_hypothesis_branches,
    promote_branch_outputs,
    read_branch_selection_score,
)
from researchclaw.pipeline.deliverables import package_deliverables
from researchclaw.pipeline.progress import (
    utcnow_iso as _utcnow_iso,
    write_progress_snapshot,
)
from researchclaw.pipeline.summary import (
    build_pipeline_summary,
    collect_content_metrics,
    write_pipeline_summary,
)
from researchclaw.pipeline.stages import (
    DECISION_ROLLBACK,
    MAX_DECISION_PIVOTS,
    NONCRITICAL_STAGES,
    STAGE_SEQUENCE,
    Stage,
    StageStatus,
)


ProgressReporter = Callable[[str], None]

logger = logging.getLogger(__name__)


def _report_progress(reporter: ProgressReporter | None, message: str) -> None:
    if reporter is not None:
        reporter(message)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _should_start(stage: Stage, from_stage: Stage, started: bool) -> bool:
    if started:
        return True
    return stage == from_stage


def _build_pipeline_summary(
    *,
    run_id: str,
    results: list[StageResult],
    from_stage: Stage,
    run_dir: Path | None = None,
) -> dict[str, object]:
    return build_pipeline_summary(
        run_id=run_id,
        results=results,
        from_stage=from_stage,
        run_dir=run_dir,
    )


def _write_pipeline_summary(run_dir: Path, summary: dict[str, object]) -> None:
    write_pipeline_summary(run_dir, summary)



def _prepare_parallel_hypothesis_branches(run_dir: Path) -> Path | None:
    return prepare_parallel_hypothesis_branches(run_dir)


def _branch_stage_range() -> tuple[Stage, ...]:
    return branch_stage_range()


def _copy_branch_context(run_dir: Path, branch_dir: Path) -> None:
    copy_branch_context(run_dir, branch_dir)


def _metric_from_mapping(data: object, metric_key: str) -> float | None:
    return metric_from_mapping(data, metric_key)


def _read_branch_selection_score(branch_dir: Path, metric_key: str) -> float | None:
    return read_branch_selection_score(branch_dir, metric_key)


def _promote_branch_outputs(branch_dir: Path, run_dir: Path) -> None:
    promote_branch_outputs(branch_dir, run_dir)


def _execute_parallel_hypothesis_branches(
    *,
    run_dir: Path,
    run_id: str,
    config: RCConfig,
    adapters: AdapterBundle,
    auto_approve_gates: bool,
    cancel_event: "threading.Event | None",
) -> Path | None:
    return execute_parallel_hypothesis_branches(
        run_dir=run_dir,
        run_id=run_id,
        config=config,
        adapters=adapters,
        auto_approve_gates=auto_approve_gates,
        cancel_event=cancel_event,
        execute_stage=execute_stage,
    )


def _write_checkpoint(
    run_dir: Path, stage: Stage, run_id: str,
    adapters: "AdapterBundle | None" = None,
) -> None:
    write_checkpoint(run_dir, stage, run_id, adapters=adapters)


def _write_heartbeat(run_dir: Path, stage: Stage, run_id: str) -> None:
    write_heartbeat(run_dir, stage, run_id)


def read_checkpoint(run_dir: Path) -> Stage | None:
    return _read_checkpoint(run_dir)


def resume_from_checkpoint(
    run_dir: Path, default_stage: Stage = Stage.TOPIC_INIT
) -> Stage:
    return _resume_from_checkpoint(run_dir, default_stage)


def _collect_content_metrics(run_dir: Path | None) -> dict[str, object]:
    return collect_content_metrics(run_dir)



def _run_experiment_diagnosis(
    run_dir: Path,
    config: RCConfig,
    run_id: str,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> None:
    run_experiment_diagnosis(
        run_dir,
        config,
        run_id,
        progress_reporter=progress_reporter,
    )


def _run_experiment_repair(
    run_dir: Path,
    config: RCConfig,
    run_id: str,
    *,
    progress_reporter: ProgressReporter | None = None,
) -> None:
    run_experiment_repair(
        run_dir,
        config,
        run_id,
        progress_reporter=progress_reporter,
    )


def execute_pipeline(
    *,
    run_dir: Path,
    run_id: str,
    config: RCConfig,
    adapters: AdapterBundle,
    from_stage: Stage = Stage.TOPIC_INIT,
    to_stage: Stage | None = None,
    auto_approve_gates: bool = False,
    stop_on_gate: bool = False,
    skip_noncritical: bool = False,
    kb_root: Path | None = None,
    cancel_event: "threading.Event | None" = None,
    progress_reporter: ProgressReporter | None = None,
    _pivot_depth: int = 0,
) -> list[StageResult]:
    """Execute pipeline stages sequentially from *from_stage* to *to_stage* (inclusive)."""

    results: list[StageResult] = []
    started = False
    total_stages = len(STAGE_SEQUENCE)

    # ── Integration hooks: EventLog, ExperimentMemory, CostTracker ──
    event_log = None
    try:
        from researchclaw.pipeline.event_log import EventLog, EventType, create_event
        event_log = EventLog(log_dir=run_dir)
        event_log.append(create_event(
            EventType.PIPELINE_START, run_id=run_id,
            stages=total_stages, from_stage=int(from_stage),
        ))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.warning("Event log initialisation failed", exc_info=True)

    exp_memory = None
    try:
        from researchclaw.memory.experiment_memory import ExperimentMemory
        _mem_dir = run_dir / "experiment_memory"
        _mem_dir.mkdir(parents=True, exist_ok=True)
        exp_memory = ExperimentMemory(store_dir=str(_mem_dir))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.warning("Experiment memory initialisation failed", exc_info=True)

    cost_budget = getattr(config.experiment.cli_agent, "max_budget_usd", 0.0) or 0.0
    branch_fanout_completed = False

    for stage in STAGE_SEQUENCE:
        started = _should_start(stage, from_stage, started)
        if not started:
            continue
        if branch_fanout_completed and Stage.EXPERIMENT_DESIGN <= stage <= Stage.RESEARCH_DECISION:
            continue

        # ── Check for cancellation before each stage ──
        if cancel_event is not None and cancel_event.is_set():
            logger.info("[%s] Pipeline cancelled before stage %s", run_id, stage.name)
            _report_progress(progress_reporter, f"[{run_id}] Pipeline cancelled by user.")
            break

        stage_num = int(stage)
        prefix = f"[{run_id}] Stage {stage_num:02d}/{total_stages}"
        _report_progress(progress_reporter, f"{prefix} {stage.name} — running...")

        # ── Event log: stage start ──
        if event_log:
            try:
                event_log.append(create_event(
                    EventType.STAGE_START, run_id=run_id, stage=stage.name,
                ))
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Event log stage-start append failed", exc_info=True)

        # ── Cost budget check ──
        if cost_budget > 0:
            try:
                from researchclaw.cost_tracker import get_global_tracker
                if not get_global_tracker().check_budget(cost_budget):
                    logger.warning("Cost budget $%.2f exceeded — pausing pipeline", cost_budget)
                    _report_progress(
                        progress_reporter,
                        f"{prefix} BUDGET EXCEEDED (${cost_budget:.2f}) — stopping",
                    )
                    break
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Cost budget check failed", exc_info=True)

        # BUG-218: Ensure the best stage-14 experiment data is promoted
        # BEFORE paper writing begins.  Without this, the recursive REFINE
        # path writes the paper using the latest (potentially worse)
        # iteration's data, because the post-recursion promotion at line
        # ~547 runs only after the recursive call—i.e. after the paper
        # has already been written.
        if stage == Stage.PAPER_OUTLINE:
            _promote_best_stage14(run_dir, config)

        t0 = _time.monotonic()

        result = execute_stage(
            stage,
            run_dir=run_dir,
            run_id=run_id,
            config=config,
            adapters=adapters,
            auto_approve_gates=auto_approve_gates,
            cancel_event=cancel_event,
        )
        elapsed = _time.monotonic() - t0

        # ── Event log: stage end ──
        if event_log:
            try:
                etype = EventType.STAGE_END if result.status == StageStatus.DONE else EventType.STAGE_FAIL
                event_log.append(create_event(
                    etype, run_id=run_id, stage=stage.name,
                    status=result.status.value, elapsed_sec=round(elapsed, 1),
                    error=result.error,
                ))
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Event log stage-end append failed", exc_info=True)

        # ── ExperimentSpec: generate after design, validate after analysis ──
        if stage == Stage.HYPOTHESIS_GEN and result.status == StageStatus.DONE:
            try:
                manifest_path = _prepare_parallel_hypothesis_branches(run_dir)
                if manifest_path is not None:
                    logger.info(
                        "Parallel hypothesis branches prepared: %s",
                        manifest_path,
                    )
                    if to_stage is None or to_stage >= Stage.RESEARCH_DECISION:
                        completed_manifest = _execute_parallel_hypothesis_branches(
                            run_dir=run_dir,
                            run_id=run_id,
                            config=config,
                            adapters=adapters,
                            auto_approve_gates=auto_approve_gates,
                            cancel_event=cancel_event,
                        )
                        branch_fanout_completed = completed_manifest is not None
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning(
                    "Parallel hypothesis branch preparation failed",
                    exc_info=True,
                )

        if stage == Stage.EXPERIMENT_DESIGN and result.status == StageStatus.DONE:
            try:
                from researchclaw.pipeline.experiment_spec import ExperimentSpec, MetricDef, generate_spec
                spec_text = generate_spec(config.research.topic, "")
                spec_path = run_dir / f"stage-{int(stage):02d}" / "experiment_spec.md"
                spec_path.write_text(spec_text, encoding="utf-8")
                logger.info("Experiment spec generated: %s", spec_path)
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Experiment spec generation failed", exc_info=True)

        if stage == Stage.RESULT_ANALYSIS and result.status == StageStatus.DONE:
            try:
                from researchclaw.pipeline.experiment_spec import parse_spec, validate_results_against_spec
                spec_path = run_dir / "stage-09" / "experiment_spec.md"
                if spec_path.exists():
                    spec = parse_spec(spec_path.read_text(encoding="utf-8"))
                    results_path = run_dir / "results.json"
                    exp_results = {}
                    if results_path.exists():
                        exp_results = json.loads(results_path.read_text(encoding="utf-8"))
                    violations = validate_results_against_spec(spec, exp_results)
                    if violations:
                        logger.warning("Spec violations: %s", violations)
                        (run_dir / f"stage-{int(stage):02d}" / "spec_violations.json").write_text(
                            json.dumps(violations, indent=2), encoding="utf-8"
                        )
            except (ImportError, json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Experiment spec validation failed", exc_info=True)

        # ── Pitfall detection after code generation / experiment run ──
        if stage in (Stage.CODE_GENERATION, Stage.EXPERIMENT_RUN) and result.status == StageStatus.DONE:
            try:
                from researchclaw.pipeline.pitfall_detector import PitfallDetector
                detector = PitfallDetector()
                code_path = run_dir / f"stage-{int(stage):02d}"
                code_files = list(code_path.rglob("*.py"))
                code_text = "\n".join(f.read_text(errors="ignore") for f in code_files[:5])
                pitfalls = detector.detect_all(code=code_text, results={}, experiment_config={})
                if pitfalls:
                    critical = [p for p in pitfalls if p.severity == "critical"]
                    if critical:
                        logger.warning("CRITICAL pitfalls detected: %s", [p.description for p in critical])
                    pitfall_report = [{"type": p.type.value, "severity": p.severity, "description": p.description} for p in pitfalls]
                    (run_dir / f"stage-{int(stage):02d}" / "pitfall_report.json").write_text(
                        json.dumps(pitfall_report, indent=2), encoding="utf-8"
                    )
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Pitfall detection failed", exc_info=True)

        # ── Experiment memory: record outcome after experiment stages ──
        if stage in (Stage.EXPERIMENT_RUN, Stage.ITERATIVE_REFINE) and result.status == StageStatus.DONE and exp_memory:
            try:
                from researchclaw.memory.experiment_memory import ExperimentOutcome
                import time as _time_mod
                results_path = run_dir / "results.json"
                metric_val = 0.0
                if results_path.exists():
                    rdata = json.loads(results_path.read_text(encoding="utf-8"))
                    metric_val = rdata.get(config.experiment.metric_key, 0.0)
                exp_memory.record_outcome(ExperimentOutcome(
                    run_id=run_id, stage=stage.name,
                    hypothesis=config.research.topic, config={},
                    metric_name=config.experiment.metric_key,
                    metric_value=float(metric_val) if metric_val else 0.0,
                    baseline_value=0.0, improvement=0.0,
                    success=result.status == StageStatus.DONE,
                    failure_mode=result.error,
                    packages_used=[], hyperparameters={},
                    timestamp=_time_mod.time(), duration_sec=elapsed,
                ))
            except (ImportError, json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Experiment memory recording failed", exc_info=True)

        if result.status == StageStatus.DONE:
            arts = ", ".join(result.artifacts) if result.artifacts else "none"
            if result.decision == "degraded":
                _report_progress(
                    progress_reporter,
                    f"{prefix} {stage.name} — DEGRADED ({elapsed:.1f}s) "
                    f"— continuing with sanitization → {arts}",
                )
            else:
                _report_progress(
                    progress_reporter,
                    f"{prefix} {stage.name} — done ({elapsed:.1f}s) → {arts}",
                )
        elif result.status == StageStatus.FAILED:
            err = result.error or "unknown error"
            _report_progress(
                progress_reporter,
                f"{prefix} {stage.name} — FAILED ({elapsed:.1f}s) — {err}",
            )
        elif result.status == StageStatus.BLOCKED_APPROVAL:
            _report_progress(
                progress_reporter,
                f"{prefix} {stage.name} — blocked (awaiting approval)",
            )
        elif result.status == StageStatus.PAUSED:
            err = result.error or "paused"
            _report_progress(
                progress_reporter,
                f"{prefix} {stage.name} -- PAUSED ({elapsed:.1f}s) -- {err}",
            )
        results.append(result)
        write_progress_snapshot(
            run_dir=run_dir,
            run_id=run_id,
            results=results,
            current_stage=stage,
            total_stages=total_stages,
            status="running",
            elapsed_sec=elapsed,
        )

        if kb_root is not None and result.status == StageStatus.DONE:
            try:
                stage_dir = run_dir / f"stage-{int(stage):02d}"
                write_stage_to_kb(
                    kb_root,
                    stage_id=int(stage),
                    stage_name=stage.name.lower(),
                    run_id=run_id,
                    artifacts=list(result.artifacts),
                    stage_dir=stage_dir,
                    backend=config.knowledge_base.backend,
                    topic=config.research.topic,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Knowledge-base stage write failed", exc_info=True)

        if result.status == StageStatus.DONE:
            _write_checkpoint(run_dir, stage, run_id, adapters=adapters)

        # ── Stop after to_stage if specified ──
        if to_stage is not None and stage == to_stage:
            logger.info("[%s] Reached --to-stage %s, stopping.", run_id, stage.name)
            _report_progress(
                progress_reporter,
                f"[{run_id}] Reached --to-stage {stage.name}, stopping pipeline.",
            )
            break

        # --- Experiment diagnosis + repair after Stage 14 (result_analysis) ---
        if (
            stage == Stage.RESULT_ANALYSIS
            and result.status == StageStatus.DONE
            and config.experiment.repair.enabled
        ):
            _run_experiment_diagnosis(
                run_dir,
                config,
                run_id,
                progress_reporter=progress_reporter,
            )

            # Check if repair loop should run
            _diag_path = run_dir / "experiment_diagnosis.json"
            if _diag_path.exists():
                try:
                    _diag_data = json.loads(_diag_path.read_text(encoding="utf-8"))
                    if _diag_data.get("repair_needed"):
                        _run_experiment_repair(
                            run_dir,
                            config,
                            run_id,
                            progress_reporter=progress_reporter,
                        )
                except (json.JSONDecodeError, OSError):
                    logger.warning(
                        "[%s] Experiment diagnosis report could not be read",
                        run_id,
                        exc_info=True,
                    )

        # --- Heartbeat for sentinel watchdog ---
        if result.status == StageStatus.DONE:
            _write_heartbeat(run_dir, stage, run_id)

        # --- PIVOT/REFINE decision handling ---
        if (
            stage == Stage.RESEARCH_DECISION
            and result.status == StageStatus.DONE
            and result.decision in DECISION_ROLLBACK
        ):
            pivot_count = _read_pivot_count(run_dir)
            # R6-4: Skip REFINE if experiment metrics are empty for consecutive cycles
            if pivot_count > 0 and _consecutive_empty_metrics(run_dir, pivot_count):
                logger.warning(
                    "Consecutive REFINE cycles produced empty metrics — forcing PROCEED"
                )
                _report_progress(
                    progress_reporter,
                    f"[{run_id}] Consecutive empty metrics across REFINE cycles — forcing PROCEED"
                )
                # BUG-211: Promote best stage-14 before proceeding with
                # empty data — an earlier iteration may have real metrics.
                _promote_best_stage14(run_dir, config)
            elif (
                pivot_count < MAX_DECISION_PIVOTS
                and _pivot_depth < MAX_DECISION_PIVOTS
            ):
                rollback_target = DECISION_ROLLBACK[result.decision]
                _record_decision_history(
                    run_dir, result.decision, rollback_target, pivot_count + 1
                )
                logger.info(
                    "Decision %s: rolling back to %s (attempt %d/%d)",
                    result.decision.upper(),
                    rollback_target.name,
                    pivot_count + 1,
                    MAX_DECISION_PIVOTS,
                )
                _report_progress(
                    progress_reporter,
                    f"[{run_id}] Decision: {result.decision.upper()} → "
                    f"rollback to {rollback_target.name} "
                    f"(attempt {pivot_count + 1}/{MAX_DECISION_PIVOTS})",
                )
                # Version existing stage directories before overwriting
                _version_rollback_stages(
                    run_dir, rollback_target, pivot_count + 1
                )
                # Recurse from rollback target
                pivot_results = execute_pipeline(
                    run_dir=run_dir,
                    run_id=run_id,
                    config=config,
                    adapters=adapters,
                    from_stage=rollback_target,
                    auto_approve_gates=auto_approve_gates,
                    stop_on_gate=stop_on_gate,
                    skip_noncritical=skip_noncritical,
                    kb_root=kb_root,
                    cancel_event=cancel_event,
                    progress_reporter=progress_reporter,
                    _pivot_depth=_pivot_depth + 1,
                )
                results.extend(pivot_results)
                # BUG-211: Promote best stage-14 after REFINE completes so
                # downstream stages use the best data, not just the latest.
                _promote_best_stage14(run_dir, config)
                break  # Exit current loop; recursive call handles the rest
            else:
                effective_pivot_count = max(pivot_count, _pivot_depth)
                # Quality gate: check if experiment results are actually usable
                _quality_ok, _quality_msg = _check_experiment_quality(
                    run_dir, effective_pivot_count
                )
                if not _quality_ok:
                    logger.warning(
                        "Max pivot attempts (%d) reached — forcing PROCEED "
                        "with quality warning: %s",
                        MAX_DECISION_PIVOTS,
                        _quality_msg,
                    )
                    _report_progress(
                        progress_reporter,
                        f"[{run_id}] QUALITY WARNING: {_quality_msg}",
                    )
                    # Write quality warning to run directory
                    _qw_path = run_dir / "quality_warning.txt"
                    _qw_path.write_text(
                        f"Max pivots ({MAX_DECISION_PIVOTS}) reached.\n"
                        f"Quality gate failed: {_quality_msg}\n"
                        f"Paper will be written but may have significant issues.\n",
                        encoding="utf-8",
                    )
                else:
                    logger.warning(
                        "Max pivot attempts (%d) reached — forcing PROCEED",
                        MAX_DECISION_PIVOTS,
                    )
                _report_progress(
                    progress_reporter,
                    f"[{run_id}] Max pivot attempts reached — forcing PROCEED",
                )

                # BUG-205: After forced PROCEED, promote the BEST stage-14
                # experiment summary across all REFINE iterations.
                _promote_best_stage14(run_dir, config)

        # --- HITL: Handle abort decision ---
        if result.decision == "abort":
            logger.info("[%s] Pipeline aborted by user at stage %s", run_id, stage.name)
            _report_progress(progress_reporter, f"[{run_id}] Pipeline aborted by user at {stage.name}")
            break

        if result.status == StageStatus.FAILED:
            if skip_noncritical and stage in NONCRITICAL_STAGES:
                logger.warning("Noncritical stage %s failed - skipping", stage.name)
            else:
                break

        if result.status == StageStatus.PAUSED:
            logger.warning(
                "[%s] Pipeline paused at %s: %s",
                run_id,
                stage.name,
                result.error or result.decision,
            )
            break

        # --- HITL: Handle rejected stage (from HITL review) ---
        if result.status == StageStatus.REJECTED:
            logger.info(
                "[%s] Stage %s rejected by reviewer — pipeline stopped",
                run_id, stage.name,
            )
            _report_progress(progress_reporter, f"[{run_id}] Stage {stage.name} rejected — pipeline stopped")
            break

        if result.status == StageStatus.BLOCKED_APPROVAL and stop_on_gate:
            break

    summary = _build_pipeline_summary(
        run_id=run_id,
        results=results,
        from_stage=from_stage,
        run_dir=run_dir,
    )
    _write_pipeline_summary(run_dir, summary)
    if results:
        write_progress_snapshot(
            run_dir=run_dir,
            run_id=run_id,
            results=results,
            current_stage=results[-1].stage,
            total_stages=total_stages,
            status=str(summary.get("final_status", "completed")),
        )

    # ── Event log: pipeline end ──
    if event_log:
        try:
            done_count = sum(1 for r in results if r.status == StageStatus.DONE)
            failed_count = sum(1 for r in results if r.status == StageStatus.FAILED)
            event_log.append(create_event(
                EventType.PIPELINE_END, run_id=run_id,
                stages_done=done_count, stages_failed=failed_count,
            ))
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("Event log pipeline-end append failed", exc_info=True)

    # --- Evolution: extract and store lessons ---
    lessons: list[object] = []
    try:
        lessons = extract_lessons(results, run_id=run_id, run_dir=run_dir)
        if lessons:
            store = EvolutionStore(run_dir / "evolution")
            store.append_many(lessons)
            logger.info("Extracted %d lessons from pipeline run", len(lessons))
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.warning(
            "Evolution lesson extraction failed (non-blocking)",
            exc_info=True,
        )

    # --- MetaClaw bridge: convert high-severity lessons to skills ---
    try:
        _metaclaw_post_pipeline(config, results, lessons, run_id, run_dir)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.warning(
            "MetaClaw post-pipeline hook failed (non-blocking)",
            exc_info=True,
        )

    # --- Package deliverables into a single folder ---
    try:
        deliverables_dir = _package_deliverables(run_dir, run_id, config)
        if deliverables_dir is not None:
            _report_progress(
                progress_reporter,
                f"[{run_id}] Deliverables packaged → {deliverables_dir}",
            )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.warning("Deliverables packaging failed (non-blocking)", exc_info=True)

    # --- HITL: Finalize session state ---
    try:
        hitl_session = getattr(adapters, "hitl", None)
        if hitl_session is not None:
            has_abort = any(
                r.decision == "abort" for r in results
            )
            has_failure = any(
                r.status == StageStatus.FAILED for r in results
            )
            if has_abort:
                hitl_session.abort()
            elif has_failure:
                hitl_session.abort()
            else:
                hitl_session.complete()
    except (RuntimeError, TypeError, ValueError):
        logger.warning("HITL session finalization failed (non-blocking)", exc_info=True)

    return results


def _package_deliverables(
    run_dir: Path,
    run_id: str,
    config: RCConfig,
) -> Path | None:
    return package_deliverables(run_dir, run_id, config)



def _version_rollback_stages(
    run_dir: Path, rollback_target: Stage, attempt: int
) -> None:
    """Rename stage directories that will be overwritten by a PIVOT/REFINE.

    For example, if rolling back to Stage 8 (attempt 2), renames:
      stage-08/ → stage-08_v1/
      stage-09/ → stage-09_v1/
      ... up to stage-15/
    """
    import shutil

    rollback_num = int(rollback_target)
    # Stages from rollback target up to RESEARCH_DECISION (15) will be rerun
    decision_num = int(Stage.RESEARCH_DECISION)

    for stage_num in range(rollback_num, decision_num + 1):
        stage_dir = run_dir / f"stage-{stage_num:02d}"
        if stage_dir.exists():
            version_dir = run_dir / f"stage-{stage_num:02d}_v{attempt}"
            if version_dir.exists():
                shutil.rmtree(version_dir)
            stage_dir.rename(version_dir)
            logger.debug(
                "Versioned %s → %s", stage_dir.name, version_dir.name
            )


def _consecutive_empty_metrics(run_dir: Path, pivot_count: int) -> bool:
    """R6-4: Check if the current and previous REFINE cycles both produced empty metrics."""
    # Check the most recent experiment_summary.json (stage-14) and its versioned predecessor.
    # BUG-215: When stage-14/ doesn't exist (renamed to stage-14_v{N} without
    # promotion), fall back to the latest versioned directory as "current".
    current = run_dir / "stage-14" / "experiment_summary.json"
    if not current.exists():
        # Try the latest versioned directory
        for _v in range(pivot_count + 1, 0, -1):
            alt = run_dir / f"stage-14_v{_v}" / "experiment_summary.json"
            if alt.exists():
                current = alt
                break
    prev = run_dir / f"stage-14_v{pivot_count}" / "experiment_summary.json"
    for path in (current, prev):
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Check all possible metric locations
            has_metrics = False
            ms = data.get("metrics_summary", {})
            if isinstance(ms, dict) and ms:
                has_metrics = True
            br = data.get("best_run", {})
            if isinstance(br, dict) and br.get("metrics"):
                has_metrics = True
            if has_metrics:
                return False  # At least one cycle had real metrics
        except (json.JSONDecodeError, OSError, AttributeError):
            return False
    return True  # Both cycles had empty metrics


def _promote_best_stage14(run_dir: Path, config: RCConfig) -> None:
    """BUG-205: After forced PROCEED, promote the best stage-14 experiment.

    Scans all ``stage-14*`` directories, scores them by primary metric,
    and copies the best experiment_summary.json into ``stage-14/`` if the
    current ``stage-14/`` is not already the best.
    """
    import shutil

    metric_key = config.experiment.metric_key or "primary_metric"
    metric_dir = config.experiment.metric_direction or "maximize"

    candidates: list[tuple[float, Path]] = []
    for d in sorted(run_dir.glob("stage-14*")):
        summary_path = d / "experiment_summary.json"
        if not summary_path.exists():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "Failed to read stage-14 summary candidate from %s: %s",
                summary_path,
                exc,
                exc_info=True,
            )
            continue
        ms = data.get("metrics_summary", {})
        pm_val: float | None = None
        # BUG-DA8-03: Exact match first, then substring fallback
        # (avoids "accuracy" matching "balanced_accuracy")
        if metric_key in ms:
            _v = ms[metric_key]
            try:
                pm_val = float(_v["mean"] if isinstance(_v, dict) else _v)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug(
                    "Failed to parse primary metric %s from %s: %s",
                    metric_key,
                    summary_path,
                    exc,
                    exc_info=True,
                )
        if pm_val is None:
            for k, v in ms.items():
                if metric_key in k:
                    try:
                        pm_val = float(v["mean"] if isinstance(v, dict) else v)
                    except (TypeError, ValueError, KeyError) as exc:
                        logger.debug(
                            "Failed to parse fallback primary metric %s from %s: %s",
                            k,
                            summary_path,
                            exc,
                            exc_info=True,
                        )
                    break
        if pm_val is not None:
            if math.isnan(pm_val):
                continue
            candidates.append((pm_val, d))

    if not candidates:
        return  # nothing to promote

    current_dir = run_dir / "stage-14"

    # Sort: best first
    candidates.sort(key=lambda x: x[0], reverse=(metric_dir == "maximize"))

    # BUG-226: Detect degenerate near-zero metrics (broken normalization or
    # collapsed training).  When minimising, a value >1000x smaller than the
    # second-best almost certainly comes from a degenerate iteration.
    if metric_dir == "minimize" and len(candidates) > 1:
        _bv, _bd = candidates[0]
        _sv = candidates[1][0]
        if 0 < _bv < _sv * 1e-3:
            logger.warning(
                "BUG-226: Degenerate best value %.6g is >1000× smaller than "
                "second-best %.6g — skipping degenerate iteration %s",
                _bv, _sv, _bd.name,
            )
            candidates.pop(0)

    best_val, best_dir = candidates[0]

    # BUG-223: Always write canonical best summary at run root BEFORE any
    # early return, so downstream consumers (Stage 17, Stage 20, Stage 22,
    # VerifiedRegistry) always find experiment_summary_best.json.
    _best_src = best_dir / "experiment_summary.json"
    if _best_src.exists():
        shutil.copy2(_best_src, run_dir / "experiment_summary_best.json")
        logger.info(
            "BUG-223: Wrote experiment_summary_best.json from %s (%.4f)",
            best_dir.name, best_val,
        )
        # BUG-225: Also copy analysis.md from the best iteration so Stage 17
        # doesn't read stale analysis from a degenerate non-versioned stage-14.
        _best_analysis = best_dir / "analysis.md"
        if _best_analysis.exists():
            shutil.copy2(_best_analysis, run_dir / "analysis_best.md")

    if best_dir == current_dir:
        logger.info("BUG-205: stage-14/ already has the best result (%.4f)", best_val)
        return

    # Promote: copy best summary into stage-14/
    current_summary = current_dir / "experiment_summary.json"
    best_summary = best_dir / "experiment_summary.json"
    # BUG-213: Also promote when stage-14/ is missing or empty
    if best_summary.exists():
        current_dir.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "BUG-205: Promoting %s (%.4f) over stage-14/",
            best_dir.name, best_val,
        )
        shutil.copy2(best_summary, current_summary)
        # Also copy charts, analysis, and figure plans if they exist
        for fname in [
            "analysis.md",
            "results_table.tex",
            "figure_plan.json",           # BUG-213: must travel with metrics
            "figure_plan_final.json",     # BUG-213: ditto
        ]:
            src = best_dir / fname
            if src.exists():
                shutil.copy2(src, current_dir / fname)
        # Copy charts directory
        best_charts = best_dir / "charts"
        current_charts = current_dir / "charts"
        if best_charts.is_dir():
            if current_charts.is_dir():
                shutil.rmtree(current_charts)
            shutil.copytree(best_charts, current_charts)


def _check_experiment_quality(
    run_dir: Path, pivot_count: int
) -> tuple[bool, str]:
    """Quality gate before forced PROCEED.

    Returns (ok, message). ok=False means experiment results have critical
    quality issues and the forced-PROCEED paper will likely be poor.
    """
    # BUG-DA8-18: Check experiment_summary_best.json first (repair-promoted)
    summary_path = run_dir / "experiment_summary_best.json"
    if not summary_path.exists():
        summary_path = run_dir / "stage-14" / "experiment_summary.json"
    if not summary_path.exists():
        for v in range(pivot_count, 0, -1):
            alt = run_dir / f"stage-14_v{v}" / "experiment_summary.json"
            if alt.exists():
                summary_path = alt
                break

    if not summary_path.exists():
        return False, "No experiment_summary.json found — no metrics produced"

    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, "experiment_summary.json is malformed"

    # Check 1: Are all metrics zero?
    ms = data.get("metrics_summary", {})
    if isinstance(ms, dict):
        values: list[float] = []
        for k, v in ms.items():
            if isinstance(v, (int, float)):
                values.append(float(v))
            # BUG-212: metrics_summary values are often dicts {min,max,mean,count}
            elif isinstance(v, dict) and "mean" in v:
                _mv = v["mean"]
                if isinstance(_mv, (int, float)):
                    values.append(float(_mv))
        if values and all(v == 0.0 for v in values):
            return False, "All experiment metrics are zero — experiments likely failed"

    # Check 2: Zero variance across conditions (R13-1)
    # Look for ablation_warnings or condition comparison data
    ablation_warnings = data.get("ablation_warnings", [])
    # BUG-212: Key is "condition_summaries", not "conditions"
    conditions = data.get(
        "condition_summaries", data.get("condition_metrics", {})
    )
    if isinstance(conditions, dict) and len(conditions) >= 2:
        primary_values: list[float] = []
        for cond_name, cond_data in conditions.items():
            if isinstance(cond_data, dict):
                # BUG-212: Primary metric lives inside cond_data["metrics"]
                _metrics = cond_data.get("metrics", cond_data)
                pm = _metrics.get(
                    "primary_metric",
                    _metrics.get("primary_metric_mean"),
                )
                if isinstance(pm, (int, float)):
                    primary_values.append(float(pm))
        if len(primary_values) >= 2 and len(set(primary_values)) == 1:
            return False, (
                f"All {len(primary_values)} conditions have identical primary_metric "
                f"({primary_values[0]}) — condition implementations are likely broken"
            )

    # Check 3: Too many ablation warnings
    if isinstance(ablation_warnings, list) and len(ablation_warnings) >= 3:
        return False, (
            f"{len(ablation_warnings)} ablation warnings — most conditions "
            f"produce identical results"
        )

    # Check 4: Analysis quality score (if available)
    quality = data.get("analysis_quality", data.get("quality_score"))
    if isinstance(quality, (int, float)) and quality < 3.0:
        return False, f"Analysis quality score {quality}/10 — below minimum threshold"

    return True, "Quality checks passed"


def _read_pivot_count(run_dir: Path) -> int:
    """Read how many PIVOT/REFINE decisions have been made so far."""
    history_path = run_dir / "decision_history.json"
    if not history_path.exists():
        return 0
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return len(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug(
            "Failed to read decision history pivot count from %s: %s",
            history_path,
            exc,
            exc_info=True,
        )
    return 0


def _record_decision_history(
    run_dir: Path, decision: str, rollback_target: Stage, attempt: int
) -> None:
    """Append a decision event to the history log."""
    history_path = run_dir / "decision_history.json"
    history: list[dict[str, object]] = []
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                history = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "Failed to read existing decision history from %s: %s",
                history_path,
                exc,
                exc_info=True,
            )
    history.append({
        "decision": decision,
        "rollback_target": rollback_target.name,
        "rollback_stage_num": int(rollback_target),
        "attempt": attempt,
        "timestamp": _utcnow_iso(),
    })
    history_path.write_text(
        json.dumps(history, indent=2), encoding="utf-8"
    )



def _read_quality_score(run_dir: Path) -> float | None:
    """Extract quality score from the most recent quality_report.json."""
    report_path = run_dir / "stage-20" / "quality_report.json"
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Try common keys: score_1_to_10, score, quality_score
            for key in ("score_1_to_10", "score", "quality_score", "overall_score"):
                if key in data:
                    return float(data[key])
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.debug(
            "Failed to read quality score from %s: %s",
            report_path,
            exc,
            exc_info=True,
        )
    return None


def _write_iteration_context(
    run_dir: Path, iteration: int, reviews: str, quality_score: float | None
) -> None:
    """Write iteration feedback file so next round can read it."""
    ctx = {
        "iteration": iteration,
        "quality_score": quality_score,
        "reviews_excerpt": reviews[:3000] if reviews else "",
        "generated": _utcnow_iso(),
    }
    (run_dir / "iteration_context.json").write_text(
        json.dumps(ctx, indent=2), encoding="utf-8"
    )


def execute_iterative_pipeline(
    *,
    run_dir: Path,
    run_id: str,
    config: RCConfig,
    adapters: AdapterBundle,
    auto_approve_gates: bool = False,
    kb_root: Path | None = None,
    max_iterations: int = 3,
    quality_threshold: float = 7.0,
    convergence_rounds: int = 2,
) -> dict[str, object]:
    """Run the full pipeline with iterative quality improvement.

    After the first full pass (stages 1-22), if the quality gate score is below
    *quality_threshold*, re-run stages 16-22 (paper writing + finalization) with
    review feedback injected.  Stop when:
      - Score >= quality_threshold, OR
      - Score hasn't improved for *convergence_rounds* consecutive iterations, OR
      - *max_iterations* reached.

    Returns a summary dict with iteration history.
    """
    iteration_scores: list[float | None] = []
    all_results: list[list[StageResult]] = []

    # --- First full pass ---
    logger.info("Iteration 1/%d: running full pipeline (stages 1-22)", max_iterations)
    results = execute_pipeline(
        run_dir=run_dir,
        run_id=f"{run_id}-iter1",
        config=config,
        adapters=adapters,
        auto_approve_gates=auto_approve_gates,
        kb_root=kb_root,
    )
    all_results.append(results)
    score = _read_quality_score(run_dir)
    iteration_scores.append(score)
    logger.info("Iteration 1 score: %s", score)

    # --- Iterative improvement ---
    for iteration in range(2, max_iterations + 1):
        # Check if we've met quality threshold
        if score is not None and score >= quality_threshold:
            logger.info(
                "Quality threshold %.1f met (score=%.1f). Stopping.",
                quality_threshold,
                score,
            )
            break

        # Check convergence (score hasn't improved)
        if len(iteration_scores) >= convergence_rounds:
            recent = iteration_scores[-convergence_rounds:]
            if all(s is not None for s in recent):
                recent_scores = [float(s) for s in recent if s is not None]
                if max(recent_scores) - min(recent_scores) < 0.5:
                    logger.info(
                        "Convergence detected: scores %s unchanged for %d rounds. Stopping.",
                        recent,
                        convergence_rounds,
                    )
                    break

        # Write iteration context with feedback from reviews
        reviews_text = ""
        reviews_path = run_dir / "stage-18" / "reviews.md"
        if reviews_path.exists():
            reviews_text = reviews_path.read_text(encoding="utf-8")
        _write_iteration_context(run_dir, iteration, reviews_text, score)

        # Re-run from PAPER_OUTLINE (stage 16) through EXPORT_PUBLISH (stage 22)
        logger.info(
            "Iteration %d/%d: re-running stages 16-22 with feedback",
            iteration,
            max_iterations,
        )
        results = execute_pipeline(
            run_dir=run_dir,
            run_id=f"{run_id}-iter{iteration}",
            config=config,
            adapters=adapters,
            from_stage=Stage.PAPER_OUTLINE,
            auto_approve_gates=auto_approve_gates,
            kb_root=kb_root,
        )
        all_results.append(results)
        score = _read_quality_score(run_dir)
        iteration_scores.append(score)
        logger.info("Iteration %d score: %s", iteration, score)

    # --- Build iterative summary ---
    converged = False
    if len(iteration_scores) >= convergence_rounds:
        recent_window = iteration_scores[-convergence_rounds:]
        if all(s is not None for s in recent_window):
            recent_scores = [float(s) for s in recent_window if s is not None]
            converged = max(recent_scores) - min(recent_scores) < 0.5

    summary: dict[str, object] = {
        "run_id": run_id,
        "total_iterations": len(iteration_scores),
        "iteration_scores": iteration_scores,
        "quality_threshold": quality_threshold,
        "converged": converged,
        "final_score": iteration_scores[-1] if iteration_scores else None,
        "met_threshold": score is not None and score >= quality_threshold,
        "stages_per_iteration": [len(r) for r in all_results],
        "generated": _utcnow_iso(),
    }
    (run_dir / "iteration_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    # --- Package deliverables into a single folder ---
    try:
        deliverables_dir = _package_deliverables(run_dir, run_id, config)
        if deliverables_dir is not None:
            logger.info("[%s] Deliverables packaged -> %s", run_id, deliverables_dir)
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.warning("Deliverables packaging failed (non-blocking)", exc_info=True)

    return summary


def _metaclaw_post_pipeline(
    config: RCConfig,
    results: list[StageResult],
    lessons: list[object],
    run_id: str,
    run_dir: Path,
) -> None:
    """MetaClaw bridge: post-pipeline hook.

    1. Convert high-severity lessons into MetaClaw skills.
    2. Record skill effectiveness feedback.
    3. Signal session end to MetaClaw proxy.
    """
    bridge = getattr(config, "metaclaw_bridge", None)
    if not bridge or not getattr(bridge, "enabled", False):
        return

    from researchclaw.llm.client import LLMClient

    # 1. Lesson-to-skill conversion
    l2s = getattr(bridge, "lesson_to_skill", None)
    if l2s and getattr(l2s, "enabled", False) and lessons:
        try:
            from researchclaw.metaclaw_bridge.lesson_to_skill import (
                convert_lessons_to_skills,
            )

            min_sev = getattr(l2s, "min_severity", "warning")
            llm = LLMClient.from_rc_config(config)
            new_skills = convert_lessons_to_skills(
                lessons,
                llm,
                getattr(bridge, "skills_dir", "~/.metaclaw/skills"),
                min_severity=min_sev,
                max_skills=getattr(l2s, "max_skills_per_run", 3),
            )
            if new_skills:
                logger.info(
                    "MetaClaw: generated %d new skills from lessons: %s",
                    len(new_skills),
                    new_skills,
                )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.warning("MetaClaw lesson-to-skill conversion failed", exc_info=True)

    # 2. Skill effectiveness feedback
    try:
        from researchclaw.metaclaw_bridge.skill_feedback import (
            SkillFeedbackStore,
            record_stage_skills,
        )
        from researchclaw.metaclaw_bridge.stage_skill_map import get_stage_config

        feedback_store = SkillFeedbackStore(run_dir / "evolution" / "skill_effectiveness.jsonl")
        for result in results:
            stage_num = int(getattr(result, "stage", 0))
            stage_name = {
                1: "topic_init", 2: "problem_decompose", 3: "search_strategy",
                4: "literature_collect", 5: "literature_screen", 6: "knowledge_extract",
                7: "synthesis", 8: "hypothesis_gen", 9: "experiment_design",
                10: "code_generation", 11: "resource_planning", 12: "experiment_run",
                13: "iterative_refine", 14: "result_analysis", 15: "research_decision",
                16: "paper_outline", 17: "paper_draft", 18: "peer_review",
                19: "paper_revision", 20: "quality_gate", 21: "knowledge_archive",
                22: "export_publish", 23: "citation_verify",
            }.get(stage_num, "")
            if not stage_name:
                continue

            stage_config = get_stage_config(stage_name)
            active_skills = stage_config.get("skills", [])
            status = str(getattr(result, "status", ""))
            success = "done" in status.lower()

            if active_skills:
                record_stage_skills(
                    feedback_store,
                    stage_name,
                    run_id,
                    success,
                    active_skills,
                )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        logger.warning("MetaClaw skill feedback recording failed", exc_info=True)

    # 3. Signal session end (fire-and-forget)
    try:
        from researchclaw.metaclaw_bridge.session import MetaClawSession
        from researchclaw.utils.http import urlopen_http
        import json as _json
        import urllib.request as _urllib_req

        session = MetaClawSession(run_id)
        end_headers = session.end()
        # Send a minimal request to signal session end
        proxy_url = getattr(bridge, "proxy_url", "http://localhost:30000")
        url = f"{proxy_url.rstrip('/')}/v1/chat/completions"
        body = _json.dumps({
            "model": "session-end",
            "messages": [{"role": "user", "content": "session complete"}],
            "max_tokens": 1,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(end_headers)
        req = _urllib_req.Request(url, data=body, headers=headers)
        try:
            urlopen_http(req, timeout=5)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError, TimeoutError):
            logger.debug("MetaClaw session-end signal request failed", exc_info=True)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        logger.debug("MetaClaw session-end signal setup failed", exc_info=True)

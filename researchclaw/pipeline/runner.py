from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.evolution import EvolutionStore, extract_lessons
from researchclaw.knowledge.base import write_stage_to_kb
from researchclaw.pipeline.checkpoint import (
    read_checkpoint as _read_checkpoint,
)
from researchclaw.pipeline.checkpoint import (
    resume_from_checkpoint as _resume_from_checkpoint,
)
from researchclaw.pipeline.checkpoint import (
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
from researchclaw.pipeline.progress import (
    utcnow_iso as _utcnow_iso,
)
from researchclaw.pipeline.progress import (
    write_progress_snapshot,
)
from researchclaw.pipeline.stages import (
    DECISION_ROLLBACK,
    MAX_DECISION_PIVOTS,
    NONCRITICAL_STAGES,
    STAGE_SEQUENCE,
    Stage,
    StageStatus,
)
from researchclaw.pipeline.summary import (
    build_pipeline_summary,
    collect_content_metrics,
    write_pipeline_summary,
)
from researchclaw.pipeline.runner_post import (
    _check_experiment_quality,
    _consecutive_empty_metrics,
    _metaclaw_post_pipeline,
    _package_deliverables,
    _promote_best_stage14,
    _read_pivot_count,
    _read_quality_score,
    _record_decision_history,
    _version_rollback_stages,
    _write_iteration_context,
    execute_iterative_pipeline,
)

ProgressReporter = Callable[[str], None]

logger = logging.getLogger(__name__)


def _report_progress(reporter: ProgressReporter | None, message: str) -> None:
    if reporter is not None:
        reporter(message)


def _utcnow_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


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
    cancel_event: threading.Event | None,
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
    adapters: AdapterBundle | None = None,
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
    cancel_event: threading.Event | None = None,
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
                from researchclaw.pipeline.experiment_spec import (
                    ExperimentSpec,
                    MetricDef,
                    generate_spec,
                )
                spec_text = generate_spec(config.research.topic, "")
                spec_path = run_dir / f"stage-{int(stage):02d}" / "experiment_spec.md"
                spec_path.write_text(spec_text, encoding="utf-8")
                logger.info("Experiment spec generated: %s", spec_path)
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                logger.warning("Experiment spec generation failed", exc_info=True)

        if stage == Stage.RESULT_ANALYSIS and result.status == StageStatus.DONE:
            try:
                from researchclaw.pipeline.experiment_spec import (
                    parse_spec,
                    validate_results_against_spec,
                )
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
                import time as _time_mod

                from researchclaw.memory.experiment_memory import ExperimentOutcome
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
            if has_abort or has_failure:
                hitl_session.abort()
            else:
                hitl_session.complete()
    except (RuntimeError, TypeError, ValueError):
        logger.warning("HITL session finalization failed (non-blocking)", exc_info=True)

    return results

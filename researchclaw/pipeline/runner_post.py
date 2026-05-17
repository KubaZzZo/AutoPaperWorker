"""Post-run promotion, iteration, and MetaClaw helpers for pipeline runner."""

from __future__ import annotations

import json
import logging
import math
import shutil
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.pipeline.artifact_io import _stage_sort_key
from researchclaw.pipeline.deliverables import package_deliverables
from researchclaw.pipeline.executor import StageResult
from researchclaw.pipeline.progress import utcnow_iso as _utcnow_iso
from researchclaw.pipeline.stages import Stage

logger = logging.getLogger("researchclaw.pipeline.runner")


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

    metric_key = config.experiment.metric_key or "primary_metric"
    metric_dir = config.experiment.metric_direction or "maximize"

    candidates: list[tuple[float, Path]] = []
    for d in sorted(run_dir.glob("stage-14*"), key=_stage_sort_key):
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

    # Sort: best metric first; when tied, prefer the newest numeric stage version.
    def _candidate_rank(item: tuple[float, Path]) -> tuple[float, tuple[str, int]]:
        value, path = item
        metric_rank = value if metric_dir == "maximize" else -value
        base, neg_version = _stage_sort_key(path)
        return (metric_rank, (base, -neg_version))

    candidates.sort(key=_candidate_rank, reverse=True)

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

    from researchclaw.pipeline.runner import execute_pipeline

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
        import json as _json
        import urllib.request as _urllib_req

        from researchclaw.metaclaw_bridge.session import MetaClawSession
        from researchclaw.utils.http import urlopen_http

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

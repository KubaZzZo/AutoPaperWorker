"""Stages 14-15: Result analysis and research decision."""

from __future__ import annotations

import json
import logging
import math
import os
from itertools import combinations
import random
import re
import statistics
import hashlib
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _build_context_preamble,
    _chat_with_prompt,
    _collect_experiment_results,
    _collect_json_context,
    _get_evolution_overlay,
    _multi_perspective_generate,
    _read_prior_artifact,
    _synthesize_perspectives,
    _utcnow_iso,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)


def _select_best_sandbox(iteration: dict[str, Any]) -> dict[str, Any]:
    """Return the sandbox payload that contains metrics for a refine iteration."""
    sandbox = iteration.get("sandbox", {})
    if isinstance(sandbox, dict) and sandbox.get("metrics"):
        return sandbox
    fixed_sandbox = iteration.get("sandbox_after_fix", {})
    if isinstance(fixed_sandbox, dict) and fixed_sandbox.get("metrics"):
        return fixed_sandbox
    return sandbox if isinstance(sandbox, dict) else {}


def _metric_as_float(metrics: dict[str, Any], metric_key: str) -> float | None:
    """Find a metric by exact key first, then substring fallback."""
    items = list(metrics.items())
    for key, value in items:
        if key == metric_key:
            try:
                return float(value["mean"] if isinstance(value, dict) else value)
            except (TypeError, ValueError, KeyError):
                logger.debug("Stage 14: Could not coerce metric %s", key, exc_info=True)
            return None
    for key, value in items:
        if metric_key in key:
            try:
                return float(value["mean"] if isinstance(value, dict) else value)
            except (TypeError, ValueError, KeyError):
                logger.debug(
                    "Stage 14: Could not coerce fallback metric %s", key, exc_info=True
                )
            return None
    return None


def _merge_refinement_log(
    exp_data: dict[str, Any],
    refine_log_text: str,
    *,
    metric_key: str,
    metric_direction: str,
    context: str,
) -> str:
    """Merge richer Stage 13 refinement metrics into collected experiment data."""
    if not refine_log_text:
        return context
    try:
        refine_data = json.loads(refine_log_text)
        best_version = refine_data.get("best_version", "")
        best_iter = None
        for iteration in refine_data.get("iterations", []):
            sandbox = _select_best_sandbox(iteration)
            if iteration.get("version_dir", "") == best_version and sandbox.get("metrics", {}):
                best_iter = iteration
                break
        if best_iter is None:
            for iteration in refine_data.get("iterations", []):
                if _select_best_sandbox(iteration).get("metrics"):
                    best_iter = iteration
                    break
        if best_iter is None:
            return context

        sandbox = _select_best_sandbox(best_iter)
        refine_metrics = sandbox.get("metrics", {})
        refine_is_better = not exp_data["metrics_summary"]
        if not refine_is_better and refine_metrics:
            existing_pm = _metric_as_float(exp_data.get("metrics_summary") or {}, metric_key)
            refine_pm = _metric_as_float(refine_metrics, metric_key)
            if existing_pm is None:
                refine_is_better = True
            elif refine_pm is not None:
                if metric_direction == "maximize":
                    refine_is_better = refine_pm > existing_pm
                else:
                    refine_is_better = refine_pm < existing_pm
            logger.info(
                "Stage 14: Refine metric comparison: existing=%s, refine=%s, "
                "direction=%s -> refine_is_better=%s",
                existing_pm,
                refine_pm,
                metric_direction,
                refine_is_better,
            )
        if not refine_metrics or not refine_is_better:
            return context

        new_summary: dict[str, dict[str, float | int]] = {}
        for metric_name, metric_value in refine_metrics.items():
            try:
                value = float(metric_value)
                new_summary[metric_name] = {
                    "min": round(value, 6),
                    "max": round(value, 6),
                    "mean": round(value, 6),
                    "count": 1,
                }
            except (ValueError, TypeError):
                logger.debug(
                    "Stage 14: Skipping non-numeric refinement summary metric %s",
                    metric_name,
                    exc_info=True,
                )
        if not new_summary:
            return context

        exp_data["metrics_summary"] = new_summary
        exp_data["best_run"] = {
            "run_id": "iterative-refine-best",
            "task_id": "sandbox-main",
            "status": "completed",
            "metrics": {key: value for key, value in refine_metrics.items()},
            "elapsed_sec": sandbox.get("elapsed_sec", 0),
            "stdout": "",
            "stderr": sandbox.get("stderr", ""),
            "timed_out": sandbox.get("timed_out", False),
        }
        latex_rows = [
            r"\begin{table}[h]",
            r"\centering",
            r"\caption{Experiment Results (Best Refinement Iteration)}",
            r"\begin{tabular}{lrrrr}",
            r"\hline",
            r"Metric & Min & Max & Mean & N \\",
            r"\hline",
        ]
        for metric_name in sorted(new_summary.keys()):
            summary = new_summary[metric_name]
            latex_rows.append(
                f"{metric_name} & {summary['min']:.4f} & {summary['max']:.4f} "
                f"& {summary['mean']:.4f} & {summary['count']} \\\\"
            )
        latex_rows.extend([r"\hline", r"\end{tabular}", r"\end{table}"])
        exp_data["latex_table"] = "\n".join(latex_rows)
        conditions = {
            key for key in refine_metrics if "seed" not in key and not key.endswith("_std")
        }
        exp_data["runs"] = [exp_data["best_run"]]
        exp_data["best_run"]["condition_count"] = len(conditions)
        if not context:
            context = json.dumps(
                {"refinement_best_metrics": refine_metrics},
                indent=2,
                default=str,
            )
        best_metric = refine_data.get("best_metric")
        logger.info(
            "R13-1: Merged %d metrics from refinement_log (best_metric=%.4f)",
            len(refine_metrics),
            float(best_metric) if isinstance(best_metric, (int, float)) else 0.0,
        )
    except (json.JSONDecodeError, OSError, KeyError):
        logger.warning("R13-1: Failed to parse refinement_log.json, using Stage 12 data")
    return context


def _compute_bootstrap_ci(
    values: list[float],
    condition_name: str,
    *,
    iterations: int = 1000,
) -> tuple[float, float, float, float]:
    """Compute mean, std, and bootstrap 95% CI for seed-level values."""
    mean_value = statistics.mean(values)
    std_value = statistics.stdev(values)
    rng = random.Random(_bootstrap_seed(values))
    boot_means = []
    for _ in range(iterations):
        sample = [rng.choice(values) for _ in range(len(values))]
        boot_means.append(statistics.mean(sample))
    boot_means.sort()
    ci_low = round(boot_means[int(0.025 * len(boot_means))], 6)
    ci_high = round(boot_means[int(0.975 * len(boot_means))], 6)
    if ci_low > mean_value or ci_high < mean_value:
        logger.warning(
            "Bootstrap CI [%.4f, %.4f] does not contain mean %.4f "
            "for condition %s -> replacing CI with mean +/- 1.96*SE",
            ci_low,
            ci_high,
            mean_value,
            condition_name,
        )
        standard_error = std_value / (len(values) ** 0.5)
        ci_low = round(mean_value - 1.96 * standard_error, 6)
        ci_high = round(mean_value + 1.96 * standard_error, 6)
    return mean_value, std_value, ci_low, ci_high


def _bootstrap_seed(values: list[float]) -> int:
    """Derive a stable bootstrap seed from the data being resampled."""
    payload = json.dumps([float(value) for value in values], separators=(",", ":"))
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _detect_ablation_failures(
    condition_summaries: dict[str, dict[str, Any]],
) -> list[str]:
    """Detect identical or near-identical condition metrics."""
    ablation_warnings: list[str] = []
    if not condition_summaries or len(condition_summaries) < 2:
        return ablation_warnings

    cond_names = sorted(condition_summaries.keys())
    for cond_a, cond_b in combinations(cond_names, 2):
        summary_a = condition_summaries[cond_a]
        summary_b = condition_summaries[cond_b]
        metrics_a = summary_a.get("metrics") or {}
        metrics_b = summary_b.get("metrics") or {}
        if not isinstance(metrics_a, dict) or not isinstance(metrics_b, dict):
            continue
        shared_keys = set(metrics_a.keys()) & set(metrics_b.keys())
        if not shared_keys:
            continue
        if all(metrics_a[key] == metrics_b[key] for key in shared_keys):
            warning = (
                f"ABLATION FAILURE: Conditions '{cond_a}' and '{cond_b}' produce "
                f"identical outputs across all {len(shared_keys)} metrics. "
                f"The ablation is invalid - the differentiating parameter "
                f"is likely not used in the code."
            )
            ablation_warnings.append(warning)
            logger.warning("P8: %s", warning)
            continue

        near_identical = True
        for key in shared_keys:
            try:
                value_a, value_b = float(metrics_a[key]), float(metrics_b[key])
                denom = max(abs(value_a), abs(value_b), 1e-12)
                if abs(value_a - value_b) / denom > 0.01:
                    near_identical = False
                    break
            except (TypeError, ValueError):
                near_identical = False
                break
        if near_identical:
            warning = (
                f"ABLATION WARNING: Conditions '{cond_a}' and '{cond_b}' produce "
                f"near-identical outputs (<1% relative difference) across "
                f"all {len(shared_keys)} metrics. The ablation may be trivial."
            )
            ablation_warnings.append(warning)
            logger.warning("P8: %s", warning)
    return ablation_warnings


def _build_analysis_summary(
    exp_data: dict[str, Any],
    condition_summaries: dict[str, dict[str, Any]],
    seed_insufficiency_warnings: list[str],
    ablation_warnings: list[str],
    paired_comparisons: list[dict[str, object]],
    total_conditions: int | None,
    total_metrics: int | None,
) -> dict[str, Any]:
    """Build the structured Stage 14 experiment summary payload."""
    summary_payload = {
        "metrics_summary": exp_data["metrics_summary"],
        "total_runs": len(exp_data["runs"]),
        "best_run": exp_data["best_run"],
        "latex_table": exp_data["latex_table"],
        "generated": _utcnow_iso(),
    }
    if seed_insufficiency_warnings:
        summary_payload["seed_insufficiency_warnings"] = seed_insufficiency_warnings

    if condition_summaries and len(condition_summaries) >= 2:
        primary_values = []
        for condition_summary in condition_summaries.values():
            if not isinstance(condition_summary, dict):
                continue
            metrics = condition_summary.get("metrics", {})
            if isinstance(metrics, dict) and metrics:
                primary_candidate = next(iter(metrics.values()), None)
                if isinstance(primary_candidate, dict):
                    primary_candidate = primary_candidate.get("mean")
                if isinstance(primary_candidate, (int, float)):
                    primary_values.append(primary_candidate)
                    continue
            primary_metric = condition_summary.get("primary_metric", {})
            primary_value = (
                primary_metric.get("mean")
                if isinstance(primary_metric, dict)
                else primary_metric
            )
            if isinstance(primary_value, (int, float)):
                primary_values.append(primary_value)
        if len(primary_values) >= 2 and len(set(primary_values)) == 1:
            zero_variance_warning = (
                f"ZERO VARIANCE: All {len(primary_values)} conditions have "
                f"identical primary_metric ({primary_values[0]}). "
                f"Experiment condition wiring is likely broken."
            )
            ablation_warnings.append(zero_variance_warning)
            logger.warning("R13-1: %s", zero_variance_warning)

    if ablation_warnings:
        summary_payload["ablation_warnings"] = ablation_warnings
    if paired_comparisons:
        summary_payload["paired_comparisons"] = paired_comparisons
    if condition_summaries:
        summary_payload["condition_summaries"] = condition_summaries
        summary_payload["condition_metrics"] = condition_summaries
        summary_payload["total_conditions"] = total_conditions
    if total_metrics:
        summary_payload["total_metric_keys"] = total_metrics
    return summary_payload


def _execute_result_analysis(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    # --- Collect experiment data ---
    exp_data = _collect_experiment_results(
        run_dir,
        metric_key=config.experiment.metric_key,
        metric_direction=config.experiment.metric_direction,
    )
    runs_dir = _read_prior_artifact(run_dir, "runs/") or ""
    context = ""
    if runs_dir:
        context = _collect_json_context(Path(runs_dir), max_files=30)

    # --- R13-1: Merge Stage 13 (ITERATIVE_REFINE) results if available ---
    # Stage 13 stores richer per-condition metrics in refinement_log.json
    # that _collect_experiment_results() misses (it only scans runs/ dirs).
    _refine_log_text = _read_prior_artifact(run_dir, "refinement_log.json")
    context = _merge_refinement_log(
        exp_data,
        _refine_log_text,
        metric_key=config.experiment.metric_key or "primary_metric",
        metric_direction=config.experiment.metric_direction or "maximize",
        context=context,
    )


    # --- R19-2: Extract PAIRED comparisons from refinement stdout ---
    from researchclaw.experiment.sandbox import extract_paired_comparisons as _extract_paired

    _all_paired: list[dict[str, object]] = []
    # First: from _collect_experiment_results (Stage 12 runs/)
    if exp_data.get("paired_comparisons"):
        _all_paired.extend(exp_data["paired_comparisons"])
    # Second: from refinement_log iterations (Stage 13)
    if _refine_log_text:
        try:
            refinement_log = json.loads(_refine_log_text)
            for iteration in refinement_log.get("iterations", []):
                for sandbox_key in ("sandbox", "sandbox_after_fix"):
                    sandbox_stdout = (iteration.get(sandbox_key) or {}).get("stdout", "")
                    if sandbox_stdout:
                        _all_paired.extend(_extract_paired(sandbox_stdout))
        except (json.JSONDecodeError, OSError):
            logger.debug("R19-2: Failed to parse refinement stdout comparisons", exc_info=True)

    # --- R19-3: Build structured condition_summaries from metrics ---
    _condition_summaries: dict[str, dict[str, Any]] = {}
    metrics_summary = exp_data.get("metrics_summary", {})
    best_metrics = {}
    if exp_data.get("best_run") and isinstance(exp_data["best_run"], dict):
        best_metrics = exp_data["best_run"].get("metrics", {})

    # Group metrics by condition prefix (e.g., "ppo/primary_metric" → condition "ppo")
    for metric_key_name, metric_value in best_metrics.items():
        parts = metric_key_name.split("/")
        if len(parts) >= 2:
            condition_name = parts[0]
            metric_name = parts[-1]
            if condition_name not in _condition_summaries:
                _condition_summaries[condition_name] = {"metrics": {}}
            try:
                _condition_summaries[condition_name]["metrics"][metric_name] = float(metric_value)
            except (ValueError, TypeError):
                logger.debug("Stage 14: Skipping non-numeric condition metric %s/%s", condition_name, metric_name, exc_info=True)

    # BUG-09 fix: If no condition summaries were built (metrics don't use
    # condition/metric format), try to extract from metrics_summary or
    # structured_results so FigureAgent has data to work with.
    if not _condition_summaries and metrics_summary:
        # Try to parse condition data from metrics_summary keys
        for metric_key_name, metric_value in metrics_summary.items():
            parts = metric_key_name.split("/")
            if len(parts) >= 2:
                condition_name = parts[0]
                metric_name = parts[-1]
                if condition_name not in _condition_summaries:
                    _condition_summaries[condition_name] = {"metrics": {}}
                try:
                    # BUG-182: metrics_summary values are dicts {min,max,mean,count},
                    # not plain floats. Extract the mean value.
                    if isinstance(metric_value, dict):
                        value = float(metric_value["mean"]) if "mean" in metric_value else None
                    else:
                        value = float(metric_value)
                    if value is not None:
                        _condition_summaries[condition_name]["metrics"][metric_name] = value
                except (ValueError, TypeError, KeyError):
                    logger.debug("Stage 14: Skipping malformed metrics_summary value %s", metric_key_name, exc_info=True)
    if not _condition_summaries:
        # Last resort: build from structured_results condition keys
        structured_results = exp_data.get("structured_results", {})
        if isinstance(structured_results, dict):
            for condition_name, condition_summary in structured_results.items():
                if isinstance(condition_summary, dict) and condition_name not in ("metadata", "config"):
                    _condition_summaries[condition_name] = {"metrics": {}}
                    for metric_key_name, metric_value in condition_summary.items():
                        try:
                            _condition_summaries[condition_name]["metrics"][metric_key_name] = float(metric_value)
                        except (ValueError, TypeError):
                            logger.debug("Stage 14: Skipping non-numeric structured result %s/%s", condition_name, metric_key_name, exc_info=True)

    # R33: Build per-seed data structure (needed for CIs and paired tests below)
    seed_data: dict[str, dict[int, float]] = {}  # {condition: {seed: value}}
    for metric_key_name, metric_value in best_metrics.items():
        parts = metric_key_name.split("/")
        # Pattern: condition/regime/seed_id/primary_metric
        if len(parts) >= 4 and parts[-1] == config.experiment.metric_key:
            condition_name = parts[0]
            try:
                seed_id = int(parts[2])
                value = float(metric_value)
                seed_data.setdefault(condition_name, {})[seed_id] = value
            except (ValueError, TypeError):
                logger.debug("Stage 14: Skipping malformed seed metric key=%s value=%r", metric_key_name, metric_value, exc_info=True)

    # Enrich condition summaries with seed counts, success rates, and CIs
    for condition_name, condition_summary in _condition_summaries.items():
        # Look for success_rate in metrics
        success_rate_key = f"{condition_name}/success_rate"
        if success_rate_key in best_metrics:
            try:
                condition_summary["success_rate"] = float(best_metrics[success_rate_key])
            except (ValueError, TypeError):
                logger.debug("Stage 14: Skipping non-numeric success_rate for %s", condition_name, exc_info=True)
        # Count seed-level entries to estimate n_seeds
        seed_metric_count = 0
        for metric_key_name in best_metrics:
            if metric_key_name.startswith(f"{condition_name}/") and "seed" in metric_key_name.lower():
                seed_metric_count += 1
        if seed_metric_count > 0:
            condition_summary["n_seed_metrics"] = seed_metric_count

        # R33: Compute mean ± std and bootstrap 95% CI from per-seed data
        if condition_name in seed_data and len(seed_data[condition_name]) >= 3:
            seed_values = list(seed_data[condition_name].values())
            mean_value, std_value, ci_low, ci_high = _compute_bootstrap_ci(seed_values, condition_name)
            condition_summary["metrics"][f"{config.experiment.metric_key}_mean"] = round(mean_value, 6)
            condition_summary["metrics"][f"{config.experiment.metric_key}_std"] = round(std_value, 6)
            condition_summary["n_seeds"] = len(seed_values)
            condition_summary["ci95_low"] = ci_low
            condition_summary["ci95_high"] = ci_high

    # Count totals
    total_conditions = len(_condition_summaries) if _condition_summaries else None
    total_metrics = len(best_metrics) if best_metrics else None

    # --- R33: Pipeline-level paired computation as fallback ---
    # If the experiment code's PAIRED lines are sparse or suspicious (e.g.,
    # all identical t-stats), compute fresh paired tests from per-seed data.
    # (seed_data was built above before condition summary enrichment)
    if len(seed_data) >= 2:
        # Find common seeds across conditions
        all_seed_sets = [set(values.keys()) for values in seed_data.values()]
        common_seeds = set.intersection(*all_seed_sets) if all_seed_sets else set()

        if len(common_seeds) >= 3:
            sorted_condition_names = sorted(seed_data.keys())
            pipeline_paired: list[dict[str, object]] = []
            # Compare each condition against the first baseline (alphabetically)
            baseline_condition = sorted_condition_names[0]
            for other_condition in sorted_condition_names[1:]:
                diffs = []
                for seed_id in sorted(common_seeds):
                    diffs.append(
                        seed_data[other_condition][seed_id] - seed_data[baseline_condition][seed_id]
                    )
                if diffs:
                    sample_size = len(diffs)
                    mean_difference = statistics.mean(diffs)
                    std_difference = statistics.stdev(diffs) if sample_size > 1 else 0.0
                    t_stat = (
                        mean_difference / (std_difference / (sample_size ** 0.5))
                    ) if std_difference > 0 else 0.0
                    degrees_freedom = sample_size - 1
                    # Two-tailed p-value using t-distribution
                    try:
                        from scipy.stats import t as t_dist
                        p_value = float(2 * t_dist.sf(abs(t_stat), degrees_freedom))
                    except ImportError:
                        p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / (2 ** 0.5))))
                        if degrees_freedom < 30:
                            p_value = min(1.0, p_value * (1 + 2.5 / max(degrees_freedom, 1)))
                    pipeline_paired.append({
                        "method": other_condition,
                        "baseline": baseline_condition,
                        "mean_diff": round(mean_difference, 6),
                        "std_diff": round(std_difference, 6),
                        "t_stat": round(t_stat, 4),
                        "p_value": round(p_value, 6),
                        "n_seeds": sample_size,
                        "source": "pipeline_computed",
                    })

            # Use pipeline-computed if experiment code's are suspicious
            experiment_t_stats = {round(p.get("t_stat", 0), 4) for p in _all_paired}
            all_identical = len(experiment_t_stats) <= 1 and len(_all_paired) > 1
            if pipeline_paired and (all_identical or len(_all_paired) < len(pipeline_paired)):
                logger.info(
                    "R33: Using %d pipeline-computed paired tests (experiment code had %d, identical=%s)",
                    len(pipeline_paired), len(_all_paired), all_identical,
                )
                _all_paired = pipeline_paired

    # --- P8: Detect identical conditions (broken ablations) ---
    ablation_warnings = _detect_ablation_failures(_condition_summaries)

    # --- Improvement B: Validate seed counts ---
    seed_insufficiency_warnings: list[str] = []
    for condition_name, condition_seeds in seed_data.items():
        seed_count = len(condition_seeds)
        if 0 < seed_count < 3:
            warning = (
                f"SEED_INSUFFICIENCY: Condition '{condition_name}' has only "
                f"{seed_count} seed(s) (minimum 3 required for statistical validity)"
            )
            seed_insufficiency_warnings.append(warning)
            logger.warning("B: %s", warning)

    # --- Write structured experiment summary ---
    summary_payload = _build_analysis_summary(
        exp_data,
        _condition_summaries,
        seed_insufficiency_warnings,
        ablation_warnings,
        _all_paired,
        total_conditions,
        total_metrics,
    )
    (stage_dir / "experiment_summary.json").write_text(
        json.dumps(summary_payload, indent=2, default=str), encoding="utf-8"
    )
    if exp_data["latex_table"]:
        (stage_dir / "results_table.tex").write_text(
            exp_data["latex_table"], encoding="utf-8"
        )

    # --- Build data-augmented prompt ---
    preamble = _build_context_preamble(
        config, run_dir, include_goal=True, include_hypotheses=True
    )
    data_context = ""
    if exp_data["metrics_summary"]:
        lines = ["\n## Quantitative Results"]
        for metric_key_name, metric_value in exp_data["metrics_summary"].items():
            if isinstance(metric_value, dict):
                lines.append(
                    f"- {metric_key_name}: mean={metric_value.get('mean', '?')}, "
                    f"min={metric_value.get('min', '?')}, "
                    f"max={metric_value.get('max', '?')}, n={metric_value.get('count', '?')}"
                )
        data_context = "\n".join(lines)

    # Append structured results if available
    if exp_data.get("structured_results"):
        structured_text = json.dumps(
            exp_data["structured_results"], indent=2, default=str
        )
        # Truncate to avoid blowing up context
        if len(structured_text) > 6000:
            structured_text = structured_text[:6000] + "\n... (truncated)"
        data_context += (
            f"\n\n## Structured Experiment Results (from results.json)\n"
            f"```json\n{structured_text}\n```"
        )

    # P8: Inject ablation warnings into data context
    if ablation_warnings:
        data_context += "\n\nCRITICAL ABLATION WARNINGS:\n"
        for warning in ablation_warnings:
            data_context += f"- {warning}\n"
        data_context += (
            "\nYou MUST address these in your analysis. Identical conditions "
            "mean the ablation design is broken and the comparison is meaningless.\n"
        )

    if llm is not None:
        _pm = prompts or PromptManager()
        from researchclaw.prompts import DEBATE_ROLES_ANALYSIS  # noqa: PLC0415

        # --- Multi-perspective debate ---
        perspectives_dir = stage_dir / "perspectives"
        variables = {
            "preamble": preamble,
            "data_context": data_context,
            "context": context,
        }
        perspectives = _multi_perspective_generate(
            llm, DEBATE_ROLES_ANALYSIS, variables, perspectives_dir
        )
        # --- Synthesize into unified analysis ---
        analysis = _synthesize_perspectives(
            llm, perspectives, "analysis_synthesize", _pm
        )
    else:
        # Template with real data if available
        ms = exp_data["metrics_summary"]
        metrics_block = ""
        if ms:
            for metric_key_name, metric_value in ms.items():
                if isinstance(metric_value, dict):
                    metrics_block += (
                        f"- **{metric_key_name}**: mean={metric_value.get('mean')}, "
                        f"min={metric_value.get('min')}, max={metric_value.get('max')}, "
                        f"n={metric_value.get('count')}\n"
                    )
        else:
            metrics_block = f"- Primary metric key: `{config.experiment.metric_key}`\n- No quantitative data yet.\n"

        analysis = f"""# Result Analysis

## Metrics Summary
{metrics_block}
## Comparative Findings
- Proposed approach results from {len(exp_data["runs"])} run(s) collected.

## Statistical Checks
- Recommend confidence interval and seed-wise variance reporting.

## Limitations
- Limited runs and synthetic constraints.

## Conclusion
- Proceed to decision stage with moderate confidence.

Generated: {_utcnow_iso()}
"""
    (stage_dir / "analysis.md").write_text(analysis, encoding="utf-8")

    artifacts = ["analysis.md", "experiment_summary.json"]
    if (stage_dir / "results_table.tex").exists():
        artifacts.append("results_table.tex")

    # IMP-6 + FA: Generate charts early (Stage 14) so paper draft can reference them
    # Try FigureAgent first (multi-agent intelligent charts), fall back to visualize.py
    _figure_plan_saved = False
    if config.experiment.figure_agent.enabled and llm is not None:
        try:
            from researchclaw.agents.figure_agent import FigureOrchestrator
            from researchclaw.agents.figure_agent.orchestrator import FigureAgentConfig as _FACfg

            _fa_cfg = _FACfg(
                enabled=True,
                min_figures=config.experiment.figure_agent.min_figures,
                max_figures=config.experiment.figure_agent.max_figures,
                max_iterations=config.experiment.figure_agent.max_iterations,
                render_timeout_sec=config.experiment.figure_agent.render_timeout_sec,
                use_docker=config.experiment.figure_agent.use_docker,
                docker_image=config.experiment.figure_agent.docker_image,
                output_format=config.experiment.figure_agent.output_format,
                gemini_api_key=os.environ.get(
                    config.experiment.figure_agent.gemini_api_key_env, ""
                ),
                gemini_model=config.experiment.figure_agent.gemini_model,
                nano_banana_enabled=config.experiment.figure_agent.nano_banana_enabled,
                strict_mode=config.experiment.figure_agent.strict_mode,
                dpi=config.experiment.figure_agent.dpi,
            )
            _fa = FigureOrchestrator(llm, _fa_cfg, stage_dir=stage_dir)

            # Build conditions list from condition_summaries
            _fa_conditions = list(_condition_summaries.keys()) if _condition_summaries else []

            # BUG-09 fix: pass best_run metrics as fallback data if
            # structured_results is empty, so Planner has some data to chart
            _fa_exp_results = exp_data.get("structured_results", {})
            if not _fa_exp_results and best_metrics:
                _fa_exp_results = {"best_run_metrics": best_metrics}

            # Read paper draft for Decision Agent analysis
            _paper_draft = (
                _read_prior_artifact(run_dir, "paper_draft.md")
                or _read_prior_artifact(run_dir, "outline.md")
                or ""
            )

            _fa_plan = _fa.orchestrate({
                "experiment_results": _fa_exp_results,
                "condition_summaries": _condition_summaries,
                "metrics_summary": exp_data.get("metrics_summary", {}),
                "metric_key": config.experiment.metric_key,
                "conditions": _fa_conditions,
                "topic": _read_prior_artifact(run_dir, "topic.md") or config.research.topic,
                "hypothesis": _read_prior_artifact(run_dir, "hypotheses.md") or "",
                "paper_draft": _paper_draft,
                "output_dir": str(stage_dir / "charts"),
            })

            if _fa_plan.figure_count > 0:
                # Save figure plan for Stage 17 to read
                (stage_dir / "figure_plan.json").write_text(
                    json.dumps(_fa_plan.to_dict(), indent=2, default=str),
                    encoding="utf-8",
                )
                _figure_plan_saved = True
                for _cf_name in _fa_plan.get_chart_files():
                    artifacts.append(f"charts/{_cf_name}")
                logger.info(
                    "Stage 14: FigureAgent generated %d charts (%d passed review, %.1fs)",
                    _fa_plan.figure_count,
                    _fa_plan.passed_count,
                    _fa_plan.elapsed_sec,
                )
            else:
                logger.warning("Stage 14: FigureAgent produced no charts, falling back")
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as _fa_exc:
            logger.warning("Stage 14: FigureAgent failed (%s), falling back to visualize.py", _fa_exc, exc_info=True)

    # Fallback: legacy visualize.py chart generation
    if not _figure_plan_saved:
        try:
            from researchclaw.experiment.visualize import (
                generate_all_charts as _gen_charts_early,
            )

            _charts_dir = stage_dir / "charts"
            _early_charts = _gen_charts_early(
                run_dir,
                _charts_dir,
                metric_key=config.experiment.metric_key,
            )
            if _early_charts:
                for _cp in _early_charts:
                    artifacts.append(f"charts/{_cp.name}")
                logger.info(
                    "Stage 14: Generated %d early charts (legacy) for paper embedding",
                    len(_early_charts),
                )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as _chart_exc:
            logger.warning("Stage 14: Early chart generation failed: %s", _chart_exc, exc_info=True)

    return StageResult(
        stage=Stage.RESULT_ANALYSIS,
        status=StageStatus.DONE,
        artifacts=tuple(artifacts),
        evidence_refs=tuple(f"stage-14/{a}" for a in artifacts),
    )


def _parse_decision(text: str) -> str:
    """Extract PROCEED/PIVOT/REFINE from decision text.

    Looks for the first standalone keyword on its own line after a
    ``## Decision`` heading.  Falls back to a keyword scan of the first
    few lines after the heading, but only matches the keyword itself
    (not mentions inside explanatory prose like "PIVOT is not warranted").
    Returns lowercase ``"proceed"`` / ``"pivot"`` / ``"refine"``.
    Defaults to ``"proceed"`` if nothing matches.
    """
    text_upper = text.upper()
    # Look in the first occurrence after "## Decision" heading
    decision_section = ""
    for keyword in ("## DECISION", "## Decision", "## decision"):
        if keyword.upper() in text_upper:
            idx = text_upper.index(keyword.upper())
            decision_section = text[idx : idx + 200]
            break
    search_text = decision_section or text[:500]

    # First try: look for a line that is just the keyword (possibly with
    # whitespace / markdown bold / trailing punctuation).
    for line in search_text.splitlines():
        stripped = line.strip().strip("*").strip("#").strip()
        if stripped.upper() in ("PROCEED", "PIVOT", "REFINE"):
            return stripped.lower()

    # Fallback: regex for standalone word boundaries so that
    # "PIVOT is not warranted" does NOT match as a decision.
    for kw in ("PIVOT", "REFINE", "PROCEED"):
        # Only match if the keyword appears as the FIRST keyword-class token
        # on its own (not embedded in a sentence saying "not PIVOT").
        pattern = re.compile(
            r"(?:^|##\s*Decision\s*\n\s*)" + kw, re.IGNORECASE | re.MULTILINE
        )
        if pattern.search(search_text):
            return kw.lower()

    # Last resort: position-based — prefer whichever keyword appears LAST
    # (the final conclusion after deliberation is more reliable than early mentions)
    # BUG-DA8-08: Old code always returned "refine" when both keywords present
    search_upper = search_text.upper()
    last_refine = search_upper.rfind("REFINE")
    last_pivot = search_upper.rfind("PIVOT")
    if last_refine >= 0 and (last_pivot < 0 or last_refine > last_pivot):
        return "refine"
    if last_pivot >= 0 and (last_refine < 0 or last_pivot > last_refine):
        return "pivot"
    return "proceed"


def _execute_research_decision(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    analysis = _read_prior_artifact(run_dir, "analysis.md") or ""

    # P6: Detect degenerate REFINE cycles — inject warning if metrics stagnate
    degenerate_hint = ""
    refine_log_text = _read_prior_artifact(run_dir, "refinement_log.json")
    if refine_log_text:
        try:
            refinement_log = json.loads(refine_log_text)
            iterations = refinement_log.get("iterations", [])
            metrics = [
                iteration.get("metric")
                for iteration in iterations
                if isinstance(iteration, dict)
            ]
            valid_metrics = [metric for metric in metrics if metric is not None]
            all_saturated = valid_metrics and all(
                metric <= 0.001 or metric >= 0.999 for metric in valid_metrics
            )
            all_identical = len(set(valid_metrics)) <= 1 and len(valid_metrics) >= 2
            if all_saturated or all_identical:
                degenerate_hint = (
                    "\n\nSYSTEM WARNING — DEGENERATE REFINE CYCLE DETECTED:\n"
                    f"Metrics across {len(valid_metrics)} iterations: {valid_metrics}\n"
                    "All iterations produce identical/saturated results. Further REFINE "
                    "cycles CANNOT fix this — the underlying benchmark design is too "
                    "easy/hard. You SHOULD choose PROCEED with a quality caveat rather "
                    "than REFINE again.\n"
                )
                logger.warning("P6: Degenerate refine cycle detected, injecting PROCEED hint")
        except (json.JSONDecodeError, OSError):
            logger.debug("P6: Failed to parse refinement_log for degenerate cycle check", exc_info=True)

    # Phase 2: Inject experiment diagnosis into decision prompt
    _diagnosis_hint = ""
    _diag_path = run_dir / "experiment_diagnosis.json"
    if _diag_path.exists():
        try:
            _diag_data = json.loads(_diag_path.read_text(encoding="utf-8"))
            _qa = _diag_data.get("quality_assessment", {})
            _mode = _qa.get("mode", "unknown")
            _sufficient = _qa.get("sufficient", False)
            _deficiency_types = _qa.get("deficiency_types", [])
            if not _sufficient:
                _diagnosis_hint = (
                    "\n\n## EXPERIMENT DIAGNOSIS (from automated analysis)\n"
                    f"Quality mode: {_mode}\n"
                    f"Sufficient for full paper: NO\n"
                    f"Issues found: {', '.join(_deficiency_types)}\n\n"
                    "IMPORTANT: The experiment has significant issues. "
                    "If REFINE is chosen, a structured repair prompt is available "
                    "at repair_prompt.txt with specific fixes for identified issues.\n"
                    "If the same issues persist after 2+ REFINE cycles, choose PROCEED "
                    "with appropriate quality caveats.\n"
                )
                logger.info(
                    "Stage 15: Injected experiment diagnosis — mode=%s, issues=%s",
                    _mode, _deficiency_types,
                )
        except (json.JSONDecodeError, OSError):
            logger.debug("Stage 15: Failed to parse experiment diagnosis", exc_info=True)

    # Improvement C: Check ablation quality — if >50% trivial, push REFINE
    _ablation_refine_hint = ""
    # BUG-DA8-16: Prefer experiment_summary_best.json (promoted best) over
    # alphabetically-last stage-14* (which could be a stale versioned dir)
    _exp_sum_path = run_dir / "experiment_summary_best.json"
    if not _exp_sum_path.is_file():
        _exp_sum_path = None
        for _s14 in sorted(run_dir.glob("stage-14*/experiment_summary.json"), reverse=True):
            _exp_sum_path = _s14
            break
    if _exp_sum_path and _exp_sum_path.is_file():
        try:
            from researchclaw.pipeline.stage_impls._paper_writing import (
                _check_ablation_effectiveness,
            )
            _abl_exp = json.loads(_exp_sum_path.read_text(encoding="utf-8"))
            _abl_warnings = _check_ablation_effectiveness(_abl_exp, threshold=0.02)
            if _abl_warnings:
                _trivial_count = sum(1 for w in _abl_warnings if "ineffective" in w.lower() or "trivial" in w.lower())
                _total_abl = max(1, len(_abl_warnings))
                if _trivial_count / _total_abl > 0.5:
                    _ablation_refine_hint = (
                        "\n\n## ABLATION QUALITY ASSESSMENT (CRITICAL)\n"
                        f"STRONG RECOMMENDATION: Choose REFINE.\n"
                        f"{_trivial_count}/{_total_abl} ablations show <2% difference from baseline "
                        f"(trivially similar). This means the ablation design is broken.\n"
                        "Warnings:\n" + "\n".join(f"- {w}" for w in _abl_warnings) + "\n"
                    )
                    logger.warning("C: %d/%d ablations trivial → recommending REFINE", _trivial_count, _total_abl)
        except (ImportError, json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Stage 15: Ablation quality assessment skipped", exc_info=True)

    if llm is not None:
        _pm = prompts or PromptManager()
        _overlay = _get_evolution_overlay(run_dir, "research_decision")
        sp = _pm.for_stage("research_decision", evolution_overlay=_overlay, analysis=analysis)
        _user = sp.user + degenerate_hint + _diagnosis_hint + _ablation_refine_hint
        resp = _chat_with_prompt(llm, sp.system, _user)
        decision_md = resp.content
    else:
        decision_md = f"""# Research Decision

## Decision
PROCEED

## Justification
Current evidence suggests measurable progress with actionable limitations.

## Next Actions
- Build detailed paper outline
- Expand ablation and uncertainty analysis in writing

Generated: {_utcnow_iso()}
"""
    (stage_dir / "decision.md").write_text(decision_md, encoding="utf-8")

    # --- Extract structured decision ---
    decision = _parse_decision(decision_md)

    # T3.1: Validate decision quality — check for minimum experiment rigor
    _quality_warnings: list[str] = []
    _dec_lower = decision_md.lower()
    if "baseline" not in _dec_lower and "control" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention baselines")
    if "seed" not in _dec_lower and "replicat" not in _dec_lower and "run" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention multi-seed/replicate runs")
    if "metric" not in _dec_lower and "accuracy" not in _dec_lower and "loss" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention evaluation metrics")
    if _quality_warnings:
        logger.warning("T3.1: Decision quality warnings: %s", _quality_warnings)

    decision_payload = {
        "decision": decision,
        "raw_text_excerpt": decision_md[:500],
        "quality_warnings": _quality_warnings,
        "generated": _utcnow_iso(),
    }
    (stage_dir / "decision_structured.json").write_text(
        json.dumps(decision_payload, indent=2), encoding="utf-8"
    )
    logger.info("Research decision: %s", decision)

    return StageResult(
        stage=Stage.RESEARCH_DECISION,
        status=StageStatus.DONE,
        artifacts=("decision.md", "decision_structured.json"),
        evidence_refs=("stage-15/decision.md",),
        decision=decision,
    )

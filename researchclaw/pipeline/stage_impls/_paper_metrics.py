"""Experiment metric helpers for paper writing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _collect_raw_experiment_metrics(run_dir: Path) -> tuple[str, bool]:
    """Collect raw experiment metric lines from stdout for paper writing.

    Returns a tuple of (formatted block, has_parsed_metrics).
    ``has_parsed_metrics`` is True when at least one run had a non-empty
    ``metrics`` dict in its JSON payload — a reliable signal of real data.
    """
    metric_lines: list[str] = []
    run_count = 0
    has_parsed_metrics = False

    for stage_subdir in sorted(run_dir.glob("stage-*/runs")):
        for run_file in sorted(stage_subdir.glob("*.json")):
            if run_file.name == "results.json":
                continue
            try:
                payload = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(payload, dict):
                continue

            # R10: Skip simulated data — only collect real experiment results
            if payload.get("status") == "simulated":
                continue

            run_count += 1

            # Extract from parsed metrics (check both 'metrics' and 'key_metrics')
            metrics = payload.get("metrics", {}) or payload.get("key_metrics", {})
            if isinstance(metrics, dict) and metrics:
                has_parsed_metrics = True
                for k, v in metrics.items():
                    metric_lines.append(f"  {k}: {v}")

            # Also extract from stdout for full detail
            # BUG-23: Filter out infrastructure lines that are NOT experiment results
            _INFRA_KEYS = {
                "SEED_COUNT", "TIME_ESTIMATE", "TRAINING_STEPS",
                "REGISTERED_CONDITIONS", "METRIC_DEF", "GPU_MEMORY",
                "BATCH_SIZE", "NUM_WORKERS", "TOTAL_PARAMS",
                "time_budget_sec", "max_epochs", "num_seeds",
            }
            stdout = payload.get("stdout", "")
            if stdout:
                for line in stdout.splitlines():
                    line = line.strip()
                    if ":" in line:
                        parts = line.rsplit(":", 1)
                        try:
                            float(parts[1].strip())
                            key_part = parts[0].strip().split("/")[-1]  # last segment
                            if key_part in _INFRA_KEYS:
                                continue  # skip infrastructure lines
                            metric_lines.append(f"  {line}")
                        except (ValueError, TypeError, IndexError):
                            logger.debug("Stage 17: Skipping non-metric stdout line %r", line, exc_info=True)

    # R19-4 + R23-1: Collect metrics from refinement_log.json (Stage 13).
    # If refinement has richer data than Stage 12 runs/, REPLACE Stage 12 data
    # to avoid confusing the paper writer with conflicting sources.
    _refine_lines: list[str] = []
    _refine_run_count = 0
    # Scan ALL refinement logs across versions, pick by quality (primary
    # metric) then richness (metric count).  BUG-207: Previous logic picked
    # the sandbox entry with the most metric keys regardless of whether it
    # represented a regression (e.g. sandbox_after_fix with 1.29% accuracy
    # winning over sandbox with 78.93% because it had 6 more keys).
    _best_refine_metrics: dict[str, Any] = {}
    _best_refine_stdout = ""
    _best_refine_primary: float | None = None
    for _rl_path in sorted(run_dir.glob("stage-13*/refinement_log.json")):
        try:
            _rlog = json.loads(_rl_path.read_text(encoding="utf-8"))
            for _it in _rlog.get("iterations", []):
                for _sbx_key in ("sandbox", "sandbox_after_fix"):
                    _sbx = _it.get(_sbx_key, {})
                    if not isinstance(_sbx, dict):
                        continue
                    _sbx_metrics = _sbx.get("metrics", {})
                    if not isinstance(_sbx_metrics, dict) or not _sbx_metrics:
                        continue
                    # Extract primary metric value for quality comparison
                    _sbx_primary: float | None = None
                    for _pm_key in ("primary_metric", "best_metric"):
                        if _pm_key in _sbx_metrics:
                            try:
                                _sbx_primary = float(_sbx_metrics[_pm_key])
                            except (ValueError, TypeError):
                                logger.debug("Stage 17: Could not coerce refinement primary metric %s", _pm_key, exc_info=True)
                            break
                    # Prefer higher primary metric; fall back to count
                    _dominated = False
                    if _best_refine_primary is not None and _sbx_primary is not None:
                        if _sbx_primary > _best_refine_primary:
                            _dominated = True  # new is better
                        elif _sbx_primary < _best_refine_primary * 0.5:
                            continue  # skip: regression (>50% worse)
                    # Accept if quality-dominant or richer-with-no-regression
                    if _dominated or len(_sbx_metrics) > len(_best_refine_metrics):
                        _best_refine_metrics = _sbx_metrics
                        _best_refine_stdout = _sbx.get("stdout", "")
                        _best_refine_primary = _sbx_primary
        except (json.JSONDecodeError, OSError):
            logger.debug("Stage 17: Failed to parse refinement log %s", _rl_path, exc_info=True)

    if _best_refine_metrics and len(_best_refine_metrics) > len(metric_lines) // 2:
        # Refinement has richer data — REPLACE Stage 12 data to avoid conflicts
        metric_lines = []
        run_count = 1
        for k, v in _best_refine_metrics.items():
            metric_lines.append(f"  {k}: {v}")
        # Also extract PAIRED and metric lines from stdout
        if _best_refine_stdout:
            for _line in _best_refine_stdout.splitlines():
                _line = _line.strip()
                if _line.startswith("PAIRED:"):
                    metric_lines.append(f"  {_line}")
                elif ":" in _line:
                    parts = _line.rsplit(":", 1)
                    try:
                        float(parts[1].strip())
                        metric_lines.append(f"  {_line}")
                    except (ValueError, TypeError, IndexError):
                        logger.debug("Stage 17: Skipping non-metric refinement stdout line %r", _line, exc_info=True)
    elif _best_refine_metrics:
        # Refinement has some data but not richer — append to existing
        run_count += 1
        for k, v in _best_refine_metrics.items():
            metric_lines.append(f"  {k}: {v}")
        if _best_refine_stdout:
            for _line in _best_refine_stdout.splitlines():
                _line = _line.strip()
                if _line.startswith("PAIRED:"):
                    metric_lines.append(f"  {_line}")

    if not metric_lines:
        return "", has_parsed_metrics

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for line in metric_lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)

    # BUG-29: Reformat raw metric lines into human-readable condition summaries
    # to prevent LLM from pasting raw path-style lines into the paper
    _grouped: dict[str, list[str]] = {}
    _ungrouped: list[str] = []
    for line in unique[:200]:
        stripped = line.strip()
        # Match pattern: condition/env/step/metric: value
        parts = stripped.split("/")
        if len(parts) >= 3 and ":" in parts[-1]:
            cond = parts[0]
            detail = "/".join(parts[1:])
            _grouped.setdefault(cond, []).append(f"  - {detail}")
        else:
            _ungrouped.append(stripped)

    formatted_lines: list[str] = []
    if _grouped:
        for cond, details in sorted(_grouped.items()):
            formatted_lines.append(f"## Condition: {cond}")
            formatted_lines.extend(details[:30])
    if _ungrouped:
        formatted_lines.extend(_ungrouped)

    return (
        f"\n\nACTUAL EXPERIMENT DATA (from {run_count} run(s) — use ONLY these numbers):\n"
        "```\n"
        + "\n".join(formatted_lines[:200])
        + "\n```\n"
        "CRITICAL: Every number in the Results table MUST come from the data above. "
        "Do NOT round excessively, do NOT invent numbers, do NOT change values. "
        f"The experiment ran {run_count} time(s) — state this accurately in the methodology.\n"
        "NEVER paste raw metric paths (like 'condition/env/step/metric: value') "
        "into the paper. Always convert to formatted LaTeX tables or inline prose.\n"
    ), has_parsed_metrics


def _check_ablation_effectiveness(
    exp_summary: dict[str, Any],
    threshold: float = 0.02,
) -> list[str]:
    """P7: Check if ablation results are within *threshold* of baseline.

    Returns a list of warning strings for ineffective ablations.
    Threshold tightened from 5% to 2% (Improvement C) — ablations with
    < 2% relative difference AND < 1pp absolute difference are flagged
    as TRIVIAL.
    """
    warnings: list[str] = []
    cond_summaries = exp_summary.get("condition_summaries", {})
    if not isinstance(cond_summaries, dict) or not cond_summaries:
        return warnings

    # Find baseline/control condition
    baseline_name = None
    baseline_mean = None
    for name, data in cond_summaries.items():
        if not isinstance(data, dict):
            continue
        name_lower = name.lower()
        if any(tag in name_lower for tag in ("baseline", "control", "vanilla", "standard")):
            metrics = data.get("metrics") or {}
            if not isinstance(metrics, dict):
                metrics = {}
            # Use the first metric that has a _mean suffix or the first available
            for mk, mv in metrics.items():
                if mk.endswith("_mean"):
                    baseline_name = name
                    baseline_mean = float(mv)
                    break
            if baseline_mean is None:
                for mk, mv in metrics.items():
                    try:
                        baseline_name = name
                        baseline_mean = float(mv)
                        break
                    except (TypeError, ValueError):
                        continue
            if baseline_name:
                break

    if baseline_name is None or baseline_mean is None:
        return warnings

    # Check each ablation condition
    for name, data in cond_summaries.items():
        if not isinstance(data, dict):
            continue
        name_lower = name.lower()
        if name == baseline_name:
            continue
        if not any(tag in name_lower for tag in ("ablation", "no_", "without", "reduced")):
            continue
        metrics = data.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        for mk, mv in metrics.items():
            if not mk.endswith("_mean"):
                continue
            try:
                abl_val = float(mv)
            except (TypeError, ValueError):
                continue
            if baseline_mean != 0:
                rel_diff = abs(abl_val - baseline_mean) / abs(baseline_mean)
            else:
                rel_diff = abs(abl_val - baseline_mean)
            abs_diff = abs(abl_val - baseline_mean)
            # Improvement C: Tighter check — both relative < threshold
            # AND absolute < 1pp → TRIVIAL
            if rel_diff < threshold and abs_diff < 1.0:
                warnings.append(
                    f"TRIVIAL: Ablation '{name}' {mk}={abl_val:.4f} is within "
                    f"{rel_diff:.1%} (abs {abs_diff:.4f}pp) of baseline "
                    f"'{baseline_name}' {mk}={baseline_mean:.4f} — "
                    f"ablation is ineffective"
                )
            elif rel_diff < threshold:
                warnings.append(
                    f"Ablation '{name}' {mk}={abl_val:.4f} is within "
                    f"{rel_diff:.1%} of baseline '{baseline_name}' "
                    f"{mk}={baseline_mean:.4f} — ablation may be ineffective"
                )
            break  # Only check the first _mean metric per condition

    # Improvement C: Prepend CRITICAL summary if >50% trivial
    trivial_count = sum(1 for w in warnings if w.startswith("TRIVIAL:"))
    if trivial_count > 0 and len(warnings) > 0 and trivial_count / len(warnings) > 0.5:
        warnings.insert(0, (
            f"CRITICAL: {trivial_count}/{len(warnings)} ablations are trivially "
            f"similar to baseline (<{threshold:.0%} relative, <1pp absolute). "
            f"The ablation design is likely broken — components are not effectively removed."
        ))

    return warnings


def _detect_result_contradictions(
    exp_summary: dict[str, Any],
    metric_direction: str = "maximize",
) -> list[str]:
    """P10: Detect contradictions in experiment results before paper writing.

    Returns a list of advisory strings to inject into paper writing prompt.
    """
    advisories: list[str] = []
    cond_summaries = exp_summary.get("condition_summaries", {})
    if not isinstance(cond_summaries, dict) or not cond_summaries:
        return advisories

    # Collect primary metric means per condition
    means: dict[str, float] = {}
    for name, data in cond_summaries.items():
        if not isinstance(data, dict):
            continue
        metrics = data.get("metrics", {})
        for mk, mv in metrics.items():
            if mk.endswith("_mean"):
                try:
                    means[name] = float(mv)
                except (TypeError, ValueError):
                    logger.debug("P10: Skipping non-numeric condition mean for %s/%s", name, mk, exc_info=True)
                break

    if len(means) < 2:
        return advisories

    # Check 1: All methods within noise margin (2% relative spread)
    vals = list(means.values())
    val_range = max(vals) - min(vals)
    val_mean = sum(vals) / len(vals)
    if val_mean != 0 and (val_range / abs(val_mean)) < 0.02:
        advisories.append(
            "NULL RESULT: All methods produce nearly identical primary metric values "
            f"(range={val_range:.4f}, mean={val_mean:.4f}). Frame this as a null result — "
            "the methods are statistically indistinguishable. Do NOT claim any method "
            "is superior. Discuss possible explanations (task too easy/hard, metric "
            "insensitive, insufficient differentiation in methods)."
        )

    # Check 2: Control/simple baseline outperforms proposed method
    # BUG-P1: Respect metric_direction — "higher is better" vs "lower is better"
    _maximize = metric_direction == "maximize"
    baseline_val = None
    baseline_name = None
    proposed_val = None
    proposed_name = None
    for name, val in means.items():
        name_lower = name.lower()
        if any(tag in name_lower for tag in ("baseline", "control", "random", "vanilla")):
            if baseline_val is None or (_maximize and val > baseline_val) or (not _maximize and val < baseline_val):
                baseline_val = val
                baseline_name = name
        elif any(tag in name_lower for tag in ("proposed", "our", "novel", "method")):
            if proposed_val is None or (_maximize and val > proposed_val) or (not _maximize and val < proposed_val):
                proposed_val = val
                proposed_name = name

    if baseline_val is not None and proposed_val is not None:
        _baseline_wins = (baseline_val > proposed_val) if _maximize else (baseline_val < proposed_val)
        if _baseline_wins:
            advisories.append(
                f"NEGATIVE RESULT: Baseline '{baseline_name}' ({baseline_val:.4f}) "
                f"outperforms proposed method '{proposed_name}' ({proposed_val:.4f}). "
                "This is a NEGATIVE result. Do NOT claim the proposed method is superior. "
                "Frame as 'An Empirical Study of...' or 'When X Falls Short'. "
                "Discuss why the baseline won and what this implies for future work."
            )

    return advisories

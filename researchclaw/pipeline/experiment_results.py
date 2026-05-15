"""Experiment stdout metric parsing and run-result aggregation."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from researchclaw.hardware import is_metric_name
from researchclaw.pipeline.parsing import safe_json_loads

logger = logging.getLogger(__name__)

_CONDITION_RE = re.compile(
    r"^condition=(\S+)\s+metric=([0-9eE.+-]+)\s*$"
)


def parse_metrics_from_stdout(
    stdout: str,
    *,
    condition_pattern: Any = _CONDITION_RE,
    diagnostic_logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Parse metric lines from experiment stdout."""
    log = diagnostic_logger or logger
    metrics: dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        m = condition_pattern.match(line)
        if m:
            cond_name = m.group(1)
            try:
                fval = float(m.group(2))
                metrics[cond_name] = fval
            except (ValueError, TypeError) as exc:
                log.debug(
                    "Skipping non-numeric condition metric from stdout %s=%r: %s",
                    cond_name,
                    m.group(2),
                    exc,
                    exc_info=True,
                )
            continue
        if ":" not in line:
            continue
        parts = line.rsplit(":", 1)
        if len(parts) != 2:
            continue
        name_part = parts[0].strip()
        value_part = parts[1].strip()
        if not is_metric_name(name_part):
            continue
        try:
            fval = float(value_part)
            metrics[name_part] = fval
        except (ValueError, TypeError) as exc:
            log.debug(
                "Skipping non-numeric metric from stdout %s=%r: %s",
                name_part,
                value_part,
                exc,
                exc_info=True,
            )
    return metrics


def collect_experiment_results(
    run_dir: Path,
    metric_key: str = "",
    metric_direction: str = "maximize",
    *,
    json_loader: Callable[[str, Any], Any] = safe_json_loads,
    diagnostic_logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Aggregate experiment metrics from runs/ directories across prior stages."""
    log = diagnostic_logger or logger
    runs_data: list[dict[str, Any]] = []
    structured_results: Any = None

    for stage_subdir in sorted(run_dir.glob("stage-*/runs")):
        results_json = stage_subdir / "results.json"
        if results_json.exists() and structured_results is None:
            try:
                structured_results = json.loads(
                    results_json.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                log.debug(
                    "Failed to load structured experiment results from %s: %s",
                    results_json,
                    exc,
                    exc_info=True,
                )

        for run_file in sorted(stage_subdir.glob("*.json")):
            if run_file.name == "results.json":
                continue
            parsed = json_loader(run_file.read_text(encoding="utf-8"), {})
            if isinstance(parsed, dict) and "metrics" in parsed:
                if "structured_results" in parsed and structured_results is None:
                    structured_results = parsed["structured_results"]
                runs_data.append(parsed)
            elif isinstance(parsed, dict) and "key_metrics" in parsed:
                parsed["metrics"] = parsed.pop("key_metrics")
                runs_data.append(parsed)

    if not runs_data:
        result: dict[str, Any] = {
            "runs": [],
            "metrics_summary": {},
            "best_run": None,
            "latex_table": "",
        }
        if structured_results is not None:
            result["structured_results"] = structured_results
        return result

    metrics_summary = _summarize_metrics(runs_data, log)
    best_run = _select_best_run(runs_data, metric_key, metric_direction, log)
    latex_table = _build_latex_table(metrics_summary)
    paired_comparisons = _collect_paired_comparisons(runs_data)

    collected: dict[str, Any] = {
        "runs": runs_data,
        "metrics_summary": metrics_summary,
        "best_run": best_run,
        "latex_table": latex_table,
    }
    if paired_comparisons:
        collected["paired_comparisons"] = paired_comparisons
    if structured_results is not None:
        collected["structured_results"] = structured_results
    return collected


def _summarize_metrics(
    runs_data: list[dict[str, Any]],
    log: logging.Logger,
) -> dict[str, dict[str, float | int]]:
    all_metric_keys: set[str] = set()
    for r in runs_data:
        m = r.get("metrics") or {}
        if isinstance(m, dict):
            all_metric_keys.update(m.keys())

    metrics_summary: dict[str, dict[str, float | int]] = {}
    for key in sorted(all_metric_keys):
        values = []
        for r in runs_data:
            m = r.get("metrics") or {}
            if isinstance(m, dict) and key in m:
                try:
                    fval = float(m[key])
                    if fval == fval and abs(fval) != float("inf"):
                        values.append(fval)
                except (ValueError, TypeError) as exc:
                    log.debug(
                        "Skipping non-numeric experiment metric %s=%r: %s",
                        key,
                        m[key],
                        exc,
                        exc_info=True,
                    )
        if values:
            metrics_summary[key] = {
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "mean": round(sum(values) / len(values), 6),
                "count": len(values),
            }
    return metrics_summary


def _select_best_run(
    runs_data: list[dict[str, Any]],
    metric_key: str,
    metric_direction: str,
    log: logging.Logger,
) -> dict[str, Any] | None:
    def _primary_metric(r: dict[str, Any]) -> float:
        m = r.get("metrics") or {}
        if isinstance(m, dict):
            if metric_key and metric_key in m:
                try:
                    return float(m[metric_key])
                except (ValueError, TypeError) as exc:
                    log.debug(
                        "Skipping non-numeric primary experiment metric %s=%r: %s",
                        metric_key,
                        m[metric_key],
                        exc,
                        exc_info=True,
                    )
            for v in m.values():
                try:
                    return float(v)
                except (ValueError, TypeError) as exc:
                    log.debug(
                        "Skipping non-numeric fallback experiment metric value %r: %s",
                        v,
                        exc,
                        exc_info=True,
                    )
        return 0.0

    compare = min if metric_direction == "minimize" else max
    return compare(runs_data, key=_primary_metric)


def _build_latex_table(metrics_summary: dict[str, dict[str, float | int]]) -> str:
    latex_lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Experiment Results}",
    ]
    if metrics_summary:
        cols = sorted(metrics_summary.keys())
        latex_lines.append(r"\begin{tabular}{l" + "r" * 4 + "}")
        latex_lines.append(r"\hline")
        latex_lines.append("Metric & Min & Max & Mean & N \\\\")
        latex_lines.append(r"\hline")
        for col in cols:
            s = metrics_summary[col]
            row = (
                f"{col} & {s['min']:.4f} & {s['max']:.4f} & "
                f"{s['mean']:.4f} & {s['count']} \\\\"
            )
            latex_lines.append(row)
        latex_lines.append(r"\hline")
        latex_lines.append(r"\end{tabular}")
    else:
        latex_lines.append(r"\begin{tabular}{l}")
        latex_lines.append("No experiment data available \\\\")
        latex_lines.append(r"\end{tabular}")
    latex_lines.append(r"\end{table}")
    return "\n".join(latex_lines)


def _collect_paired_comparisons(
    runs_data: list[dict[str, Any]],
) -> list[dict[str, object]]:
    from researchclaw.experiment.sandbox import extract_paired_comparisons

    paired_comparisons: list[dict[str, object]] = []
    for r in runs_data:
        stdout = r.get("stdout", "")
        if stdout:
            paired_comparisons.extend(extract_paired_comparisons(stdout))
    return paired_comparisons

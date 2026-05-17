"""Helpers for experiment repair results and sandbox execution."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
from pathlib import Path
from typing import Any

logger = logging.getLogger("researchclaw.pipeline.experiment_repair")

_CODE_BLOCK_RE = re.compile(
    r"```(?:python)?\s*([\w./\\-]+\.(?:py|txt))\s*\n(.*?)```",
    re.DOTALL,
)
_UNNAMED_BLOCK_RE = re.compile(
    r"```python\s*\n(.*?)```",
    re.DOTALL,
)


def _extract_code_blocks(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _CODE_BLOCK_RE.finditer(text):
        fname = Path(match.group(1).strip()).name
        code = match.group(2).strip()
        if fname and code:
            files[fname] = code
    if not files:
        for match in _UNNAMED_BLOCK_RE.finditer(text):
            code = match.group(1).strip()
            if code and len(code) > 50:
                files["main.py"] = code
                break
    return files


def _run_experiment_in_sandbox(
    exp_dir: Path,
    config: Any,
    work_dir: Path,
    timeout_sec: int = 600,
) -> dict | None:
    """Run experiment code in Docker/sandbox and return results dict."""
    try:
        from researchclaw.experiment.factory import create_sandbox

        sandbox_dir = work_dir / "sandbox"
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        sandbox = create_sandbox(config.experiment, sandbox_dir)
        result = sandbox.run_project(exp_dir, timeout_sec=timeout_sec)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "metrics": dict(result.metrics) if result.metrics else {},
            "elapsed_sec": result.elapsed_sec,
            "timed_out": result.timed_out,
        }
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("Sandbox execution failed: %s", exc, exc_info=True)
        return None


def _build_experiment_summary_from_run(
    run_result: dict,
    code: dict[str, str],
) -> dict:
    """Build an experiment_summary.json from a single sandbox run."""
    metrics = run_result.get("metrics", {})
    stdout = run_result.get("stdout", "")

    if not metrics and stdout:
        try:
            from researchclaw.experiment.sandbox import parse_metrics
            metrics = parse_metrics(stdout)
        except ImportError as exc:
            logger.debug(
                "parse_metrics unavailable while building experiment summary: %s",
                exc,
                exc_info=True,
            )

    condition_summaries: dict[str, dict] = {}
    for key, value in metrics.items():
        if not isinstance(value, (int, float)):
            continue
        parts = key.split("/")
        if len(parts) >= 3:
            cond_name = parts[0]
            metric_name = parts[-1]
            if cond_name not in condition_summaries:
                condition_summaries[cond_name] = {"metrics": {}, "seeds": {}}
            condition_summaries[cond_name]["metrics"][metric_name] = value
            seed_key = "/".join(parts[1:-1])
            condition_summaries[cond_name]["seeds"].setdefault(seed_key, {})[metric_name] = value
        elif len(parts) == 2:
            cond_name, metric_name = parts
            if cond_name not in condition_summaries:
                condition_summaries[cond_name] = {"metrics": {}, "seeds": {}}
            condition_summaries[cond_name]["metrics"][metric_name] = value
            condition_summaries[cond_name]["seeds"].setdefault("0", {})[metric_name] = value

    for cond_name, cdata in condition_summaries.items():
        seeds = cdata.get("seeds", {})
        if seeds:
            cdata["n_seeds"] = len(seeds)
            all_metrics: dict[str, list[float]] = {}
            for seed_data in seeds.values():
                for mk, mv in seed_data.items():
                    if isinstance(mv, (int, float)):
                        all_metrics.setdefault(mk, []).append(float(mv))
            for mk, values in all_metrics.items():
                if values:
                    cdata["metrics"][mk] = sum(values) / len(values)
        cdata.pop("seeds", None)

    return {
        "condition_summaries": condition_summaries,
        "best_run": {
            "metrics": metrics,
            "status": "completed" if run_result.get("returncode") == 0 else "failed",
            "stdout": stdout[:5000],
            "stderr": run_result.get("stderr", "")[:2000],
        },
        "metrics_summary": {},
        "total_conditions": len(condition_summaries),
        "total_metric_keys": len(metrics),
    }


__all__ = [
    "_build_experiment_summary_from_run",
    "_extract_code_blocks",
    "_run_experiment_in_sandbox",
]

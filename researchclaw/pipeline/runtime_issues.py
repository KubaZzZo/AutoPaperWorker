"""Runtime issue detection for sandboxed experiment results."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_NAN_RE = re.compile(r"\bnan\b", re.IGNORECASE)


def detect_runtime_issues(
    sandbox_result: Any,
    *,
    diagnostic_logger: logging.Logger | None = None,
) -> str:
    """Detect NaN/Inf metrics, suspicious stdout, and stderr warnings."""
    log = diagnostic_logger or logger
    issues: list[str] = []

    metrics = _sandbox_metrics(sandbox_result)
    for key, val in metrics.items():
        try:
            fval = float(val)
            if math.isnan(fval):
                issues.append(
                    f"METRIC NaN: '{key}' returned NaN — likely a division by zero or invalid computation in code"
                )
            elif math.isinf(fval):
                issues.append(
                    f"METRIC Inf: '{key}' returned Infinity — likely overflow or unbounded computation"
                )
        except (TypeError, ValueError) as exc:
            log.debug(
                "Skipping non-numeric sandbox metric while detecting runtime issues %s=%r: %s",
                key,
                val,
                exc,
                exc_info=True,
            )

    stdout = getattr(sandbox_result, "stdout", "") or ""
    if _NAN_RE.search(stdout):
        nan_lines = [
            line.strip()
            for line in stdout.splitlines()
            if _NAN_RE.search(line)
        ]
        if nan_lines:
            issues.append(
                "NaN values detected in output:\n" + "\n".join(nan_lines[:10])
            )

    stderr = getattr(sandbox_result, "stderr", "") or ""
    if stderr.strip():
        warning_lines = []
        for line in stderr.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if any(
                kw in line_stripped
                for kw in (
                    "Warning",
                    "Error",
                    "Traceback",
                    "Exception",
                    "divide",
                    "overflow",
                    "invalid value",
                    "NaN",
                    "inf",
                )
            ):
                warning_lines.append(line_stripped)
        if warning_lines:
            issues.append(
                "Runtime warnings/errors from stderr:\n"
                + "\n".join(warning_lines[:15])
            )

    if stdout:
        metric_values_by_name: dict[str, list[float]] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            parts = line.rsplit(":", 1)
            if len(parts) != 2:
                continue
            try:
                fval = float(parts[1].strip())
            except (ValueError, TypeError) as exc:
                log.debug(
                    "Skipping non-numeric stdout metric while detecting dummy metrics %s=%r: %s",
                    parts[0].strip(),
                    parts[1].strip(),
                    exc,
                    exc_info=True,
                )
                continue
            name = parts[0].strip()
            metric_suffix = name.split()[-1] if name.split() else name
            metric_values_by_name.setdefault(metric_suffix, []).append(fval)

        for metric_name, vals in metric_values_by_name.items():
            if len(vals) >= 3:
                unique = set(vals)
                if len(unique) <= 2:
                    issues.append(
                        f"DUMMY METRIC: '{metric_name}' has only {len(unique)} unique value(s) "
                        f"across {len(vals)} entries ({unique}) — likely a placeholder. "
                        f"Implement real measurement logic (e.g., track iterations to convergence)."
                    )

    for key, val in metrics.items():
        try:
            fval = float(val)
            if "loss" in key.lower() and fval > 100:
                issues.append(
                    f"DIVERGING LOSS: '{key}' = {fval} (>100) — the optimization is "
                    f"diverging. Reduce learning rate, check gradient computation, "
                    f"or add gradient clipping."
                )
        except (TypeError, ValueError) as exc:
            log.debug(
                "Skipping non-numeric loss metric while detecting divergence %s=%r: %s",
                key,
                val,
                exc,
                exc_info=True,
            )

    if not issues:
        return ""

    return (
        "## Runtime Issues Detected\n\n"
        "The experiment code ran but produced problematic results. "
        "Fix the ROOT CAUSE of these issues in the code:\n\n"
        + "\n\n".join(f"- {issue}" for issue in issues)
    )


def _sandbox_metrics(sandbox_result: Any) -> Mapping[str, Any]:
    metrics = getattr(sandbox_result, "metrics", {}) or {}
    if isinstance(metrics, Mapping):
        return metrics
    return {}

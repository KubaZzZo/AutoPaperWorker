"""Fabricated-results sanitization for publish-stage papers."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from researchclaw.pipeline._helpers import _utcnow_iso

logger = logging.getLogger(__name__)

_SANITIZER_ALWAYS_ALLOWED: set[float] = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0,
    0.5, 0.01, 0.001, 0.0001, 0.1, 0.05, 0.95, 0.99,
    2024.0, 2025.0, 2026.0, 2027.0,
    8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0, 2048.0,
    224.0, 299.0, 384.0,
    0.0003, 0.0005, 0.002, 2e-3,
    0.2, 0.3, 0.25, 0.7, 0.6, 0.8,
    0.9, 0.999, 0.9999,
    0.02, 0.03,
    1e-5, 1e-6, 1e-8,
    300.0, 400.0, 500.0,
    4096.0, 8192.0,
}

_HYPHEN_CHARS = "\u2010\u2011\u2013\\-"
_SANITIZER_NUM_PAT = re.compile(
    f"(?<![a-zA-Z_{_HYPHEN_CHARS}])"
    r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"
    r"(%?)"
    f"(?![a-zA-Z_{_HYPHEN_CHARS}])"
)
_MARKDOWN_TABLE_PAT = re.compile(
    r"((?:^[ \t]*\|.+\|[ \t]*\n)+)",
    re.MULTILINE,
)
_LATEX_TABULAR_PAT = re.compile(
    r"(\\begin\{tabular\}.*?\\end\{tabular\})",
    re.DOTALL,
)
_PROSE_RESULT_PATTERN = re.compile(
    r"(?:achiev|obtain|reach|attain|yield|report|record|produc|demonstrat|show|observ)"
    r"(?:ed|es|ing|s)?\s+"
    r"(?:an?\s+)?(?:\w+\s+)?(?:of\s+)?"
    r"(\d+\.?\d*)\s*"
    r"(%|\\%)?",
    re.IGNORECASE,
)
_RESULTS_SECTION_HEADER_PAT = re.compile(
    r"^#{1,3}\s*(Results|Experiments|Experimental|Evaluation|Ablation)",
    re.IGNORECASE,
)
_ANY_MARKDOWN_HEADER_PAT = re.compile(r"^#{1,3}\s+")

_HP_TABLE_KEYWORDS = {
    "hyperparameter", "hyper-parameter", "configuration", "config",
    "setting", "parameter", "learning rate", "lr", "batch size",
    "optimizer", "architecture", "schedule", "warmup", "decay",
    "dropout", "weight decay", "momentum", "epsilon", "clip",
}
_STAT_TABLE_KEYWORDS = {
    "t-statistic", "t-stat", "t statistic", "p-value", "p value",
    "paired", "cohen", "effect size", "wilcoxon", "mann-whitney",
    "statistical", "significance", "confidence interval",
}
_RESULT_TABLE_KEYWORDS = {
    "accuracy", "acc", "loss", "f1", "auroc", "auc", "precision",
    "recall", "bleu", "rouge", "reward", "return", "rmse", "mae",
    "mse", "error", "score", "metric", "performance", "improvement",
    "top-1", "top1", "top-5", "top5",
}
_HP_COLUMN_KEYWORDS = {
    "lr", "learning rate", "batch", "epoch", "optimizer",
    "schedule", "warmup", "decay", "dropout", "momentum",
    "clip", "epsilon", "eps", "beta", "alpha", "gamma",
    "lambda", "weight decay", "wd", "temperature", "temp",
    "hidden", "dim", "layers", "heads", "steps", "iterations",
    "seed", "patience", "#param", "params", "size", "depth",
    "width", "channels", "kernel", "stride", "padding",
    "t-stat", "t stat", "p-value", "p value", "p-val",
    "cohen", "effect", "ci lower", "ci upper", "difference",
}
_LATEX_HP_KEYWORDS = {
    "hyperparameter", "hyper-parameter", "configuration", "config",
    "setting", "learning rate", "lr", "batch size", "optimizer",
}
_LATEX_RESULT_KEYWORDS = {
    "accuracy", "acc", "loss", "f1", "auroc", "auc", "precision",
    "recall", "reward", "score", "metric", "performance", "result",
}
_LATEX_STAT_KEYWORDS = {
    "t-statistic", "t-stat", "t statistic", "p-value", "p value",
    "paired", "cohen", "effect size", "statistical", "significance",
}


class _SanitizationState:
    def __init__(self, verified_values: set[float]) -> None:
        self.verified_values = verified_values
        self.numbers_replaced = 0
        self.numbers_kept = 0
        self.tables_processed = 0
        self.prose_numbers_replaced = 0
        self.replaced_values: list[str] = []


# ---------------------------------------------------------------------------
# Helpers imported from paper-writing stage implementations.
# Lazy-imported inside functions to avoid circular imports when executor.py
# imports the review/publish stage modules.
# ---------------------------------------------------------------------------



def _experiment_summary_richness(path: Path) -> int:
    """Score an experiment_summary.json by how many conditions it has."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return -1
    if not isinstance(data, dict):
        return -1
    return len(data.get("condition_summaries", {})) + len(data.get("metrics_summary", {}))


def _select_sanitizer_summary_path(run_dir: Path) -> Path:
    root_best = run_dir / "experiment_summary_best.json"
    if root_best.exists() and _experiment_summary_richness(root_best) > 0:
        return root_best
    candidates = list(run_dir.glob("stage-14*/experiment_summary.json"))
    if candidates:
        return max(candidates, key=_experiment_summary_richness)
    return run_dir / "stage-14" / "experiment_summary.json"


def _collect_verified_numbers(obj: Any, verified_values: set[float], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if math.isfinite(float(obj)):
            verified_values.add(float(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_verified_numbers(value, verified_values, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            _collect_verified_numbers(value, verified_values, depth + 1)


def _load_verified_values(run_dir: Path) -> set[float]:
    verified_values: set[float] = set()
    exp_path = _select_sanitizer_summary_path(run_dir)
    if not exp_path.exists():
        return verified_values
    try:
        exp_data = json.loads(exp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return verified_values
    if not isinstance(exp_data, dict):
        return verified_values
    for key in (
        "metrics_summary", "condition_summaries", "best_run",
        "condition_metrics", "conditions", "ablation_results",
    ):
        if key in exp_data:
            _collect_verified_numbers(exp_data[key], verified_values)
    return verified_values


def _is_verified_value(num: float, verified_values: set[float]) -> bool:
    """Check exact, percent, and decimal forms against verified values."""
    for v in verified_values:
        if v == 0.0:
            if abs(num) < 1e-9:
                return True
            continue  # cannot compare ratios against zero
        rel_tol = 0.01
        if abs(num - v) / abs(v) <= rel_tol:
            return True
        if abs(num / 100.0 - v) / abs(v) <= rel_tol:
            return True
        if abs(num - v * 100.0) / abs(v * 100.0) <= rel_tol:
            return True
    return False


def _replace_sanitized_number(match: re.Match[str], state: _SanitizationState) -> str:
    num_str = match.group(1)
    pct = match.group(2)
    try:
        value = float(num_str)
    except ValueError:
        return match.group(0)
    if value in _SANITIZER_ALWAYS_ALLOWED:
        state.numbers_kept += 1
        return match.group(0)
    if value == int(value) and abs(value) <= 20:
        state.numbers_kept += 1
        return match.group(0)
    if _is_verified_value(value, state.verified_values):
        state.numbers_kept += 1
        return match.group(0)
    state.numbers_replaced += 1
    state.replaced_values.append(num_str + pct)
    return "---"


def _table_has_separator(lines: list[str]) -> bool:
    return any(re.match(r"^[ \t]*\|[\s:|-]+\|[ \t]*$", line) for line in lines)


def _markdown_table_should_skip(lines: list[str]) -> bool:
    header_lower = lines[0].lower() if lines else ""
    is_hp_table = any(keyword in header_lower for keyword in _HP_TABLE_KEYWORDS)
    is_result_table = any(keyword in header_lower for keyword in _RESULT_TABLE_KEYWORDS)
    is_stat_table = any(keyword in header_lower for keyword in _STAT_TABLE_KEYWORDS)
    return (is_hp_table and not is_result_table) or is_stat_table


def _hp_column_indices(lines: list[str]) -> set[int]:
    hp_cols: set[int] = set()
    if not lines:
        return hp_cols
    for index, cell in enumerate(lines[0].split("|")):
        cell_lower = cell.strip().lower()
        if any(keyword in cell_lower for keyword in _HP_COLUMN_KEYWORDS):
            hp_cols.add(index)
    return hp_cols


def _sanitize_markdown_table(match: re.Match[str], state: _SanitizationState) -> str:
    table_text = match.group(0)
    lines = table_text.split("\n")
    if not _table_has_separator(lines) or _markdown_table_should_skip(lines):
        return table_text

    hp_cols = _hp_column_indices(lines)
    state.tables_processed += 1
    sanitized_lines: list[str] = []
    for index, line in enumerate(lines):
        is_separator = bool(re.match(r"^[ \t]*\|[\s:|-]+\|[ \t]*$", line))
        is_header = index == 0
        if is_separator or is_header:
            sanitized_lines.append(line)
            continue
        sanitized_cells: list[str] = []
        for cell_index, cell in enumerate(line.split("|")):
            if cell_index <= 1 or not cell.strip() or cell_index in hp_cols:
                sanitized_cells.append(cell)
            else:
                sanitized_cells.append(
                    _SANITIZER_NUM_PAT.sub(
                        lambda num_match: _replace_sanitized_number(num_match, state),
                        cell,
                    )
                )
        sanitized_lines.append("|".join(sanitized_cells))
    return "\n".join(sanitized_lines)


def _sanitize_markdown_tables(paper: str, state: _SanitizationState) -> str:
    return _MARKDOWN_TABLE_PAT.sub(
        lambda match: _sanitize_markdown_table(match, state),
        paper,
    )


def _latex_table_should_skip(context: str) -> bool:
    context_lower = context.lower()
    is_hp = any(keyword in context_lower for keyword in _LATEX_HP_KEYWORDS)
    is_result = any(keyword in context_lower for keyword in _LATEX_RESULT_KEYWORDS)
    is_stat = any(keyword in context_lower for keyword in _LATEX_STAT_KEYWORDS)
    return (is_hp and not is_result) or is_stat


def _sanitize_latex_table(match: re.Match[str], source: str, state: _SanitizationState) -> str:
    block = match.group(0)
    start = match.start()
    context = source[max(0, start - 300):start + 300]
    if _latex_table_should_skip(context):
        return block

    state.tables_processed += 1
    parts = re.split(r"(\\\\)", block)
    result_parts: list[str] = []
    seen_midrule = False
    for part in parts:
        if part == "\\\\":
            result_parts.append(part)
            continue
        stripped = part.strip()
        if re.search(r"\\(hline|toprule|midrule|bottomrule|cline|cmidrule)", stripped):
            if "midrule" in stripped or "hline" in stripped:
                seen_midrule = True
            result_parts.append(part)
            continue
        if r"\begin{tabular}" in part or r"\end{tabular}" in part or not seen_midrule:
            result_parts.append(part)
            continue
        sanitized_cells = [
            cell if index == 0 else _SANITIZER_NUM_PAT.sub(
                lambda num_match: _replace_sanitized_number(num_match, state),
                cell,
            )
            for index, cell in enumerate(part.split("&"))
        ]
        result_parts.append("&".join(sanitized_cells))
    return "".join(result_parts)


def _sanitize_latex_tables(paper: str, state: _SanitizationState) -> str:
    return _LATEX_TABULAR_PAT.sub(
        lambda match: _sanitize_latex_table(match, paper, state),
        paper,
    )


def _replace_prose_number(match: re.Match[str], state: _SanitizationState) -> str:
    num_str = match.group(1)
    try:
        value = float(num_str)
    except ValueError:
        return match.group(0)
    if value in _SANITIZER_ALWAYS_ALLOWED:
        return match.group(0)
    if value == int(value) and abs(value) <= 20:
        return match.group(0)
    if _is_verified_value(value, state.verified_values):
        return match.group(0)
    state.prose_numbers_replaced += 1
    return match.group(0).replace(num_str + (match.group(2) or ""), "[value removed]")


def _sanitize_results_prose(paper: str, state: _SanitizationState) -> str:
    sanitized_lines: list[str] = []
    in_results_section = False
    for line in paper.split("\n"):
        if _RESULTS_SECTION_HEADER_PAT.match(line):
            in_results_section = True
        elif _ANY_MARKDOWN_HEADER_PAT.match(line) and in_results_section:
            header_text = line.lstrip("#").strip().lower()
            if header_text and not any(
                keyword in header_text
                for keyword in ("result", "experiment", "ablation", "evaluation", "comparison")
            ):
                in_results_section = False
        if in_results_section and "|" not in line:
            line = _PROSE_RESULT_PATTERN.sub(
                lambda match: _replace_prose_number(match, state),
                line,
            )
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines)


def _sanitize_fabricated_data(
    paper: str,
    run_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Replace unverified numerical data in result tables and prose."""
    verified_values = _load_verified_values(run_dir)
    if not verified_values:
        report: dict[str, Any] = {
            "sanitized": False,
            "reason": "no verified values found in experiment_summary.json",
            "tables_processed": 0,
            "numbers_replaced": 0,
        }
        return paper, report

    state = _SanitizationState(verified_values)
    sanitized = _sanitize_markdown_tables(paper, state)
    sanitized = _sanitize_latex_tables(sanitized, state)
    sanitized = _sanitize_results_prose(sanitized, state)
    report = {
        "sanitized": state.numbers_replaced > 0 or state.prose_numbers_replaced > 0,
        "tables_processed": state.tables_processed,
        "numbers_replaced": state.numbers_replaced,
        "numbers_kept": state.numbers_kept,
        "prose_numbers_replaced": state.prose_numbers_replaced,
        "verified_values_count": len(verified_values),
        "replaced_samples": state.replaced_values[:20],
        "generated": _utcnow_iso(),
    }
    return sanitized, report



# ---------------------------------------------------------------------------
# BUG-176: Missing citation resolution
# BUG-194: Validate search results to avoid replacing correct entries with
#           garbage.  Previous code searched by cite-key fragments (e.g.
#           "he 2016 deep") which returned completely unrelated papers.
#           Fix: (1) consult seminal_papers.yaml first, (2) require title-
#           similarity validation for API results, (3) build better queries.
# ---------------------------------------------------------------------------


"""Stages 21-23: knowledge archive, export/publish, and citation verification."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml  # noqa: F401 — available for downstream use

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._domain import _detect_domain  # noqa: F401
from researchclaw.pipeline._helpers import (
    StageResult,
    _build_context_preamble,
    _chat_with_prompt,
    _collect_experiment_results,  # noqa: F401
    _extract_paper_title,
    _generate_framework_diagram_prompt,
    _generate_neurips_checklist,
    _get_evolution_overlay,
    _read_best_analysis,
    _read_prior_artifact,
    _safe_json_loads,
    _topic_constraint_block,  # noqa: F401
    _utcnow_iso,
    reconcile_figure_refs,
)
from researchclaw.pipeline.stage_impls.review_publish_citations import (
    load_seminal_papers_by_key,
    resolve_missing_citations,
    seminal_to_bibtex,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger("researchclaw.pipeline.stage_impls._review_publish")


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


def _get_collect_raw_experiment_metrics():
    from researchclaw.pipeline.stage_impls._paper_writing_shared import (
        _collect_raw_experiment_metrics,
    )

    return _collect_raw_experiment_metrics


def _get_review_compiled_pdf():
    from researchclaw.pipeline.stage_impls._paper_writing_shared import (
        _review_compiled_pdf,
    )

    return _review_compiled_pdf

# ---------------------------------------------------------------------------
# Stage 21: Knowledge Archive
# ---------------------------------------------------------------------------

def _execute_knowledge_archive(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    revised = _read_prior_artifact(run_dir, "paper_revised.md") or ""
    analysis = _read_best_analysis(run_dir)
    decision = _read_prior_artifact(run_dir, "decision.md") or ""
    preamble = _build_context_preamble(config, run_dir, include_goal=True)
    if llm is not None:
        _pm = prompts or PromptManager()
        _overlay = _get_evolution_overlay(run_dir, "knowledge_archive")
        sp = _pm.for_stage(
            "knowledge_archive",
            evolution_overlay=_overlay,
            preamble=preamble,
            decision=decision,
            analysis=analysis,
            revised=revised[:15000],
        )
        resp = _chat_with_prompt(
            llm,
            sp.system,
            sp.user,
            json_mode=sp.json_mode,
            max_tokens=sp.max_tokens,
        )
        archive = resp.content
    else:
        archive = f"""# Knowledge Archive

## Lessons Learned
- Preserve strict metric reporting protocol.
- Keep refinement logs aligned with code changes.

## Reproducibility
- Include exact experiment script and schedule.
- Capture run-level JSON metrics.

## Future Work
- Extend robustness and external validity checks.

Generated: {_utcnow_iso()}
"""
    (stage_dir / "archive.md").write_text(archive, encoding="utf-8")

    files: list[str] = []
    for stage_subdir in sorted(run_dir.glob("stage-*")):
        for artifact in sorted(stage_subdir.rglob("*")):
            if artifact.is_file() and artifact != (stage_dir / "bundle_index.json"):
                files.append(str(artifact.relative_to(run_dir)))
    index = {
        "run_id": run_dir.name,
        "generated": _utcnow_iso(),
        "artifact_count": len(files),
        "artifacts": files,
    }
    (stage_dir / "bundle_index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )
    return StageResult(
        stage=Stage.KNOWLEDGE_ARCHIVE,
        status=StageStatus.DONE,
        artifacts=("archive.md", "bundle_index.json"),
        evidence_refs=("stage-21/archive.md", "stage-21/bundle_index.json"),
    )


# ---------------------------------------------------------------------------
# _sanitize_fabricated_data helper
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

def _load_seminal_papers_by_key() -> dict[str, dict]:
    return load_seminal_papers_by_key()


def _seminal_to_bibtex(paper: dict, cite_key: str) -> str:
    return seminal_to_bibtex(paper, cite_key)


def _resolve_missing_citations(
    missing_keys: set[str],
    existing_bib: str,
) -> tuple[set[str], list[str]]:
    return resolve_missing_citations(
        missing_keys,
        existing_bib,
        diagnostic_logger=logger,
    )

# ---------------------------------------------------------------------------
# Stage 22: Export & Publish
# ---------------------------------------------------------------------------

def _execute_export_publish(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    revised = _read_prior_artifact(run_dir, "paper_revised.md") or ""
    if llm is not None:
        _pm = prompts or PromptManager()
        _overlay = _get_evolution_overlay(run_dir, "export_publish")
        sp = _pm.for_stage("export_publish", evolution_overlay=_overlay, revised=revised)
        resp = _chat_with_prompt(
            llm,
            sp.system,
            sp.user,
            json_mode=sp.json_mode,
            max_tokens=sp.max_tokens,
        )
        final_paper = resp.content
        # Content guard: reject LLM output that truncates the paper
        if revised and len(final_paper) < 0.6 * len(revised):
            logger.warning(
                "Stage 22: LLM output is %.0f%% of input length — using original",
                100 * len(final_paper) / max(len(revised), 1),
            )
            final_paper = revised
    else:
        final_paper = revised
    if not final_paper.strip():
        final_paper = "# Final Paper\n\nNo content generated."

    # --- Always-on fabrication sanitization (Phase 1 anti-fabrication) ---
    # Back up pre-sanitized version
    (stage_dir / "paper_presanitized.md").write_text(
        final_paper, encoding="utf-8"
    )

    # Sanitize unverified data in tables — always-on, not just degraded mode
    final_paper, _san_report = _sanitize_fabricated_data(
        final_paper, run_dir
    )
    (stage_dir / "sanitization_report.json").write_text(
        json.dumps(_san_report, indent=2), encoding="utf-8"
    )
    if _san_report.get("numbers_replaced", 0) > 0:
        logger.info(
            "Stage 22: Fabrication sanitization — %d numbers replaced, %d kept",
            _san_report.get("numbers_replaced", 0),
            _san_report.get("numbers_kept", 0),
        )

    # Graceful degradation: insert notice only when quality gate was degraded
    _degradation_signal_path = run_dir / "degradation_signal.json"
    if _degradation_signal_path.exists():
        try:
            _deg_signal = json.loads(
                _degradation_signal_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            _deg_signal = {}

        # Insert degradation notice after abstract
        _deg_score = _deg_signal.get("score", "N/A")
        _deg_threshold = _deg_signal.get("threshold", "N/A")
        _deg_notice = (
            "\n\n> **Note:** This paper was produced in degraded mode. "
            f"Quality gate score ({_deg_score}/{_deg_threshold}) was below "
            "threshold. Unverified numerical results in tables have been "
            "replaced with `---` and require independent verification.\n\n"
        )
        # Try to insert after ## Abstract section
        _abstract_markers = ["## Abstract\n", "# Abstract\n"]
        _notice_inserted = False
        for _marker in _abstract_markers:
            if _marker in final_paper:
                _marker_end = final_paper.index(_marker) + len(_marker)
                # Find the end of the abstract paragraph
                _next_section = final_paper.find("\n## ", _marker_end)
                _next_heading = final_paper.find("\n# ", _marker_end)
                _insert_pos = min(
                    p for p in (_next_section, _next_heading)
                    if p > 0
                ) if any(p > 0 for p in (_next_section, _next_heading)) else len(final_paper)
                final_paper = (
                    final_paper[:_insert_pos]
                    + _deg_notice
                    + final_paper[_insert_pos:]
                )
                _notice_inserted = True
                break
        if not _notice_inserted:
            # Fallback: prepend to paper
            final_paper = _deg_notice + final_paper

        logger.info(
            "Stage 22: Applied degraded-mode notice (score=%s, threshold=%s)",
            _deg_score, _deg_threshold,
        )

    # IMP-3: Deduplicate "due to computational constraints" — keep at most 1
    _CONSTRAINT_PAT = re.compile(
        r"[Dd]ue to computational constraints", re.IGNORECASE
    )
    _matches = list(_CONSTRAINT_PAT.finditer(final_paper))
    if len(_matches) > 1:
        # Keep only the first occurrence; remove subsequent ones by
        # deleting the enclosing sentence.
        for m in reversed(_matches[1:]):
            # Find sentence boundaries around the match
            start = final_paper.rfind(".", 0, m.start())
            start = start + 1 if start >= 0 else m.start()
            end = final_paper.find(".", m.end())
            end = end + 1 if end >= 0 else m.end()
            sentence = final_paper[start:end].strip()
            if sentence:
                final_paper = final_paper[:start] + final_paper[end:]
        final_paper = re.sub(r"[^\S\n]{2,}", " ", final_paper)
        logger.info(
            "Stage 22: Removed %d duplicate 'computational constraints' "
            "disclaimers",
            len(_matches) - 1,
        )

    # IMP-19 Layer 2: Ensure at least figures are referenced in the paper
    chart_files = []
    # BUG-215: Also search stage-14* versioned dirs (stage-14_v1, etc.)
    # in case stage-14/ was renamed and never recreated.
    _chart_search_dirs = [stage_dir / "charts", run_dir / "stage-14" / "charts"]
    for _s14_charts in sorted(run_dir.glob("stage-14*/charts"), reverse=True):
        if _s14_charts not in _chart_search_dirs:
            _chart_search_dirs.append(_s14_charts)
    for _chart_src_dir in _chart_search_dirs:
        if _chart_src_dir.is_dir():
            chart_files.extend(sorted(_chart_src_dir.glob("*.png")))
    # BUG-190: Also inject charts not already referenced in the paper.
    # The old condition only fired when NO figures were present. Now we
    # filter to only unreferenced charts, so partially-illustrated papers
    # also get the remaining charts injected.
    _already_referenced = set()
    for _cf in chart_files:
        if _cf.name in final_paper:
            _already_referenced.add(_cf.name)
    chart_files = [cf for cf in chart_files if cf.name not in _already_referenced]
    if chart_files:
        # Distribute figures to relevant sections based on filename keywords
        _fig_placement: dict[str, list[str]] = {
            "method": [],       # architecture, method, model, pipeline diagrams
            "result": [],       # experiment, comparison, ablation charts
            "intro": [],        # concept, overview, illustration
        }
        _fig_counter = len(_already_referenced)  # start numbering after existing figs
        for cf in chart_files[:6]:
            _fig_counter += 1
            stem_lower = cf.stem.lower()
            label = cf.stem.replace("_", " ").title()
            fig_md = f"![Figure {_fig_counter}: {label}](charts/{cf.name})"
            if any(k in stem_lower for k in ("architecture", "model", "pipeline", "method", "flowchart")):
                _fig_placement["method"].append(fig_md)
            elif any(k in stem_lower for k in ("experiment", "comparison", "ablation", "result", "metric")):
                _fig_placement["result"].append(fig_md)
            elif any(k in stem_lower for k in ("concept", "overview", "illustration", "threat", "attack")):
                _fig_placement["intro"].append(fig_md)
            else:
                _fig_placement["result"].append(fig_md)  # default to results

        # Insert figures at relevant section boundaries.
        # BUG-200: Match both H1 (#) and H2 (##) headings — LLMs generate
        # either level depending on the writing_structure prompt.
        _section_markers = {
            "method": ["# Method", "## Method", "# Methodology", "## Methodology",
                        "# Approach", "## Approach", "# Framework", "## Framework",
                        "## 3. Method", "## 3 Method"],
            "result": ["# Results", "## Results", "# Experiments", "## Experiments",
                        "# Evaluation", "## Evaluation",
                        "## 5. Results", "## 4. Experiments", "## 5 Results"],
            "intro": ["# Related Work", "## Related Work", "# Background",
                       "## Background", "## 2. Related", "## 2 Related Work"],
        }
        _total_inserted = 0
        for category, figs in _fig_placement.items():
            if not figs:
                continue
            fig_block = "\n\n" + "\n\n".join(figs) + "\n\n"
            inserted = False
            for marker in _section_markers.get(category, []):
                if marker in final_paper:
                    # Insert BEFORE the marker section (so figure appears at end of previous section)
                    final_paper = final_paper.replace(marker, fig_block + marker, 1)
                    inserted = True
                    _total_inserted += len(figs)
                    break
            if not inserted:
                # Fallback: insert before Conclusion/Limitations/Discussion
                for fallback in ["# Conclusion", "## Conclusion",
                                 "# Limitations", "## Limitations",
                                 "# Discussion", "## Discussion"]:
                    if fallback in final_paper:
                        final_paper = final_paper.replace(fallback, fig_block + fallback, 1)
                        inserted = True
                        _total_inserted += len(figs)
                        break
            if not inserted:
                # BUG-200: Last resort — insert before closing fence marker
                # rather than appending after it (which puts content outside
                # the markdown fence and gets dropped by converter).
                _fence_end = final_paper.rfind("\n```")
                if _fence_end > 0:
                    final_paper = (
                        final_paper[:_fence_end] + fig_block + final_paper[_fence_end:]
                    )
                else:
                    final_paper += fig_block
                _total_inserted += len(figs)

        logger.info(
            "IMP-19: Injected %d figure references into paper_final.md (distributed across sections)",
            _total_inserted,
        )

    # IMP-24: Detect excessive number repetition
    _numbers_found = re.findall(r"\b\d+\.\d{2,}\b", final_paper)
    _num_counts = Counter(_numbers_found)
    _repeated = {n: c for n, c in _num_counts.items() if c > 3}
    if _repeated:
        logger.warning(
            "IMP-24: Numbers repeated >3 times: %s",
            _repeated,
        )

    (stage_dir / "paper_final.md").write_text(final_paper, encoding="utf-8")

    # --- Legacy fabrication sanitization (disabled — superseded by Phase 1 _sanitize_fabricated_data above) ---
    # Kept but guarded: Phase 1 always-on sanitization handles this now.
    # Only run if Phase 1 was somehow skipped (should never happen).
    _fab_flags_text = _read_prior_artifact(run_dir, "fabrication_flags.json") or ""
    _fab_flags = _safe_json_loads(_fab_flags_text, {}) if _fab_flags_text else {}
    if (
        isinstance(_fab_flags, dict)
        and _fab_flags.get("fabrication_suspected")
        and _san_report.get("numbers_replaced", 0) == 0  # Phase 1 didn't run/replace
    ):
        _real_vals = set()
        for rv in _fab_flags.get("real_metric_values", []):
            if isinstance(rv, (int, float)) and math.isfinite(rv):
                _real_vals.add(str(round(rv, 4)))
                _real_vals.add(str(round(rv, 2)))
                _real_vals.add(str(round(rv, 1)))
                if rv == int(rv):
                    _real_vals.add(str(int(rv)))

        def _sanitize_number(m: re.Match) -> str:  # type: ignore[name-defined]
            """Replace fabricated numbers with '--' but keep real ones."""
            num_str = m.group(0)
            # Keep the number if it matches any known real metric value
            try:
                num_val = float(num_str)
                if not math.isfinite(num_val):
                    return "--"
                rounded_strs = {
                    str(round(num_val, 4)),
                    str(round(num_val, 2)),
                    str(round(num_val, 1)),
                    *(
                        [str(int(num_val))] if num_val == int(num_val) else []
                    ),
                }
                if rounded_strs & _real_vals:
                    return num_str  # real value — keep it
            except (ValueError, OverflowError):
                return num_str
            return "--"

        # Only sanitize numbers in Results/Experiments/Evaluation/Ablation sections
        _result_section_pat = re.compile(
            r"(##\s*(?:\d+\.?\s*)?(?:Results|Experiments|Evaluation|Ablation"
            r"|Experimental Results|Quantitative).*?)(?=\n##\s|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        _sanitized_count = 0

        def _sanitize_section(sec_match: re.Match) -> str:  # type: ignore[name-defined]
            nonlocal _sanitized_count
            section_text = sec_match.group(0)
            # Replace decimal numbers (e.g., 73.42, 0.891) but NOT integers
            # that are likely structural (year, section number, figure number)
            def _replace_in_section(m: re.Match) -> str:  # type: ignore[name-defined]
                nonlocal _sanitized_count
                result = _sanitize_number(m)
                if result == "--":
                    _sanitized_count += 1
                return result
            return re.sub(
                r"\b\d+\.\d{1,6}\b", _replace_in_section, section_text
            )

        final_paper = _result_section_pat.sub(_sanitize_section, final_paper)

        if _sanitized_count > 0:
            logger.warning(
                "Stage 22: Fabrication sanitization — blanked %d unsupported "
                "numbers in Results sections (experiment had no real metrics)",
                _sanitized_count,
            )
            # Rewrite the sanitized paper
            (stage_dir / "paper_final.md").write_text(
                final_paper, encoding="utf-8"
            )

    # Initialize artifacts list
    artifacts = ["paper_final.md"]
    # F2.7: Post-process citations — [cite_key] → \cite{cite_key}
    # and copy final references.bib to export stage
    _ay_map: dict[str, str] = {}  # BUG-102: author-year → cite_key map
    final_paper_latex = final_paper  # default when no bib_text available
    bib_text = _read_prior_artifact(run_dir, "references.bib")
    if bib_text:
        # Replace [cite_key] patterns in the final paper with \cite{cite_key}
        # Collect all valid cite_keys from the bib file
        valid_keys = set(re.findall(r"@\w+\{([^,]+),", bib_text))

        # BUG-102: Recover author-year citations → [cite_key] format.
        # When Stage 19 (paper_revision) converts [cite_key] to [Author et al., 2024],
        # the downstream regex can't match them. Build a reverse map from bib entries.
        def _build_author_year_map(bib: str, keys: set[str]) -> dict[str, str]:
            """Build mapping from author-year patterns to cite_keys.

            Returns dict like:
              "Raissi et al., 2019" → "raissi2019physicsinformed"
              "Tavella and Randall, 2000" → "tavella2000pricing"
            """
            mapping: dict[str, str] = {}
            # Parse each bib entry for author + year
            # BUG-DA8-17: Allow newline OR whitespace before closing brace
            # Use \n} or just } at start-of-line to avoid greedy cross-entry match
            entry_pat = re.compile(
                r"@\w+\{([^,]+),\s*(.*?)(?:\n\}|^[ \t]*\})", re.DOTALL | re.MULTILINE
            )
            for m in entry_pat.finditer(bib):
                key = m.group(1).strip()
                if key not in keys:
                    continue
                body = m.group(2)
                # Extract author field
                author_m = re.search(
                    r"author\s*=\s*[\{\"](.*?)[\}\"]", body, re.IGNORECASE
                )
                year_m = re.search(
                    r"year\s*=\s*[\{\"]?(\d{4})[\}\"]?", body, re.IGNORECASE
                )
                if not author_m or not year_m:
                    continue
                author_raw = author_m.group(1).strip()
                year = year_m.group(1)
                # Parse author names (split on " and ")
                authors = [a.strip() for a in re.split(r"\s+and\s+", author_raw)]
                # Extract last names
                last_names = []
                for a in authors:
                    if "," in a:
                        last_names.append(a.split(",")[0].strip())
                    else:
                        parts = a.split()
                        last_names.append(parts[-1] if parts else a)
                if not last_names:
                    continue
                # Generate author-year patterns:
                # 1 author: "Smith, 2024"
                # 2 authors: "Smith and Jones, 2024"
                # 3+ authors: "Smith et al., 2024"
                if len(last_names) == 1:
                    patterns = [f"{last_names[0]}, {year}"]
                elif len(last_names) == 2:
                    patterns = [
                        f"{last_names[0]} and {last_names[1]}, {year}",
                        f"{last_names[0]} \\& {last_names[1]}, {year}",
                    ]
                else:
                    patterns = [
                        f"{last_names[0]} et al., {year}",
                        f"{last_names[0]} et al. {year}",
                    ]
                    # Also add "Smith and Jones, 2024" for first two authors
                    patterns.append(
                        f"{last_names[0]} and {last_names[1]}, {year}"
                    )
                for pat in patterns:
                    mapping[pat] = key
            return mapping

        _ay_map = _build_author_year_map(bib_text, valid_keys)
        if _ay_map:
            # Count how many author-year citations exist in the paper
            _ay_found = 0
            for _ay_pat in _ay_map:
                if _ay_pat in final_paper:
                    _ay_found += 1
            if _ay_found > 0:
                logger.info(
                    "Stage 22: Found %d author-year citation patterns — "
                    "converting back to [cite_key] format.",
                    _ay_found,
                )
                # Sort by longest pattern first to avoid partial matches
                for _ay_pat in sorted(_ay_map, key=len, reverse=True):
                    _ay_key = _ay_map[_ay_pat]
                    # Match [Author et al., 2024] or [Author and Jones, 2024; ...]
                    # Handle single-citation brackets
                    final_paper = final_paper.replace(
                        f"[{_ay_pat}]", f"[{_ay_key}]"
                    )
                    # Handle within multi-citation brackets [A et al., 2020; B et al., 2021]
                    # Replace the author-year segment only inside [...] brackets
                    final_paper = re.sub(
                        r'\[([^\]]*?)' + re.escape(_ay_pat) + r'([^\]]*?)\]',
                        lambda _m: '[' + _m.group(1) + _ay_key + _m.group(2) + ']',
                        final_paper,
                    )
                # Fix multi-key brackets: [key1; key2] → [key1, key2]
                # (author-year uses semicolons, cite-keys use commas)
                def _fix_semicolon_cites(m_sc: re.Match[str]) -> str:
                    inner = m_sc.group(1)
                    # Only convert if ALL segments look like cite keys
                    parts = [p.strip() for p in inner.split(";")]
                    _ck = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9_]*"
                    if all(re.fullmatch(_ck, p) for p in parts):
                        return "[" + ", ".join(parts) + "]"
                    return m_sc.group(0)
                final_paper = re.sub(
                    r"\[([^\]]+;[^\]]+)\]", _fix_semicolon_cites, final_paper
                )
                (stage_dir / "paper_final.md").write_text(
                    final_paper, encoding="utf-8"
                )

        # R10-Fix4: Citation cross-validation
        # BUG-187: Also parse multi-key brackets like [key1, key2, key3].
        # The old regex only matched single-key brackets [key2020word].
        _cite_key_pat = r"[a-zA-Z]+\d{4}[a-zA-Z0-9_-]*"
        cited_keys_in_paper: set[str] = set()
        # Single-key brackets
        for m in re.finditer(rf"\[({_cite_key_pat})\]", final_paper):
            cited_keys_in_paper.add(m.group(1))
        # Multi-key brackets [key1, key2] or [key1; key2]
        for m in re.finditer(r"\[([^\]]{10,300})\]", final_paper):
            inner = m.group(1)
            # Only parse if it looks like citation keys (has year-like digits)
            parts = re.split(r"[,;]\s*", inner)
            if all(re.fullmatch(_cite_key_pat, p.strip()) for p in parts if p.strip()):
                for p in parts:
                    if p.strip():
                        cited_keys_in_paper.add(p.strip())

        if valid_keys and cited_keys_in_paper:
            invalid_keys = cited_keys_in_paper - valid_keys
            if invalid_keys:
                logger.warning(
                    "Stage 22: Found %d citation keys in paper not in references.bib: %s",
                    len(invalid_keys),
                    ", ".join(sorted(invalid_keys)[:20]),
                )
                # BUG-176: Try to resolve missing citations before removing them.
                # Parse cite_key → search query, look up via academic APIs,
                # and add found entries to references.bib.
                resolved_keys: set[str] = set()
                new_bib_entries: list[str] = []
                if len(invalid_keys) <= 30:  # Sanity: don't flood APIs
                    resolved_keys, new_bib_entries = _resolve_missing_citations(
                        invalid_keys, bib_text
                    )
                    if resolved_keys:
                        valid_keys.update(resolved_keys)
                        bib_text += "\n" + "\n\n".join(new_bib_entries) + "\n"
                        logger.info(
                            "Stage 22: Resolved %d/%d missing citations via API lookup",
                            len(resolved_keys), len(invalid_keys),
                        )

                still_invalid = invalid_keys - resolved_keys
                if still_invalid:
                    # IMP-29: Remove remaining unresolvable citations from
                    # BOTH single-key and multi-key brackets.
                    for bad_key in still_invalid:
                        # Remove single-key brackets
                        final_paper = final_paper.replace(f"[{bad_key}]", "")
                        # Remove from multi-key brackets: [good, BAD, good] → [good, good]
                        def _remove_from_multi(m: re.Match) -> str:
                            inner = m.group(1)
                            parts = [p.strip() for p in re.split(r"[,;]\s*", inner)]
                            filtered = [p for p in parts if p != bad_key]
                            if not filtered:
                                return ""
                            return "[" + ", ".join(filtered) + "]"
                        final_paper = re.sub(
                            r"\[([^\]]*\b" + re.escape(bad_key) + r"\b[^\]]*)\]",
                            _remove_from_multi,
                            final_paper,
                        )
                    # Clean up whitespace artifacts from removed citations
                    final_paper = re.sub(r"  +", " ", final_paper)
                    final_paper = re.sub(r" ([.,;:)])", r"\1", final_paper)
                (stage_dir / "paper_final.md").write_text(final_paper, encoding="utf-8")
                if still_invalid:
                    (stage_dir / "invalid_citations.json").write_text(
                        json.dumps(sorted(still_invalid), indent=2), encoding="utf-8"
                    )
                    artifacts.append("invalid_citations.json")
                if resolved_keys:
                    (stage_dir / "resolved_citations.json").write_text(
                        json.dumps(sorted(resolved_keys), indent=2), encoding="utf-8"
                    )
                    artifacts.append("resolved_citations.json")

        final_paper_latex = final_paper  # default: no citation conversion
        if valid_keys:
            _CITE_KEY_PAT = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9]*"

            # Step 1: Convert multi-key brackets [key1, key2] → \cite{key1, key2}
            def _replace_multi_cite(m: re.Match[str]) -> str:
                keys = [k.strip() for k in m.group(1).split(",")]
                matched = [k for k in keys if k in valid_keys]
                if matched:
                    return "\\cite{" + ", ".join(matched) + "}"
                return m.group(0)

            final_paper_latex = re.sub(
                rf"\[({_CITE_KEY_PAT}(?:\s*,\s*{_CITE_KEY_PAT})+)\]",
                _replace_multi_cite,
                final_paper,
            )

            # Step 2: Convert single-key brackets [key] → \cite{key}
            def _replace_cite(m: re.Match[str]) -> str:
                key = m.group(1)
                if key in valid_keys:
                    return f"\\cite{{{key}}}"
                return m.group(0)

            final_paper_latex = re.sub(
                rf"\[({_CITE_KEY_PAT})\]", _replace_cite, final_paper_latex
            )

            # Step 3: Merge adjacent \cite{a} \cite{b} → \cite{a, b}
            def _merge_adjacent_cites(m: re.Match[str]) -> str:
                keys = re.findall(r"\\cite\{([^}]+)\}", m.group(0))
                return "\\cite{" + ", ".join(keys) + "}"

            final_paper_latex = re.sub(
                r"\\cite\{[^}]+\}(?:\s*\\cite\{[^}]+\})+",
                _merge_adjacent_cites,
                final_paper_latex,
            )

            (stage_dir / "paper_final_latex.md").write_text(
                final_paper_latex, encoding="utf-8"
            )
            artifacts.append("paper_final_latex.md")
        # IMP-1: Prune uncited bibliography entries — keep only keys
        # that actually appear in the paper text (bracket or \cite form).
        if valid_keys:
            _all_cited: set[str] = set()
            # Bracket-format citations [key]
            _all_cited.update(
                re.findall(r"\[([a-zA-Z]+\d{4}[a-zA-Z0-9_-]*)\]", final_paper)
            )
            # \cite{key, key2} format (original + latex-converted)
            for _src in (
                final_paper,
                final_paper_latex,
            ):
                for _cm in re.finditer(r"\\cite\{([^}]+)\}", _src):
                    _all_cited.update(
                        k.strip() for k in _cm.group(1).split(",")
                    )
            uncited_keys = valid_keys - _all_cited
            if uncited_keys:
                bib_text = _remove_bibtex_entries(bib_text, uncited_keys)
                logger.info(
                    "Stage 22: Pruned %d uncited bibliography entries "
                    "(kept %d)",
                    len(uncited_keys),
                    len(valid_keys) - len(uncited_keys),
                )

        # Write final references.bib
        (stage_dir / "references.bib").write_text(bib_text, encoding="utf-8")
        artifacts.append("references.bib")
        logger.info(
            "Stage 22: Exported references.bib with %d entries",
            len(valid_keys) if valid_keys else 0,
        )

    # Conference template: generate .tex file
    try:
        from researchclaw.templates import get_template, markdown_to_latex

        tpl = get_template(config.export.target_conference)
        # Use the latex-citation-processed version if available
        tex_source = final_paper_latex
        # Append NeurIPS-style checklist if target is a ML conference
        if tpl.name in ("neurips_2024", "neurips_2025", "icml_2025", "icml_2026",
                         "iclr_2025", "iclr_2026"):
            _has_exp = bool(_read_prior_artifact(run_dir, "experiment_summary.json"))
            _checklist = _generate_neurips_checklist(
                has_experiments=_has_exp,
                has_code=True,
            )
            if "NeurIPS Paper Checklist" not in tex_source:
                tex_source = tex_source.rstrip() + "\n\n" + _checklist
        _t = _extract_paper_title(tex_source)
        tex_content = markdown_to_latex(
            tex_source,
            tpl,
            title=_t if _t != "Untitled Paper" else "",
            authors=config.export.authors,
            bib_file=config.export.bib_file,
            bib_entries=_ay_map or None,
        )
        (stage_dir / "paper.tex").write_text(tex_content, encoding="utf-8")
        artifacts.append("paper.tex")
        logger.info(
            "Stage 22: Generated paper.tex for %s (%d chars)",
            tpl.display_name,
            len(tex_content),
        )
        # --- Phase 1 anti-fabrication: verify paper against VerifiedRegistry ---
        _vresult = None  # BUG-DA8-04: Initialize before try to avoid fragile dir() check
        try:
            from researchclaw.pipeline.paper_verifier import verify_paper as _verify_paper

            # BUG-222: Use best_only=True to validate against promoted best data only
            from researchclaw.pipeline.verified_registry import (
                VerifiedRegistry as _VR22,
            )
            _vr22 = _VR22.from_run_dir(
                run_dir,
                metric_direction=config.experiment.metric_direction,
                best_only=True,
            )
            if _vr22.values:
                _vresult = _verify_paper(tex_content, _vr22)
                (stage_dir / "paper_verification.json").write_text(
                    json.dumps({
                        "passed": _vresult.passed,
                        "severity": _vresult.severity,
                        "total_checked": _vresult.total_numbers_checked,
                        "total_verified": _vresult.total_numbers_verified,
                        "strict_violations": _vresult.strict_violations,
                        "lenient_violations": _vresult.lenient_violations,
                        "fabrication_rate": round(_vresult.fabrication_rate, 4),
                        "unverified_numbers": [
                            {"value": u.value, "line": u.line_number,
                             "section": u.section, "in_table": u.in_table}
                            for u in _vresult.unverified_numbers[:20]
                        ],
                        "fabricated_conditions": [
                            {"name": fc.name, "line": fc.line_number}
                            for fc in _vresult.fabricated_conditions
                        ],
                        "config_warnings": getattr(_vresult, "config_warnings", []),
                        "summary": _vresult.summary,
                    }, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "Stage 22: Paper verification — %s (%d checked, %d verified, "
                    "%d strict violations, fabrication_rate=%.1f%%)",
                    _vresult.severity,
                    _vresult.total_numbers_checked,
                    _vresult.total_numbers_verified,
                    _vresult.strict_violations,
                    _vresult.fabrication_rate * 100,
                )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as _pv_exc:
            logger.debug("Stage 22: Paper verification skipped: %s", _pv_exc)

        # BUG-23 P1: Enforce REJECT verdict — sanitize unverified numbers
        if _vresult is not None and getattr(_vresult, "severity", None) == "REJECT":
            logger.warning(
                "Stage 22: Paper REJECTED by verifier (fabrication_rate=%.1f%%, "
                "%d strict violations). Sanitizing unverified numbers.",
                _vresult.fabrication_rate * 100,
                _vresult.strict_violations,
            )
            # BUG-R49-02: Section names that sound like results but are
            # actually protocol/setup sections should NOT trigger strict
            # sanitization.  Exempt sections containing "dataset", "setup",
            # "protocol", "hyperparameter", or "implementation".
            _STRICT_EXEMPT_KW = {"dataset", "setup", "protocol",
                                 "hyperparameter", "implementation",
                                 "hardware", "infrastructure"}

            _sanitized_tex = tex_content
            _san2_count = 0
            for _uv in sorted(_vresult.unverified_numbers, key=lambda u: -u.line_number):
                # Only sanitize strict-section / in-table numbers
                _uv_section_lower = (_uv.section or "").lower()
                _uv_is_strict = any(
                    s in _uv_section_lower
                    for s in ("results", "experiment", "evaluation",
                              "ablation", "comparison", "analysis")
                )
                # BUG-R49-02: Exempt protocol/setup sections from strict mode
                if _uv_is_strict and any(
                    kw in _uv_section_lower for kw in _STRICT_EXEMPT_KW
                ):
                    _uv_is_strict = False
                if _uv_is_strict or _uv.in_table:
                    _lines = _sanitized_tex.split("\n")
                    if 0 < _uv.line_number <= len(_lines):
                        _orig_line = _lines[_uv.line_number - 1]
                        # BUG-R49-01: Use word-boundary regex instead of
                        # naive substring matching to avoid replacing numbers
                        # inside identifiers (e.g. "18" in "ResNet18").
                        # BUG-206: Include ASCII hyphen and Unicode hyphens
                        # (U+2010 hyphen, U+2011 non-breaking hyphen,
                        # U+2013 en-dash) so that model variant numbers
                        # like "34" in "ResNet-34" or "ResNet‑34" are not
                        # mistaken for unverified experimental values.
                        # BUG-210: Include period (.) so that fractional
                        # parts of decimals in condition names like
                        # "ema_decay_0.9" are not treated as standalone
                        # numbers (prevents "0.9" → "0.---").
                        _BOUNDARY = "A-Za-z0-9_\u2010\u2011\u2013\\-."
                        for _rep in (
                            f"{_uv.value:.4f}".rstrip("0").rstrip("."),
                            f"{_uv.value:.3f}",
                            f"{_uv.value:.2f}",
                            f"{_uv.value:.1f}",
                            f"{_uv.value:g}",
                            str(_uv.value),
                        ):
                            # Word boundary: number must NOT be adjacent to
                            # alphanumeric, underscore, or hyphen on either side.
                            _pat = (
                                rf"(?<![{_BOUNDARY}])"
                                + re.escape(_rep)
                                + rf"(?![{_BOUNDARY}])"
                            )
                            if re.search(_pat, _orig_line):
                                _lines[_uv.line_number - 1] = re.sub(
                                    _pat, "---", _orig_line, count=1,
                                )
                                _san2_count += 1
                                break
                        _sanitized_tex = "\n".join(_lines)
            if _sanitized_tex != tex_content:
                tex_content = _sanitized_tex
                (stage_dir / "paper.tex").write_text(tex_content, encoding="utf-8")
                logger.info(
                    "Stage 22: Sanitized paper.tex — replaced %d unverified "
                    "numbers with '---'",
                    _san2_count,
                )

        # Copy bundled style files alongside paper.tex
        for sf in tpl.get_style_files():
            import shutil as _shutil_sty
            _shutil_sty.copy2(sf, stage_dir / sf.name)

        # --- Pre-compilation: copy charts and fix figure paths ---
        # BUG-R41-12: Charts MUST be available before compile_latex(),
        # otherwise \includegraphics references fail → "Float(s) lost".
        try:
            chart_dir = stage_dir / "charts"
            chart_dir.mkdir(parents=True, exist_ok=True)
            charts: list[Path] = []

            # Copy FigureAgent charts from stage-14 (any version)
            _fa_charts_found = False
            for _fa_dir in sorted(run_dir.glob("stage-14*/charts"), reverse=True):
                _fa_pngs = list(_fa_dir.glob("fig_*.png"))
                if _fa_pngs:
                    import shutil
                    for _fa_png in _fa_pngs:
                        dest = chart_dir / _fa_png.name
                        shutil.copy2(_fa_png, dest)
                        charts.append(dest)
                    _fa_charts_found = True
                    logger.info(
                        "Stage 22: Copied %d FigureAgent charts from %s",
                        len(_fa_pngs), _fa_dir,
                    )
                    break

            # Generate structured charts from visualize.py
            from researchclaw.experiment.visualize import generate_all_charts
            _metric_dir = getattr(config.experiment, "metric_direction", "minimize")
            _viz_charts = generate_all_charts(
                run_dir,
                chart_dir,
                metric_key=config.experiment.metric_key,
                metric_direction=_metric_dir,
            )
            charts.extend(_viz_charts)

            if charts:
                artifacts.append("charts/")
                logger.info("Stage 22: Generated %d chart(s) total", len(charts))
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            AttributeError,
            LookupError,
        ) as exc:
            logger.warning("Chart generation failed: %s", exc, exc_info=True)

        # BUG-99: Fix \includegraphics paths that don't match actual chart files
        try:
            reconcile_figure_refs(stage_dir / "paper.tex", stage_dir / "charts")
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.debug("Stage 22: Figure path validation skipped")

        # BUG-R41-12: Remove figure blocks referencing files that still don't exist
        try:
            tex_path = stage_dir / "paper.tex"
            if tex_path.exists():
                from researchclaw.templates.compiler import remove_missing_figures
                _tex_text = tex_path.read_text(encoding="utf-8")
                _fixed_tex, _removed_figs = remove_missing_figures(_tex_text, stage_dir)
                if _removed_figs:
                    tex_path.write_text(_fixed_tex, encoding="utf-8")
                    logger.warning(
                        "Stage 22: Removed %d figure block(s) with missing images: %s",
                        len(_removed_figs), _removed_figs,
                    )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.debug("Stage 22: remove_missing_figures skipped")

        # Compile verification
        try:
            from researchclaw.templates.compiler import compile_latex
            _compile_result = compile_latex(stage_dir / "paper.tex", max_attempts=2)
            if _compile_result.success:
                logger.info("Stage 22: LaTeX compilation verification PASSED")
                artifacts.append("paper.pdf")
                # PDF-as-reviewer: LLM-based visual review of compiled PDF
                _pdf_path = stage_dir / "paper.pdf"
                if _pdf_path.exists() and llm is not None:
                    try:
                        _pdf_review = _get_review_compiled_pdf()(
                            _pdf_path, llm, config.research.topic
                        )
                        if _pdf_review:
                            (stage_dir / "pdf_review.json").write_text(
                                json.dumps(_pdf_review, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                            artifacts.append("pdf_review.json")
                            _pdf_score = _pdf_review.get("overall_score", 0)
                            if _pdf_score < 5:
                                logger.warning(
                                    "Stage 22: PDF visual review score %d/10 — %s",
                                    _pdf_score,
                                    _pdf_review.get("summary", ""),
                                )
                            else:
                                logger.info(
                                    "Stage 22: PDF visual review score %d/10",
                                    _pdf_score,
                                )
                    except (RuntimeError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as _pdf_exc:
                        logger.debug("Stage 22: PDF review skipped: %s", _pdf_exc)
                # Post-compilation quality checks
                try:
                    from researchclaw.templates.compiler import check_compiled_quality
                    _qc = check_compiled_quality(stage_dir / "paper.tex")
                    if _qc.warnings_summary:
                        logger.warning(
                            "Stage 22: Quality checks: %s",
                            "; ".join(_qc.warnings_summary),
                        )
                    (stage_dir / "compilation_quality.json").write_text(
                        json.dumps({
                            "page_count": _qc.page_count,
                            "unresolved_refs": _qc.unresolved_refs,
                            "unresolved_cites": _qc.unresolved_cites,
                            "overfull_hboxes": len(_qc.overfull_hboxes),
                            "orphan_figures": _qc.orphan_figures,
                            "orphan_labels": _qc.orphan_labels,
                            "warnings": _qc.warnings_summary,
                        }, indent=2),
                        encoding="utf-8",
                    )
                    artifacts.append("compilation_quality.json")
                    # BUG-27: Warn if page count exceeds limit
                    _page_limit = 10
                    if _qc.page_count and _qc.page_count > _page_limit:
                        logger.warning(
                            "BUG-27: Paper is %d pages (limit %d). "
                            "Consider tightening content in revision.",
                            _qc.page_count, _page_limit,
                        )
                except (ImportError, OSError, RuntimeError, TypeError, ValueError) as _qc_exc:
                    logger.debug("Stage 22: Quality checks skipped: %s", _qc_exc)
            else:
                logger.warning("Stage 22: LaTeX compilation verification FAILED: %s", _compile_result.errors[:3])
                # Add compilation failure comment to .tex
                _tex_path = stage_dir / "paper.tex"
                if _tex_path.exists():
                    _tex_content = _tex_path.read_text(encoding="utf-8")
                    if "% WARNING: Compilation failed" not in _tex_content:
                        _tex_content = (
                            "% WARNING: Compilation failed. Errors:\n"
                            + "".join(f"% {e}\n" for e in _compile_result.errors[:5])
                            + _tex_content
                        )
                        _tex_path.write_text(_tex_content, encoding="utf-8")
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as _compile_exc:
            logger.debug("Stage 22: Compile verification skipped: %s", _compile_exc)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("LaTeX generation failed: %s", exc, exc_info=True)

    # (Charts, BUG-99 path fix, and remove_missing_figures are now handled
    #  BEFORE compile_latex() — see "Pre-compilation" block above.)

    # --- Code packaging: multi-file directory or single file ---
    exp_final_dir_path = _read_prior_artifact(run_dir, "experiment_final/")
    if exp_final_dir_path and Path(exp_final_dir_path).is_dir():
        import ast

        code_dir = stage_dir / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        all_code_combined = ""
        code_file_names: list[str] = []
        for src in sorted(Path(exp_final_dir_path).glob("*.py")):
            (code_dir / src.name).write_bytes(src.read_bytes())
            all_code_combined += src.read_text(encoding="utf-8") + "\n"
            code_file_names.append(src.name)

        # Detect dependencies from all files
        detected: set[str] = set()
        known_packages = {
            "numpy": "numpy",
            "torch": "torch",
            "tensorflow": "tensorflow",
            "sklearn": "scikit-learn",
            "scikit-learn": "scikit-learn",
            "scipy": "scipy",
            "pandas": "pandas",
            "matplotlib": "matplotlib",
            "seaborn": "seaborn",
            "transformers": "transformers",
            "datasets": "datasets",
            "jax": "jax",
        }
        try:
            tree = ast.parse(all_code_combined)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in known_packages:
                            detected.add(known_packages[top])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    if top in known_packages:
                        detected.add(known_packages[top])
        except (SyntaxError, OSError, UnicodeDecodeError, UnicodeError):
            logger.debug(
                "Failed to parse packaged experiment files for dependency detection",
                exc_info=True,
            )

        requirements = sorted(detected)
        (code_dir / "requirements.txt").write_text(
            "\n".join(requirements) + ("\n" if requirements else ""),
            encoding="utf-8",
        )

        paper_title = _extract_paper_title(final_paper)
        file_list_md = "\n".join(f"- `{f}`" for f in code_file_names)
        readme = (
            f"# Code Package for {paper_title}\n\n"
            "## Description\n"
            "This directory contains the experiment project used for the paper.\n\n"
            "## Project Files\n"
            f"{file_list_md}\n\n"
            "## How to Run\n"
            "`python main.py`\n\n"
            "## Dependencies\n"
            "Install dependencies with `pip install -r requirements.txt` if needed.\n"
        )
        (code_dir / "README.md").write_text(readme, encoding="utf-8")
        artifacts.append("code/")
        logger.info(
            "Stage 22: Packaged multi-file code release (%d files, %d deps)",
            len(code_file_names),
            len(requirements),
        )
    else:
        # Backward compat: single-file packaging
        code_payload = _read_prior_artifact(run_dir, "experiment_final.py")
        if not code_payload:
            code_payload = _read_prior_artifact(run_dir, "experiment.py")
        if code_payload:
            import ast

            code_dir = stage_dir / "code"
            code_dir.mkdir(parents=True, exist_ok=True)
            (code_dir / "experiment.py").write_text(code_payload, encoding="utf-8")

            detected_single: set[str] = set()
            known_packages_single = {
                "numpy": "numpy",
                "torch": "torch",
                "tensorflow": "tensorflow",
                "sklearn": "scikit-learn",
                "scikit-learn": "scikit-learn",
                "scipy": "scipy",
                "pandas": "pandas",
                "matplotlib": "matplotlib",
                "seaborn": "seaborn",
                "transformers": "transformers",
                "datasets": "datasets",
                "jax": "jax",
            }
            try:
                tree = ast.parse(code_payload)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            top = alias.name.split(".")[0]
                            if top in known_packages_single:
                                detected_single.add(known_packages_single[top])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        top = node.module.split(".")[0]
                        if top in known_packages_single:
                            detected_single.add(known_packages_single[top])
            except (SyntaxError, OSError, UnicodeDecodeError, UnicodeError):
                logger.debug(
                    "Failed to parse single-file experiment for dependency detection",
                    exc_info=True,
                )

            requirements = sorted(detected_single)
            (code_dir / "requirements.txt").write_text(
                "\n".join(requirements) + ("\n" if requirements else ""),
                encoding="utf-8",
            )
            paper_title = _extract_paper_title(final_paper)
            readme = (
                f"# Code Package for {paper_title}\n\n"
                "## Description\n"
                "This directory contains the final experiment script used for the paper.\n\n"
                "## How to Run\n"
                "`python experiment.py`\n\n"
                "## Dependencies\n"
                "Install dependencies with `pip install -r requirements.txt` if needed.\n"
            )
            (code_dir / "README.md").write_text(readme, encoding="utf-8")
            artifacts.append("code/")
            logger.info(
                "Stage 22: Packaged single-file code release with %d deps",
                len(requirements),
            )
    # WS-5.5: Generate framework diagram prompt for methodology section
    try:
        _framework_prompt = _generate_framework_diagram_prompt(
            final_paper, config, llm=llm
        )
        if _framework_prompt:
            _chart_dir = stage_dir / "charts"
            _chart_dir.mkdir(parents=True, exist_ok=True)
            (_chart_dir / "framework_diagram_prompt.md").write_text(
                _framework_prompt, encoding="utf-8"
            )
            logger.info("Stage 22: Generated framework diagram prompt → charts/framework_diagram_prompt.md")
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Stage 22: Framework diagram prompt generation skipped: %s", exc)

    return StageResult(
        stage=Stage.EXPORT_PUBLISH,
        status=StageStatus.DONE,
        artifacts=tuple(artifacts),
        evidence_refs=tuple(f"stage-22/{a}" for a in artifacts),
    )


# ---------------------------------------------------------------------------
# Citation helpers
# ---------------------------------------------------------------------------

def _check_citation_relevance(
    llm: Any,
    topic: str,
    results: list[Any],
) -> dict[str, float | None]:
    """Use LLM to assess relevance of each citation to the research topic.

    Returns a dict mapping cite_key → relevance score (0.0–1.0).
    Processes citations in batches of 30 to handle large bibliographies.
    """
    citation_lines = []
    for cr in results:
        citation_lines.append(f"- [{cr.cite_key}] \"{cr.title}\"")
    if not citation_lines:
        return {}

    all_scores: dict[str, float] = {}
    _BATCH_SIZE = 30

    for batch_start in range(0, len(citation_lines), _BATCH_SIZE):
        batch = citation_lines[batch_start:batch_start + _BATCH_SIZE]
        citations_text = "\n".join(batch)

        prompt = (
            f"Research topic: {topic}\n\n"
            f"Rate the relevance of each citation to the research topic "
            f"on a scale of 0.0 to 1.0.\n"
            f"Return ONLY a JSON object mapping cite_key to relevance score.\n"
            f"Example: {{\"smith2020\": 0.9, \"jones2019\": 0.2}}\n\n"
            f"Citations:\n{citations_text}"
        )

        try:
            resp = llm.chat(
                [{"role": "user", "content": prompt}],
                system="You assess citation relevance. Return only valid JSON.",
                json_mode=True,
            )
            parsed = _safe_json_loads(resp.content, {})
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, (int, float)):
                        all_scores[k] = max(0.0, min(1.0, float(v)))
        except (RuntimeError, TypeError, ValueError, UnicodeError, OSError):
            logger.debug(
                "Citation relevance check failed for batch %d–%d, skipping",
                batch_start, batch_start + len(batch),
            )

    return all_scores


def _remove_bibtex_entries(bib_text: str, keys_to_remove: set[str]) -> str:
    """Remove BibTeX entries whose keys are in *keys_to_remove*."""
    kept: list[str] = []
    for m in re.finditer(r"@\w+\{([^,]+),", bib_text):
        key = m.group(1).strip()
        if key in keys_to_remove:
            continue
        # Find the full entry (from @ to the next @ or end)
        start = m.start()
        # Find balanced braces
        depth = 0
        end = start
        for i in range(start, len(bib_text)):
            if bib_text[i] == "{":
                depth += 1
            elif bib_text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            kept.append(bib_text[start:end])
    return "\n\n".join(kept) + "\n" if kept else ""


def _remove_citations_from_text(text: str, keys_to_remove: set[str]) -> str:
    """Remove \\cite{key} and [key] references for specified citation keys."""

    # Handle multi-key LaTeX cites: \cite{a,b,c} → filter keys inside braces
    def _filter_cite(m: re.Match[str]) -> str:
        keys = [k.strip() for k in m.group(1).split(",")]
        kept = [k for k in keys if k not in keys_to_remove]
        if not kept:
            return ""
        return f"\\cite{{{','.join(kept)}}}"

    text = re.sub(r"\\cite\{([^}]+)\}", _filter_cite, text)

    # Markdown: [key]
    for key in keys_to_remove:
        text = re.sub(rf"\[{re.escape(key)}\]", "", text)
    return text


# ---------------------------------------------------------------------------
# Stage 23: Citation Verify
# ---------------------------------------------------------------------------

def _execute_citation_verify(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    from researchclaw.literature.verify import (
        VerifyStatus,
        annotate_paper_hallucinations,
        filter_verified_bibtex,
        verify_citations,
    )

    bib_text = _read_prior_artifact(run_dir, "references.bib") or ""
    paper_text = _read_prior_artifact(run_dir, "paper_final.md") or ""

    if not bib_text.strip():
        report_data = {
            "summary": {
                "total": 0,
                "verified": 0,
                "suspicious": 0,
                "hallucinated": 0,
                "skipped": 0,
                "integrity_score": 1.0,
            },
            "results": [],
            "note": "No references.bib found — nothing to verify.",
        }
        (stage_dir / "verification_report.json").write_text(
            json.dumps(report_data, indent=2), encoding="utf-8"
        )
        (stage_dir / "references_verified.bib").write_text(
            "% No references to verify\n", encoding="utf-8"
        )
        # Always write paper_final_verified.md so deliverables packaging gets
        # the latest paper (not a stale copy from a previous run)
        if paper_text.strip():
            (stage_dir / "paper_final_verified.md").write_text(
                paper_text, encoding="utf-8"
            )
        return StageResult(
            stage=Stage.CITATION_VERIFY,
            status=StageStatus.DONE,
            artifacts=("verification_report.json", "references_verified.bib"),
            evidence_refs=(
                "stage-23/verification_report.json",
                "stage-23/references_verified.bib",
            ),
        )

    s2_api_key = os.environ.get(getattr(config.llm, "s2_api_key_env", ""), "")

    from researchclaw.literature.verify import parse_bibtex_entries
    _n_entries = len(parse_bibtex_entries(bib_text))
    logger.info(
        "[citation-verify] Verifying %d references "
        "(DOI→CrossRef > OpenAlex > arXiv > S2)…",
        _n_entries,
    )
    report = verify_citations(bib_text, s2_api_key=s2_api_key)
    logger.info(
        "[citation-verify] Done: %d verified, %d suspicious, "
        "%d hallucinated, %d skipped (integrity: %.0f%%)",
        report.verified,
        report.suspicious,
        report.hallucinated,
        report.skipped,
        report.integrity_score * 100,
    )

    # --- Relevance check: assess topical relevance of verified citations ---
    if llm is not None and report.results:
        relevance_scores = _check_citation_relevance(
            llm, config.research.topic, report.results
        )
        for cr in report.results:
            score = relevance_scores.get(cr.cite_key)
            if score is not None:
                cr.relevance_score = score

    # FIX-5: Filter low-relevance citations and enforce hard cap
    RELEVANCE_THRESHOLD = 0.5
    MAX_CITATIONS = 60
    low_relevance_keys: set[str] = set()
    for cr in report.results:
        if cr.relevance_score is not None and cr.relevance_score < RELEVANCE_THRESHOLD:
            low_relevance_keys.add(cr.cite_key)

    # Hard cap: if still above MAX_CITATIONS after relevance filter, drop lowest
    # BUG-07 fix: Unscored citations (relevance_score=None) default to 0.7
    # because they passed API verification and are likely relevant.
    # Previously they defaulted to 0.0 which caused mass-deletion.
    _DEFAULT_RELEVANCE = 0.7
    remaining = [
        cr for cr in report.results
        if cr.cite_key not in low_relevance_keys
        and cr.status != VerifyStatus.HALLUCINATED
    ]
    if len(remaining) > MAX_CITATIONS:
        remaining.sort(
            key=lambda c: c.relevance_score if c.relevance_score is not None else _DEFAULT_RELEVANCE,
        )
        overflow = remaining[:len(remaining) - MAX_CITATIONS]
        for cr in overflow:
            low_relevance_keys.add(cr.cite_key)
        logger.info(
            "Stage 23: Hard cap applied, dropping %d additional low-relevance citations",
            len(overflow),
        )

    if low_relevance_keys:
        logger.info(
            "Stage 23: Filtering %d low-relevance citations (threshold=%.1f, cap=%d): %s",
            len(low_relevance_keys),
            RELEVANCE_THRESHOLD,
            MAX_CITATIONS,
            ", ".join(sorted(list(low_relevance_keys)[:20])),
        )

    (stage_dir / "verification_report.json").write_text(
        json.dumps(report.to_dict(), indent=2), encoding="utf-8"
    )

    verified_bib = filter_verified_bibtex(bib_text, report, include_suspicious=True)
    # Remove low-relevance entries from BibTeX
    if low_relevance_keys:
        verified_bib = _remove_bibtex_entries(verified_bib, low_relevance_keys)

    # BUG-26: If verification stripped >50% of entries (e.g. due to rate limiting),
    # fall back to the original bib to avoid breaking the paper's references
    original_count = len(re.findall(r"@\w+\{", bib_text))
    verified_count = len(re.findall(r"@\w+\{", verified_bib))
    if original_count > 0 and verified_count < original_count * 0.5:
        logger.warning(
            "Stage 23: Verification stripped %d→%d entries (>50%% loss). "
            "Keeping original bib to avoid breaking references.",
            original_count, verified_count,
        )
        verified_bib = bib_text

    # IMP-1: Also prune uncited entries from verified bib
    # BUG-182: Also scan LaTeX paper.tex (not just Markdown) for \cite{} keys.
    # The Markdown version may use [key] notation while LaTeX uses \cite{key}.
    if paper_text.strip():
        _vbib_keys = set(re.findall(r"@\w+\{([^,]+),", verified_bib))
        _cited_in_paper: set[str] = set()
        _cited_in_paper.update(
            re.findall(r"\[([a-zA-Z]+\d{4}[a-zA-Z0-9_-]*)\]", paper_text)
        )
        for _cm in re.finditer(r"\\cite\{([^}]+)\}", paper_text):
            _cited_in_paper.update(
                k.strip() for k in _cm.group(1).split(",")
            )
        # BUG-182: Also read stage-22/paper.tex for \cite{} keys
        _latex_paper = stage_dir.parent / "stage-22" / "paper.tex"
        if _latex_paper.exists():
            try:
                _latex_text = _latex_paper.read_text(encoding="utf-8")
                for _cm in re.finditer(r"\\cite[pt]?\{([^}]+)\}", _latex_text):
                    _cited_in_paper.update(
                        k.strip() for k in _cm.group(1).split(",")
                    )
            except OSError as exc:
                logger.warning(
                    "Could not read stage-22 paper.tex while collecting citations: %s",
                    exc,
                )
        _uncited_vbib = _vbib_keys - _cited_in_paper
        if _uncited_vbib:
            verified_bib = _remove_bibtex_entries(verified_bib, _uncited_vbib)
            logger.info(
                "Stage 23: Pruned %d uncited entries from verified bib "
                "(kept %d)",
                len(_uncited_vbib),
                len(_vbib_keys) - len(_uncited_vbib),
            )

    # BUG-100/R-2026-05-14: If all entries were filtered out
    # (low-relevance + uncited pruning), fail explicitly. A placeholder
    # references file can make downstream packaging treat an uncited or
    # fully-filtered bibliography as publication-ready.
    if not verified_bib.strip():
        failure_data = {
            "error": "all_bibtex_entries_filtered",
            "message": (
                "All BibTeX entries were removed during citation verification. "
                "Check citation keys in the paper body and regenerate the draft "
                "or references before publishing."
            ),
            "original_count": original_count,
            "verified_count": verified_count,
            "low_relevance_keys": sorted(low_relevance_keys),
        }
        (stage_dir / "citation_verify_failure.json").write_text(
            json.dumps(failure_data, indent=2), encoding="utf-8"
        )
        (stage_dir / "references_verified.bib").write_text("", encoding="utf-8")
        logger.warning(
            "Stage 23: All BibTeX entries filtered out; failing citation verification"
        )
        return StageResult(
            stage=Stage.CITATION_VERIFY,
            status=StageStatus.FAILED,
            artifacts=(
                "verification_report.json",
                "citation_verify_failure.json",
                "references_verified.bib",
            ),
            evidence_refs=(
                "stage-23/verification_report.json",
                "stage-23/citation_verify_failure.json",
                "stage-23/references_verified.bib",
            ),
        )

    (stage_dir / "references_verified.bib").write_text(verified_bib, encoding="utf-8")

    artifacts = ["verification_report.json", "references_verified.bib"]

    if paper_text.strip():
        annotated = annotate_paper_hallucinations(paper_text, report)
        # Remove \cite{} and [cite_key] references for low-relevance entries
        if low_relevance_keys:
            annotated = _remove_citations_from_text(annotated, low_relevance_keys)
        (stage_dir / "paper_final_verified.md").write_text(annotated, encoding="utf-8")
        artifacts.append("paper_final_verified.md")

    logger.info(
        "Stage 23 citation verify: %d total, %d verified, %d suspicious, "
        "%d hallucinated, %d skipped (integrity=%.1f%%)",
        report.total,
        report.verified,
        report.suspicious,
        report.hallucinated,
        report.skipped,
        report.integrity_score * 100,
    )

    return StageResult(
        stage=Stage.CITATION_VERIFY,
        status=StageStatus.DONE,
        artifacts=tuple(artifacts),
        evidence_refs=tuple(f"stage-23/{a}" for a in artifacts),
    )

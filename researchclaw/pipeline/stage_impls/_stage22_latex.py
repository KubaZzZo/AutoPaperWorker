"""LaTeX generation and verification helpers for Stage 22 export."""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    _extract_paper_title,
    _generate_neurips_checklist,
)

logger = logging.getLogger(__name__)


def generate_latex_artifacts(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    llm: LLMClient | None,
    final_paper_latex: str,
    author_year_map: dict[str, str],
    artifacts: list[str],
    *,
    read_prior_artifact: Callable[[Path, str], str | None],
    reconcile_figure_refs: Callable[[Path, Path], Any],
    review_compiled_pdf: Callable[[], Callable[[Path, LLMClient, str], dict[str, Any] | None]],
) -> list[str]:
    # Conference template: generate .tex file
    try:
        from researchclaw.templates import get_template, markdown_to_latex

        tpl = get_template(config.export.target_conference)
        # Use the latex-citation-processed version if available
        tex_source = final_paper_latex
        # Append NeurIPS-style checklist if target is a ML conference
        if tpl.name in ("neurips_2024", "neurips_2025", "icml_2025", "icml_2026",
                         "iclr_2025", "iclr_2026"):
            _has_exp = bool(read_prior_artifact(run_dir, "experiment_summary.json"))
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
            bib_entries=author_year_map or None,
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
                        _pdf_review = review_compiled_pdf()(
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

    return artifacts

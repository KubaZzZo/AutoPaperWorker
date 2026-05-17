"""Stage 22 export and publish implementation."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _chat_with_prompt,
    _extract_paper_title,
    _generate_framework_diagram_prompt,
    _generate_neurips_checklist,
    _get_evolution_overlay,
    _read_prior_artifact,
    _safe_json_loads,
    reconcile_figure_refs,
)
from researchclaw.pipeline.stage_impls._fabrication_sanitizer import _sanitize_fabricated_data
from researchclaw.pipeline.stage_impls._stage22_citations import postprocess_export_citations
from researchclaw.pipeline.stage_impls._stage22_code_package import package_export_code
from researchclaw.pipeline.stage_impls._stage22_latex import generate_latex_artifacts
from researchclaw.pipeline.stage_impls._stage23_citations import _remove_bibtex_entries
from researchclaw.pipeline.stage_impls.review_publish_citations import (
    load_seminal_papers_by_key,
    resolve_missing_citations,
    seminal_to_bibtex,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)

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

    artifacts, final_paper, final_paper_latex, bib_text, _ay_map = postprocess_export_citations(
        stage_dir,
        run_dir,
        final_paper,
        read_prior_artifact=_read_prior_artifact,
        resolve_missing_citations=_resolve_missing_citations,
    )

    artifacts = generate_latex_artifacts(
        stage_dir,
        run_dir,
        config,
        llm,
        final_paper_latex,
        _ay_map,
        artifacts,
        read_prior_artifact=_read_prior_artifact,
        reconcile_figure_refs=reconcile_figure_refs,
        review_compiled_pdf=_get_review_compiled_pdf,
    )

    # (Charts, BUG-99 path fix, and remove_missing_figures are now handled
    #  BEFORE compile_latex() - see generate_latex_artifacts().)

    artifacts = package_export_code(
        stage_dir,
        run_dir,
        final_paper,
        artifacts,
        read_prior_artifact=_read_prior_artifact,
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

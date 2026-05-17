"""Stage 17 paper draft builder."""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._domain import _detect_domain
from researchclaw.pipeline._helpers import (
    StageResult,
    _build_context_preamble,
    _read_best_analysis,
    _read_prior_artifact,
    _safe_json_loads,
    _utcnow_iso,
)
from researchclaw.pipeline.stage_impls._paper_draft_inputs import _build_initial_draft_inputs
from researchclaw.pipeline.stage_impls._paper_sections import _validate_draft_quality, _write_paper_sections
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)


def _detect_domain_compat(topic: str, domains: object = ()) -> tuple[str, str, str]:
    facade = sys.modules.get("researchclaw.pipeline.stage_impls._paper_writing")
    detect = getattr(facade, "_detect_domain", _detect_domain) if facade is not None else _detect_domain
    return detect(topic, domains)


class PaperDraftBuilder:
    """Build and persist the Stage 17 paper draft.

    The builder concentrates the paper-draft workflow behind a small stage
    interface while exposing named section helpers for future section-level
    refinement.
    """

    def __init__(
        self,
        stage_dir: Path,
        run_dir: Path,
        config: RCConfig,
        adapters: AdapterBundle,
        *,
        llm: LLMClient | None = None,
        prompts: PromptManager | None = None,
    ) -> None:
        self.stage_dir = stage_dir
        self.run_dir = run_dir
        self.config = config
        self.adapters = adapters
        self.llm = llm
        self.prompts = prompts

    @staticmethod
    def _extract_markdown_section(draft: str, *headings: str) -> str:
        heading_alt = "|".join(re.escape(h) for h in headings)
        pattern = re.compile(
            rf"^##\s+(?:{heading_alt})\b.*?(?=^##\s+|\Z)",
            re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(draft)
        return match.group(0).strip() if match else ""

    def build_abstract_section(self, draft: str) -> str:
        return self._extract_markdown_section(draft, "Abstract")

    def build_introduction_section(self, draft: str) -> str:
        return self._extract_markdown_section(draft, "Introduction")

    def build_method_section(self, draft: str) -> str:
        return self._extract_markdown_section(draft, "Method", "Methods", "Approach")

    def build_experiments_section(self, draft: str) -> str:
        return self._extract_markdown_section(draft, "Experiments", "Experimental Setup", "Results")

    def build_conclusion_section(self, draft: str) -> str:
        return self._extract_markdown_section(draft, "Conclusion", "Conclusions")

    def execute(self) -> StageResult:
        stage_dir = self.stage_dir
        run_dir = self.run_dir
        config = self.config
        adapters = self.adapters
        llm = self.llm
        prompts = self.prompts
        outline = _read_prior_artifact(run_dir, "outline.md") or ""
        preamble = _build_context_preamble(
            config,
            run_dir,
            include_goal=True,
            include_hypotheses=True,
            include_analysis=True,
            include_experiment_data=True,  # WS-5.1: inject real experiment data
        )

        draft_inputs = _build_initial_draft_inputs(run_dir, config)
        exp_summary_text = draft_inputs.exp_summary_text
        exp_metrics_instruction = draft_inputs.exp_metrics_instruction
        has_real_metrics = draft_inputs.has_real_metrics
        raw_metrics_block = draft_inputs.raw_metrics_block
        _has_parsed_metrics = draft_inputs.has_parsed_metrics
        _is_lit_first = draft_inputs.is_literature_first
        _verified_registry = draft_inputs.verified_registry
        all_simulated = True
        for stage_subdir in sorted(run_dir.glob("stage-*/runs")):
            for run_file in sorted(stage_subdir.glob("*.json")):
                if run_file.name == "results.json":
                    continue
                try:
                    _payload = json.loads(run_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(_payload, dict) and _payload.get("status") != "simulated":
                    all_simulated = False
                    break
            if not all_simulated:
                break

        if all_simulated and not _is_lit_first:
            logger.error(
                "BLOCKED: All experiment data is simulated (mode='simulated'). "
                "Cannot write a paper based on formulaic fake data. "
                "Switch to experiment.mode='sandbox' and re-run."
            )
            (stage_dir / "paper_draft.md").write_text(
                "# Paper Draft Blocked\n\n"
                "**Reason**: All experiment results are from simulated mode "
                "(formulaic data: `0.3 + idx * 0.03`). "
                "These are not real experimental results.\n\n"
                "**Action Required**: Set `experiment.mode: 'sandbox'` in "
                "config.arc.yaml and re-run the pipeline.",
                encoding="utf-8",
            )
            return StageResult(
                stage=Stage.PAPER_DRAFT,
                status=StageStatus.FAILED,
                artifacts=("paper_draft.md",),
                evidence_refs=(),
            )

        # R4-2: HARD BLOCK — refuse to write paper with no real data (ML/empirical domains)
        # For non-empirical domains (math proofs, theoretical economics), allow proceeding
        _domain_id, _domain_name, _domain_venues = _detect_domain_compat(
            config.research.topic, config.research.domains
        )
        _empirical_domains = {"ml", "engineering", "biology", "chemistry"}
        if not has_real_metrics and not _is_lit_first:
            if _domain_id in _empirical_domains:
                logger.error(
                    "BLOCKED: Cannot write paper — experiment produced NO metrics. "
                    "The pipeline will not fabricate results."
                )
                (stage_dir / "paper_draft.md").write_text(
                    "# Paper Draft Blocked\n\n"
                    "**Reason**: Experiment stage produced no metrics (status: failed/timeout). "
                    "Cannot write a paper without real experimental data.\n\n"
                    "**Action Required**: Fix experiment execution or increase time_budget_sec.",
                    encoding="utf-8",
                )
                return StageResult(
                    stage=Stage.PAPER_DRAFT,
                    status=StageStatus.FAILED,
                    artifacts=("paper_draft.md",),
                    evidence_refs=(),
                )
            else:
                logger.warning(
                    "No experiment metrics found, but domain '%s' may be non-empirical "
                    "(theoretical/mathematical). Proceeding with paper draft.",
                    _domain_name,
                )

        # R11-5: Experiment quality minimum threshold before paper writing
        # Parse analysis.md for quality rating and condition completeness
        analysis_text = _read_best_analysis(run_dir)
        _quality_warnings: list[str] = []

        # Check 1: Was the analysis quality rating very low?
        import re as _re_q
        _rating_match = _re_q.search(
            r"(?:quality\s+rating|result\s+quality)[:\s]*\**(\d+)\s*/\s*10",
            analysis_text,
            _re_q.IGNORECASE,
        )
        if _rating_match:
            _analysis_rating = int(_rating_match.group(1))
            if _analysis_rating <= 3:
                _quality_warnings.append(
                    f"Analysis rated experiment quality {_analysis_rating}/10"
                )
            # BUG-23: If quality rating is ≤ 2, force has_real_metrics = False
            # to prevent fabricated results even if stdout had stray numbers.
            # R5-BUG-05: Skip override when _has_parsed_metrics is True — the
            # analysis.md may be stale (from pre-refinement Stage 14) while
            # Stage 13 refinement produced real parsed metrics.
            if _analysis_rating <= 2 and has_real_metrics and not _has_parsed_metrics:
                logger.warning(
                    "BUG-23 guard: Analysis quality %d/10 \u2264 2 — "
                    "overriding has_real_metrics to False (experiment likely failed)",
                    _analysis_rating,
                )
                has_real_metrics = False

        # Check 2: Are baselines missing?
        _analysis_lower = analysis_text.lower()
        if "no" in _analysis_lower and "baseline" in _analysis_lower:
            if any(phrase in _analysis_lower for phrase in [
                "no baseline", "no bo", "no random", "baselines are missing",
                "missing baselines", "baseline coverage is missing",
            ]):
                _quality_warnings.append("Baselines appear to be missing from results")

        # Check 3: Is the metric undefined?
        if any(phrase in _analysis_lower for phrase in [
            "metric is undefined", "primary_metric is undefined",
            "undefined metric", "metric undefined",
        ]):
            _quality_warnings.append("Primary metric is undefined (direction/units/formula unknown)")

        # Check 4: Very few conditions completed
        _condition_count = len(_re_q.findall(
            r"condition[=:\s]+\w+.*?(?:mean|primary_metric)",
            raw_metrics_block or "",
            _re_q.IGNORECASE,
        ))

        if _quality_warnings:
            _warning_block = "\n".join(f"  - {w}" for w in _quality_warnings)
            logger.warning(
                "Stage 17: Experiment quality concerns detected before paper writing:\n%s",
                _warning_block,
            )
            # Inject quality warnings into the paper writing prompt so the LLM
            # writes an appropriately hedged paper
            exp_metrics_instruction += (
                "\n\n## EXPERIMENT QUALITY WARNINGS (address these honestly in the paper)\n"
                + "\n".join(f"- {w}" for w in _quality_warnings)
                + "\n\nBecause of these issues, the paper MUST:\n"
                "- Use hedged language ('preliminary', 'pilot', 'initial exploration')\n"
                "- NOT claim definitive comparisons between methods\n"
                "- Dedicate a substantial Limitations section to these gaps\n"
                "- Frame the contribution as methodology/framework, not empirical findings\n"
            )
            # Save warnings for tracking
            (stage_dir / "quality_warnings.json").write_text(
                json.dumps(_quality_warnings, indent=2), encoding="utf-8"
            )

        # Phase 1: Inject pre-built results tables from VerifiedRegistry
        if _verified_registry is not None:
            try:
                from researchclaw.templates.results_table_builder import (
                    build_condition_whitelist,
                    build_results_tables,
                )
                _prebuilt_tables = build_results_tables(
                    _verified_registry,
                    metric_direction=_verified_registry.metric_direction,
                )
                _condition_whitelist = build_condition_whitelist(_verified_registry)
                if _prebuilt_tables:
                    _tables_block = "\n\n".join(t.latex_code for t in _prebuilt_tables)
                    exp_metrics_instruction += (
                        "\n\n## PRE-BUILT RESULTS TABLES (MANDATORY — copy verbatim)\n"
                        "The tables below were AUTO-GENERATED from verified experiment data.\n"
                        "You MUST include these tables in the Results section EXACTLY as shown.\n"
                        "Do NOT modify any numbers. Do NOT add rows with fabricated data.\n"
                        "You MAY adjust formatting (bold, alignment) but NOT numerical values.\n\n"
                        + _tables_block
                    )
                    logger.info("Stage 17: Injected pre-built results tables into prompt")
                if _condition_whitelist:
                    exp_metrics_instruction += (
                        "\n\n## VERIFIED CONDITIONS (ONLY mention these in the paper)\n"
                        + _condition_whitelist
                        + "\nDo NOT discuss conditions not in this list. Do NOT invent new conditions.\n"
                    )
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError, KeyError) as _tb_exc:
                logger.warning("Stage 17: Failed to build pre-built tables: %s", _tb_exc, exc_info=True)

        # R4-2: Anti-fabrication data integrity instruction
        exp_metrics_instruction += (
            "\n\n## CRITICAL: Data Integrity Rules\n"
            "- You may ONLY report numbers that appear in the experiment data above\n"
            "- If the experiment data is incomplete (fewer conditions than planned), report\n"
            "  ONLY the conditions that were actually run\n"
            "- Do NOT extrapolate, interpolate, or 'fill in' missing cells in tables\n"
            "- Do NOT invent confidence intervals, p-values, or statistical tests unless\n"
            "  the actual data supports them\n"
            "- If only N conditions completed, simply report results for those N conditions\n"
            "  without repeating apologies or disclaimers about missing conditions\n"
            "- Any table cell without real data must show '\u2014' (not a plausible number)\n"
            "- FORBIDDEN: generating numbers that 'look right' based on your training data\n"
        )

        # IMP-6 + FA: Inject chart references into paper draft prompt
        # Prefer FigureAgent's figure_plan.json (rich descriptions) over raw file scan
        # BUG-FIX: figure_plan.json may be a list (from FigureAgent planner) or a dict
        # (from executor overwrite).  The orchestrator writes a list at planning time;
        # the executor overwrites with a dict only when figure_count > 0.  If the
        # FigureAgent renders 0 charts the list persists, and calling .get() on it
        # raises AttributeError.
        _fa_descriptions = ""
        # BUG-178: Iterate in reverse order so we read the LATEST stage-14
        # iteration's figure plan, matching Stage 22 which copies charts
        # from the newest iteration.
        for _s14_dir in sorted(run_dir.glob("stage-14*"), reverse=True):
            # Prefer the final plan (dict with figure_descriptions) if it exists
            for _fp_name in ("figure_plan_final.json", "figure_plan.json"):
                _fp_path = _s14_dir / _fp_name
                if not _fp_path.exists():
                    continue
                try:
                    _fp_data = json.loads(_fp_path.read_text(encoding="utf-8"))
                    if isinstance(_fp_data, dict):
                        _fa_descriptions = _fp_data.get("figure_descriptions", "")
                    elif isinstance(_fp_data, list) and _fp_data:
                        # List format from FigureAgent planner — synthesize descriptions
                        _desc_parts = ["## PLANNED FIGURES (from figure plan)\n"]
                        for _fig in _fp_data:
                            if isinstance(_fig, dict):
                                _fid = _fig.get("figure_id", "unnamed")
                                _ftitle = _fig.get("title", "")
                                _fcap = _fig.get("caption", "")
                                _fsec = _fig.get("section", "results")
                                _desc_parts.append(
                                    f"- **{_fid}** ({_fsec}): {_ftitle}\n  {_fcap}"
                                )
                        if len(_desc_parts) > 1:
                            _fa_descriptions = "\n".join(_desc_parts)
                except (json.JSONDecodeError, OSError):
                    logger.debug("Stage 17: Failed to parse figure plan %s", _fp_path, exc_info=True)
                if _fa_descriptions:
                    break
            if _fa_descriptions:
                break

        if _fa_descriptions:
            exp_metrics_instruction += "\n\n" + _fa_descriptions
            logger.info("Stage 17: Injected FigureAgent figure descriptions into paper draft prompt")
        else:
            # Fallback: scan for chart files from the LATEST stage-14 iteration
            # BUG-178: Must use reverse order to match Stage 22 chart copy behavior
            _chart_files: list[str] = []
            for _s14_dir in sorted(run_dir.glob("stage-14*"), reverse=True):
                _charts_path = _s14_dir / "charts"
                if _charts_path.is_dir():
                    _found = sorted(_charts_path.glob("*.png"))
                    if _found:
                        _chart_files = [f.name for f in _found]
                        break  # Use only the latest iteration's charts
            if _chart_files:
                _chart_block = (
                    "\n\n## AVAILABLE FIGURES (embed in the paper)\n"
                    "The following figures were generated from actual experiment data. "
                    "You MUST reference at least 1-2 of these in the Results section "
                    "using markdown image syntax: `![Caption](charts/filename.png)`\n\n"
                )
                for _cf_name in _chart_files:
                    _label = _cf_name.replace("_", " ").replace(".png", "").title()
                    _chart_block += f"- `charts/{_cf_name}` \u2014 {_label}\n"
                _chart_block += (
                    "\nFor each figure referenced, write a descriptive caption and "
                    "discuss what the figure shows in 2-3 sentences.\n"
                )
                exp_metrics_instruction += _chart_block
                logger.info(
                    "Stage 17: Injected %d chart references into paper draft prompt",
                    len(_chart_files),
                )

        # WS-5.5: Framework diagram placeholder instruction
        exp_metrics_instruction += (
            "\n\n## FRAMEWORK DIAGRAM PLACEHOLDER\n"
            "In the Method/Approach section, include a placeholder for the methodology "
            "framework overview figure. Insert this exactly:\n\n"
            "```\n"
            "![Framework Overview](charts/framework_diagram.png)\n"
            "**Figure N.** Overview of the proposed methodology. "
            "[A detailed framework diagram will be generated separately and inserted here.]\n"
            "```\n\n"
            "This figure should be referenced in the text as 'Figure N' and discussed briefly "
            "(1-2 sentences describing the overall pipeline/architecture flow). "
            "The actual image will be generated post-hoc using a text-to-image model.\n"
        )

        # P5: Extract hyperparameters from results.json for paper Method section
        _hp_table = ""
        for _s14_dir in sorted(run_dir.glob("stage-14*")):
            for _run_file in sorted(_s14_dir.glob("runs/*.json")):
                try:
                    _run_data = json.loads(_run_file.read_text(encoding="utf-8"))
                    if isinstance(_run_data, dict) and _run_data.get("hyperparameters"):
                        _hp = _run_data["hyperparameters"]
                        if isinstance(_hp, dict) and _hp:
                            _hp_table = "\n\n## HYPERPARAMETERS (include as a table in the Method section)\n"
                            _hp_table += "| Hyperparameter | Value |\n|---|---|\n"
                            for _hk, _hv in sorted(_hp.items()):
                                _hp_table += f"| {_hk} | {_hv} |\n"
                            _hp_table += (
                                "\nThis table MUST appear in the Method/Experiments section. "
                                "Include ALL hyperparameters used, with justification for key choices.\n"
                            )
                            break
                except (json.JSONDecodeError, OSError):
                    continue
            if _hp_table:
                break
        # Also check staging dirs for results.json
        if not _hp_table:
            for _staging_dir in sorted(run_dir.glob("stage-*/runs/_docker_*")):
                _rjson = _staging_dir / "results.json"
                if _rjson.is_file():
                    try:
                        _rdata = json.loads(_rjson.read_text(encoding="utf-8"))
                        if isinstance(_rdata, dict) and _rdata.get("hyperparameters"):
                            _hp = _rdata["hyperparameters"]
                            if isinstance(_hp, dict) and _hp:
                                _hp_table = "\n\n## HYPERPARAMETERS (include as a table in the Method section)\n"
                                _hp_table += "| Hyperparameter | Value |\n|---|---|\n"
                                for _hk, _hv in sorted(_hp.items()):
                                    _hp_table += f"| {_hk} | {_hv} |\n"
                                _hp_table += (
                                    "\nThis table MUST appear in the Method/Experiments section. "
                                    "Include ALL hyperparameters used, with justification for key choices.\n"
                                )
                                break
                    except (json.JSONDecodeError, OSError):
                        continue
        if _hp_table:
            exp_metrics_instruction += _hp_table

        # F2.6: Build citation list from references.bib / candidates with cite_keys
        citation_instruction = ""
        bib_text = _read_prior_artifact(run_dir, "references.bib")

        # P3: Pre-verify citations before paper draft — remove hallucinated refs
        if bib_text and bib_text.strip():
            from researchclaw.literature.verify import (
                filter_verified_bibtex,
            )
            from researchclaw.literature.verify import (
                verify_citations as _verify_cit,
            )
            try:
                _pre_report = _verify_cit(bib_text, inter_verify_delay=0.5)
                _kept = _pre_report.verified + _pre_report.suspicious
                _removed = _pre_report.hallucinated
                if _removed > 0:
                    bib_text = filter_verified_bibtex(
                        bib_text, _pre_report, include_suspicious=True
                    )
                    (stage_dir / "references_preverified.bib").write_text(
                        bib_text, encoding="utf-8"
                    )
                    logger.info(
                        "P3: Pre-verification kept %d/%d citations (removed %d hallucinated)",
                        _kept, _pre_report.total, _removed,
                    )
            except (RuntimeError, OSError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("P3: Pre-verification failed, using original bib: %s", exc, exc_info=True)

        candidates_text = _read_prior_artifact(run_dir, "candidates.jsonl")
        if candidates_text:
            cite_lines: list[str] = []
            for row_text in candidates_text.strip().splitlines():
                row = _safe_json_loads(row_text, {})
                if isinstance(row, dict) and row.get("cite_key"):
                    authors_info = ""
                    if isinstance(row.get("authors"), list) and row["authors"]:
                        first_author = row["authors"][0]
                        if isinstance(first_author, dict):
                            # BUG-38: name may be non-str (tuple/list) — force str
                            _name = first_author.get("name", "")
                            authors_info = _name if isinstance(_name, str) else str(_name)
                        elif isinstance(first_author, str):
                            authors_info = first_author
                        if len(row["authors"]) > 1:
                            authors_info += " et al."
                    title = row.get("title", "")
                    cite_lines.append(
                        f"- [{row['cite_key']}] \u2192 TITLE: \"{title}\" "
                        f"| {authors_info} "
                        f"({row.get('venue', '')}, {row.get('year', '')}, "
                        f"cited {row.get('citation_count', 0)} times) "
                        f"| ONLY cite this key when discussing: {title}"
                    )
            if cite_lines:
                citation_instruction = (
                    "\n\nAVAILABLE REFERENCES (use [cite_key] to cite in the text):\n"
                    + "\n".join(cite_lines)
                    + "\n\nCRITICAL CITATION RULES:\n"
                    "- In the body text, cite using [cite_key] format, e.g. [smith2024transformer].\n"
                    "- Do NOT write a References section \u2014 it will be auto-generated from the bibliography file.\n"
                    "- Do NOT invent any references or arXiv IDs not in the above list.\n"
                    "- You may cite a subset, but NEVER fabricate citations or change arXiv IDs.\n"
                    "- SEMANTIC MATCHING: Before citing a reference, verify that its TITLE matches\n"
                    "  the concept you are discussing. Do NOT use an unrelated cite_key just\n"
                    "  because it sounds similar.\n"
                    "- If no reference in the list matches the concept you want to cite,\n"
                    "  write 'prior work has shown...' WITHOUT a citation, rather than using\n"
                    "  a mismatched reference.\n"
                    "- Each [cite_key] MUST correspond to the paper whose title is shown\n"
                    "  next to that key in the list above. Cross-check before citing.\n"
                    "\nCITATION QUANTITY & QUALITY CONSTRAINTS:\n"
                    "- Cite 25-40 unique references in the paper body. The Related Work\n"
                    "  section alone should cite at least 15 references.\n"
                    "- Every citation MUST be directly relevant to the paper's topic.\n"
                    "- DO NOT cite papers from unrelated domains (wireless communication, "
                    "manufacturing, UAV, etc.).\n"
                    "- Prefer well-known, highly-cited papers over obscure ones.\n"
                    "- If unsure whether a paper exists or is relevant, DO NOT cite it.\n"
                )

        # Literature-first mode instruction for survey/review topics
        if _is_lit_first:
            exp_metrics_instruction += (
                "\n\n## LITERATURE-FIRST MODE\n"
                "This paper is a **survey / review / literature-first study**.\n"
                "- The contribution is the synthesis, taxonomy, and critical analysis of existing work.\n"
                "- Do NOT claim novel experimental results. Instead, summarize and compare findings\n"
                "  from the collected literature.\n"
                "- Structure the paper around themes, taxonomies, or chronological developments.\n"
                "- Include a comprehensive Related Work / Literature Review as the main body.\n"
                "- Tables should compare methods, datasets, and reported metrics FROM the literature.\n"
                "- The Conclusion should identify open problems and future directions.\n"
            )
            logger.info("Stage 17: Literature-first mode enabled for survey/review topic")

        if llm is not None:
            _pm = prompts or PromptManager()
            topic_constraint = _pm.block("topic_constraint", topic=config.research.topic)

            # --- Section-by-section writing (3 calls) for conference-grade depth ---
            draft = _write_paper_sections(
                llm=llm,
                pm=_pm,
                run_dir=run_dir,
                preamble=preamble,
                topic_constraint=topic_constraint,
                exp_metrics_instruction=exp_metrics_instruction,
                citation_instruction=citation_instruction,
                outline=outline,
                model_name=config.llm.primary_model,
                paper_language=config.export.paper_language,
            )

            # R7: Strip LLM-generated References section — it often fabricates arXiv IDs.
            import re as _re_r7
            ref_pattern = _re_r7.compile(
                r'^(#{1,2}\s*References.*)', _re_r7.MULTILINE | _re_r7.DOTALL
            )
            ref_match = ref_pattern.search(draft)
            if ref_match:
                draft = draft[:ref_match.start()].rstrip()
                logger.info("Stage 17: Stripped LLM-generated References section (R7 fix)")
        else:
            # Build template with real data if available
            results_section = "Template results summary."
            if exp_summary_text:
                exp_summary = _safe_json_loads(exp_summary_text, {})
                if isinstance(exp_summary, dict) and exp_summary.get("metrics_summary"):
                    lines = ["Experiment results:"]
                    for mk, mv in exp_summary["metrics_summary"].items():
                        if isinstance(mv, dict):
                            lines.append(
                                f"- {mk}: mean={mv.get('mean')}, min={mv.get('min')}, "
                                f"max={mv.get('max')}, n={mv.get('count')}"
                            )
                    results_section = "\n".join(lines)

            draft = f"""# Draft Title

        ## Abstract
        Template draft abstract.

        ## Introduction
        Template introduction for {config.research.topic}.

        ## Related Work
        Template related work.

        ## Method
        Template method description.

        ## Experiments
        Template experimental setup.

        ## Results
        {results_section}

        ## Limitations
        Template limitations.

        ## Conclusion
        Template conclusion.

        ## References
        Template references.

        Generated: {_utcnow_iso()}
        """
        (stage_dir / "paper_draft.md").write_text(draft, encoding="utf-8")

        # Validate draft quality (section balance + bullet density)
        _validate_draft_quality(draft, stage_dir=stage_dir)

        # --- HITL: Read human guidance for paper draft ---
        guidance_file = stage_dir / "hitl_guidance.md"
        if guidance_file.exists():
            try:
                guidance = guidance_file.read_text(encoding="utf-8").strip()
                if guidance and llm is not None:
                    draft_path = stage_dir / "paper_draft.md"
                    if draft_path.exists():
                        current_draft = draft_path.read_text(encoding="utf-8")
                        logger.info("Applying HITL guidance to paper draft")
                        resp = llm.chat(
                            [{"role": "user", "content": (
                                f"The human researcher provided this guidance for the paper:\n\n"
                                f"{guidance}\n\n"
                                f"Apply these suggestions to improve the following draft. "
                                f"Preserve all existing content and citations. "
                                f"Only make changes that align with the guidance.\n\n"
                                f"## Current Draft\n{current_draft[:8000]}"
                            )}],
                            max_tokens=8192,
                        )
                        draft_path.write_text(resp.content, encoding="utf-8")
            except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
                logger.debug("HITL guidance application to draft failed (non-blocking)", exc_info=True)

        # --- HITL: Paper Co-Writer data persistence ---
        try:
            from researchclaw.hitl.workshops.paper import PaperCoWriter

            writer = PaperCoWriter(run_dir, llm_client=llm)
            writer.load_outline()
            draft_path = stage_dir / "paper_draft.md"
            if draft_path.exists():
                draft_text = draft_path.read_text(encoding="utf-8")
                for section in writer.sections:
                    # Extract section content from draft
                    import re as _re_pw
                    pattern = rf"(?:^|\n)##?\s*{_re_pw.escape(section.name)}.*?\n(.*?)(?=\n##?\s|\Z)"
                    match = _re_pw.search(draft_text, _re_pw.DOTALL)
                    if match:
                        section.content = match.group(1).strip()
                        section.status = "ai_draft"
            writer.save()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.debug("Stage 17: Paper Co-Writer persistence failed", exc_info=True)

        return StageResult(
            stage=Stage.PAPER_DRAFT,
            status=StageStatus.DONE,
            artifacts=("paper_draft.md",),
            evidence_refs=("stage-17/paper_draft.md",),
        )


def _execute_paper_draft(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    return PaperDraftBuilder(
        stage_dir,
        run_dir,
        config,
        adapters,
        llm=llm,
        prompts=prompts,
    ).execute()

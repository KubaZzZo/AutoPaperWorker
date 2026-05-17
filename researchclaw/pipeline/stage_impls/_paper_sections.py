"""Section generation and quality review helpers for paper writing."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import _chat_with_prompt, _get_evolution_overlay, _safe_json_loads
from researchclaw.pipeline.stage_impls.paper_draft_quality import validate_draft_quality
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)


def _write_paper_sections(
    *,
    llm: LLMClient,
    pm: PromptManager,
    run_dir: Path | None = None,
    preamble: str,
    topic_constraint: str,
    exp_metrics_instruction: str,
    citation_instruction: str,
    outline: str,
    model_name: str = "",
    paper_language: str = "English",
) -> str:
    """Write a conference-grade paper in 3 sequential LLM calls.

    Call 1: Title + Abstract + Introduction + Related Work
    Call 2: Method + Experiments (with full experiment data)
    Call 3: Results + Discussion + Limitations + Conclusion

    Each call receives prior sections for coherence.
    """
    # Render writing_structure block for injection
    try:
        _writing_structure = pm.block("writing_structure")
    except KeyError:
        _writing_structure = ""

    _overlay = _get_evolution_overlay(run_dir, "paper_draft")
    system = pm.for_stage(
        "paper_draft",
        evolution_overlay=_overlay,
        preamble=preamble,
        topic_constraint=topic_constraint,
        exp_metrics_instruction=exp_metrics_instruction,
        citation_instruction=citation_instruction,
        writing_structure=_writing_structure,
        outline=outline,
    ).system

    sections: list[str] = []

    # --- R4-3: Title guidelines and abstract structure ---
    try:
        title_guidelines = pm.block("title_guidelines")
    except KeyError:
        title_guidelines = ""
    try:
        abstract_structure = pm.block("abstract_structure")
    except KeyError:
        abstract_structure = ""

    # IMP-20/25/31/24: Academic style, narrative, anti-hedging, anti-repetition
    try:
        academic_style_guide = pm.block("academic_style_guide")
    except KeyError:
        academic_style_guide = ""
    try:
        narrative_writing_rules = pm.block("narrative_writing_rules")
    except KeyError:
        narrative_writing_rules = ""
    try:
        anti_hedging_rules = pm.block("anti_hedging_rules")
    except KeyError:
        anti_hedging_rules = ""
    try:
        anti_repetition_rules = pm.block("anti_repetition_rules")
    except KeyError:
        anti_repetition_rules = ""

    language_instruction = ""
    language = paper_language.strip()
    if language and language.lower() != "english":
        language_instruction = (
            f"\nLANGUAGE REQUIREMENT: Write the paper in {language}. "
            "Keep citation keys, code identifiers, dataset names, equations, and "
            "file paths unchanged. Use natural academic terminology in the target language.\n\n"
        )

    # --- Call 1: Title + Abstract + Introduction + Related Work ---
    call1_user = (
        f"{preamble}\n\n"
        f"{topic_constraint}"
        f"{citation_instruction}\n\n"
        f"{title_guidelines}\n\n"
        f"{academic_style_guide}\n"
        f"{narrative_writing_rules}\n"
        f"{anti_hedging_rules}\n"
        f"{anti_repetition_rules}\n\n"
        f"{language_instruction}"
        "Write the following sections of a NeurIPS/ICML-quality paper in markdown. "
        "Follow the LENGTH REQUIREMENTS strictly:\n\n"
        "1. **Title** (HARD RULE: MUST be 14 words or fewer. Create a catchy method name "
        "first, then build the title: 'MethodName: Subtitle'. If your title exceeds 14 words, "
        "it will be automatically rejected. NEVER use 'Untitled Paper'.)\n"
        f"2. **Abstract** (150-220 words — HARD LIMIT. Do NOT exceed 220 words. "
        f"Do NOT include raw metric paths or 16-digit decimals.){abstract_structure}\n"
        "3. **Introduction** (800-1000 words): real-world motivation, problem statement, "
        "research gap analysis with citations, method overview, 3-4 contributions as bullet points, "
        "paper organization paragraph. MUST cite 8-12 references.\n"
        "4. **Related Work** (600-800 words): organized into 3-4 thematic subsections, each discussing "
        "4-5 papers with proper citations. Compare approaches, identify limitations, position this work.\n\n"
        f"Outline:\n{outline}\n\n"
        "Output markdown with ## headers. Do NOT include a References section.\n"
        "IMPORTANT: Start DIRECTLY with '## Title'. Do NOT include any preamble, "
        "data verification, condition listing, or metric enumeration before the title. "
        "The paper should read like a published manuscript, not a data report."
    )
    # R14-1: Higher token limit for reasoning models
    _paper_max_tokens = 12000
    if any(model_name.startswith(p) for p in ("gpt-5", "o3", "o4")):
        _paper_max_tokens = 24000

    # T3.5/R-2026-05-14: Retry once on failure, then fail fast. Writing
    # placeholder manuscript text would make downstream review treat an
    # incomplete paper as a real artifact.
    try:
        resp1 = _chat_with_prompt(llm, system, call1_user, max_tokens=_paper_max_tokens, retries=1)
        part1 = resp1.content.strip()
    except (RuntimeError, OSError, TypeError, ValueError) as exc:
        logger.error("Stage 17: Part 1 LLM call failed after retry", exc_info=True)
        raise RuntimeError("paper section 1 generation failed after retry") from exc
    sections.append(part1)
    logger.info("Stage 17: Part 1 (Title+Abstract+Intro+Related Work) — %d chars", len(part1))

    # --- Call 2: Method + Experiments ---
    call2_user = (
        f"{preamble}\n\n"
        f"{topic_constraint}"
        f"{exp_metrics_instruction}\n\n"
        f"{narrative_writing_rules}\n"
        f"{anti_hedging_rules}\n\n"
        f"{language_instruction}"
        # IMP-21: Citation instruction for Method + Experiments
        "CITATION REQUIREMENT: The Method section MUST cite at least 3-5 related "
        "technical papers (foundations your method builds on). The Experiments section "
        "MUST cite baseline method papers. Use [cite_key] syntax.\n"
        f"{citation_instruction}\n\n"
        "You are continuing a paper. The sections written so far are:\n\n"
        f"---\n{part1}\n---\n\n"
        "Now write the next sections, maintaining consistency with the above:\n\n"
        "5. **Method** (1000-1500 words): formal problem definition with mathematical notation "
        "($x$, $\\theta$, etc.), detailed algorithm description with equations, step-by-step procedure, "
        "complexity analysis, design rationale for key choices. Include algorithm pseudocode if applicable. "
        "Write as FLOWING PROSE — do NOT use bullet-point lists for method components.\n"
        "6. **Experiments** (800-1200 words): detailed experimental setup, datasets with statistics "
        "(size, splits, features), all baselines and their implementations, hyperparameter settings "
        "in a markdown table, evaluation metrics with mathematical definitions, hardware and runtime info.\n"
        "METHOD NAMES IN TABLES: Use SHORT abbreviations (4-8 chars) for method names "
        "in tables. Define abbreviation mappings in a footnote. "
        "NEVER put method names longer than 20 characters in table cells.\n\n"
        f"Outline:\n{outline}\n\n"
        "Output markdown with ## headers. Continue from where Part 1 ended."
    )
    try:
        resp2 = _chat_with_prompt(llm, system, call2_user, max_tokens=_paper_max_tokens, retries=1)
        part2 = resp2.content.strip()
    except (RuntimeError, OSError, TypeError, ValueError) as exc:
        logger.error("Stage 17: Part 2 LLM call failed after retry", exc_info=True)
        raise RuntimeError("paper section 2 generation failed after retry") from exc
    sections.append(part2)
    logger.info("Stage 17: Part 2 (Method+Experiments) — %d chars", len(part2))

    # --- Call 3: Results + Discussion + Limitations + Conclusion ---
    call3_user = (
        f"{preamble}\n\n"
        f"{topic_constraint}"
        f"{exp_metrics_instruction}\n\n"
        f"{narrative_writing_rules}\n"
        f"{anti_hedging_rules}\n"
        f"{anti_repetition_rules}\n\n"
        f"{language_instruction}"
        # IMP-21: Citation instruction for Results + Discussion + Conclusion
        "CITATION REQUIREMENT: The Discussion section MUST cite at least 3-5 papers "
        "when comparing findings with prior work. The Conclusion may cite 1-2 "
        "foundational references.\n"
        f"{citation_instruction}\n\n"
        "You are completing a paper. The sections written so far are:\n\n"
        f"---\n{part1}\n\n{part2}\n---\n\n"
        "Now write the final sections, maintaining consistency:\n\n"
        "7. **Results** (600-800 words):\n"
        "   - START with an AGGREGATED results table (Table 1): rows = methods, columns = metrics.\n"
        "     Each cell = mean \u00b1 std across seeds. Bold the best value per column.\n"
        "     EVERY table MUST have a descriptive caption that allows understanding without "
        "     reading the main text. NEVER use just 'Table 1' as a caption.\n"
        "   - Follow with a PER-REGIME table (Table 2) breaking down by easy/hard regimes.\n"
        "   - Include a STATISTICAL COMPARISON table (Table 3): paired t-tests between key methods.\n"
        "   - NEVER dump raw per-seed numbers in the main text. Aggregate first, then discuss.\n"
        "   - MUST include at least 2 figures using markdown image syntax: ![Caption](charts/filename.png)\n"
        "     One figure MUST be a performance comparison chart. Figures MUST be referenced "
        "     in text: 'As shown in Figure 1, ...'\n"
        "8. **Discussion** (400-600 words): interpretation of key findings, unexpected results, "
        "comparison with prior work (CITE 3-5 papers here!), practical implications.\n"
        "9. **Limitations** (200-300 words): honest assessment of scope, dataset, methodology. "
        "ALL caveats consolidated HERE — nowhere else in the paper.\n"
        "10. **Conclusion** (100-200 words MAXIMUM — this is a HARD LIMIT): "
        "Summarize contributions in 2-3 sentences. State main finding in 1 sentence. "
        "Suggest 2-3 concrete future directions in 1-2 sentences. "
        "Do NOT repeat any specific numbers from Results. Do NOT restate the abstract. "
        "A good conclusion is SHORT and forward-looking.\n\n"
        "CRITICAL FORMATTING RULES FOR ALL SECTIONS:\n"
        "- Write as FLOWING PROSE paragraphs, NOT bullet-point lists\n"
        "- NEVER dump raw metric paths like 'config/method_name/seed_3/primary_metric'\n"
        "- All numbers must be rounded to 4 decimal places maximum\n"
        "- Every table MUST have a descriptive caption (not just 'Table 1')\n"
        "- Use \\begin{algorithm} or pseudocode notation, NOT \\begin{verbatim}\n\n"
        "Output markdown with ## headers. Do NOT include a References section."
    )
    try:
        resp3 = _chat_with_prompt(llm, system, call3_user, max_tokens=_paper_max_tokens, retries=1)
        part3 = resp3.content.strip()
    except (RuntimeError, OSError, TypeError, ValueError) as exc:
        logger.error("Stage 17: Part 3 LLM call failed after retry", exc_info=True)
        raise RuntimeError("paper section 3 generation failed after retry") from exc
    sections.append(part3)
    logger.info("Stage 17: Part 3 (Results+Discussion+Limitations+Conclusion) — %d chars", len(part3))

    # Combine all sections
    draft = "\n\n".join(sections)

    # R32: Strip data verification preamble that LLMs sometimes emit before
    # the actual paper.  The preamble typically starts with "## Tested Conditions"
    # or similar headings and ends before "## Title".
    _title_match = re.search(r"^## Title\b", draft, re.MULTILINE)
    if _title_match and _title_match.start() > 200:
        _stripped = draft[_title_match.start():]
        logger.info(
            "R32: Stripped %d-char preamble before '## Title'",
            _title_match.start(),
        )
        draft = _stripped

    total_words = len(draft.split())
    logger.info("Stage 17: Full draft — %d chars, ~%d words", len(draft), total_words)

    return draft


def _validate_draft_quality(
    draft: str,
    stage_dir: Path | None = None,
) -> dict[str, Any]:
    return validate_draft_quality(
        draft,
        stage_dir=stage_dir,
        diagnostic_logger=logger,
    )


def _review_compiled_pdf(
    pdf_path: Path,
    llm: LLMClient,
    topic: str,
) -> dict[str, Any]:
    """Multi-dimensional LLM review of compiled paper (AI-Scientist style).

    Scores the paper on 7 academic review dimensions (1-10 each),
    identifies specific strengths/weaknesses, and provides an overall
    accept/reject recommendation with confidence.

    Returns a dict with dimensional scores, issues, and decision.
    """
    if not pdf_path.exists():
        return {}

    # Use source-based review since not all models support vision
    tex_path = pdf_path.with_suffix(".tex")
    if not tex_path.exists():
        return {}

    tex_content = tex_path.read_text(encoding="utf-8")[:12000]

    review_prompt = (
        "You are a senior Area Chair at a top AI conference (NeurIPS/ICML/ICLR) "
        "reviewing a paper submission. Provide a rigorous, structured review.\n\n"
        f"PAPER TOPIC: {topic}\n\n"
        f"LaTeX source:\n```latex\n{tex_content}\n```\n\n"
        "REVIEW INSTRUCTIONS:\n"
        "Score each dimension 1-10 (1=unacceptable, 5=borderline, 8=strong accept, "
        "10=best paper candidate). Be critical but fair.\n\n"
        "DIMENSIONS:\n"
        "1. SOUNDNESS: Are claims well-supported? Is methodology correct? "
        "Are there logical gaps or unsupported claims?\n"
        "2. PRESENTATION: Is the writing clear, flowing, and professional? "
        "Are there grammar errors, bullet lists in prose sections, or "
        "boilerplate phrases? Is it free of AI-generated slop?\n"
        "3. CONTRIBUTION: Is the contribution significant? Does it advance "
        "the field beyond incremental improvement?\n"
        "4. ORIGINALITY: Is the approach novel? Does it differentiate clearly "
        "from prior work?\n"
        "5. CLARITY: Are the method and results easy to understand? Are figures "
        "and tables well-designed with descriptive captions?\n"
        "6. SIGNIFICANCE: Would the community benefit from this work? Does it "
        "open new research directions?\n"
        "7. REPRODUCIBILITY: Are experimental details sufficient to reproduce "
        "results? Are hyperparameters, datasets, and metrics clearly stated?\n\n"
        "Also evaluate:\n"
        "- Are all figures referenced in the text?\n"
        "- Are tables properly formatted (booktabs style, no vertical rules)?\n"
        "- Does the related work critically compare, not just list papers?\n"
        "- Are statistical measures (std, CI, multiple seeds) reported?\n"
        "- Is there a clear limitations section?\n\n"
        "Return a JSON object:\n"
        "{\n"
        '  "soundness": N,\n'
        '  "presentation": N,\n'
        '  "contribution": N,\n'
        '  "originality": N,\n'
        '  "clarity": N,\n'
        '  "significance": N,\n'
        '  "reproducibility": N,\n'
        '  "overall_score": N,\n'
        '  "confidence": N,\n'
        '  "decision": "accept" or "reject",\n'
        '  "strengths": ["strength1", "strength2", ...],\n'
        '  "weaknesses": ["weakness1", "weakness2", ...],\n'
        '  "critical_issues": ["issue requiring revision", ...],\n'
        '  "minor_issues": ["formatting/typo issues", ...],\n'
        '  "summary": "2-3 sentence overall assessment"\n'
        "}\n"
    )

    try:
        resp = llm.chat(
            messages=[{"role": "user", "content": review_prompt}],
            system=(
                "You are a meticulous, critical academic reviewer. "
                "You have reviewed 100+ papers at top venues. "
                "Score honestly — most papers deserve 4-6, not 7-9. "
                "Flag any sign of AI-generated boilerplate."
            ),
        )
        review_data = _safe_json_loads(resp.content, {})
        if isinstance(review_data, dict) and "overall_score" in review_data:
            # Compute weighted aggregate if individual scores present
            dim_scores = {
                k: review_data.get(k, 0)
                for k in (
                    "soundness", "presentation", "contribution",
                    "originality", "clarity", "significance",
                    "reproducibility",
                )
            }
            valid = {k: v for k, v in dim_scores.items() if isinstance(v, (int, float)) and v > 0}
            if valid:
                review_data["mean_score"] = round(sum(valid.values()) / len(valid), 2)
            return review_data
    except (RuntimeError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("PDF review LLM call failed: %s", exc)

    return {}

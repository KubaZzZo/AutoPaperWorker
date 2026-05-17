"""Stage 15 research-decision helpers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _chat_with_prompt,
    _get_evolution_overlay,
    _read_prior_artifact,
    _utcnow_iso,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger("researchclaw.pipeline.stage_impls._analysis")

def _parse_decision(text: str) -> str:
    """Extract PROCEED/PIVOT/REFINE from decision text.

    Looks for the first standalone keyword on its own line after a
    ``## Decision`` heading.  Falls back to a keyword scan of the first
    few lines after the heading, but only matches the keyword itself
    (not mentions inside explanatory prose like "PIVOT is not warranted").
    Returns lowercase ``"proceed"`` / ``"pivot"`` / ``"refine"``.
    Defaults to ``"proceed"`` if nothing matches.
    """
    text_upper = text.upper()
    # Look in the first occurrence after "## Decision" heading
    decision_section = ""
    for keyword in ("## DECISION", "## Decision", "## decision"):
        if keyword.upper() in text_upper:
            idx = text_upper.index(keyword.upper())
            decision_section = text[idx : idx + 200]
            break
    search_text = decision_section or text[:500]

    # First try: look for a line that is just the keyword (possibly with
    # whitespace / markdown bold / trailing punctuation).
    for line in search_text.splitlines():
        stripped = line.strip().strip("*").strip("#").strip()
        if stripped.upper() in ("PROCEED", "PIVOT", "REFINE"):
            return stripped.lower()

    # Fallback: regex for standalone word boundaries so that
    # "PIVOT is not warranted" does NOT match as a decision.
    for kw in ("PIVOT", "REFINE", "PROCEED"):
        # Only match if the keyword appears as the FIRST keyword-class token
        # on its own (not embedded in a sentence saying "not PIVOT").
        pattern = re.compile(
            r"(?:^|##\s*Decision\s*\n\s*)" + kw, re.IGNORECASE | re.MULTILINE
        )
        if pattern.search(search_text):
            return kw.lower()

    # Last resort: position-based — prefer whichever keyword appears LAST
    # (the final conclusion after deliberation is more reliable than early mentions)
    # BUG-DA8-08: Old code always returned "refine" when both keywords present
    search_upper = search_text.upper()
    last_refine = search_upper.rfind("REFINE")
    last_pivot = search_upper.rfind("PIVOT")
    if last_refine >= 0 and (last_pivot < 0 or last_refine > last_pivot):
        return "refine"
    if last_pivot >= 0 and (last_refine < 0 or last_pivot > last_refine):
        return "pivot"
    return "proceed"


def _execute_research_decision(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    analysis = _read_prior_artifact(run_dir, "analysis.md") or ""

    # P6: Detect degenerate REFINE cycles — inject warning if metrics stagnate
    degenerate_hint = ""
    refine_log_text = _read_prior_artifact(run_dir, "refinement_log.json")
    if refine_log_text:
        try:
            refinement_log = json.loads(refine_log_text)
            iterations = refinement_log.get("iterations", [])
            metrics = [
                iteration.get("metric")
                for iteration in iterations
                if isinstance(iteration, dict)
            ]
            valid_metrics = [metric for metric in metrics if metric is not None]
            all_saturated = valid_metrics and all(
                metric <= 0.001 or metric >= 0.999 for metric in valid_metrics
            )
            all_identical = len(set(valid_metrics)) <= 1 and len(valid_metrics) >= 2
            if all_saturated or all_identical:
                degenerate_hint = (
                    "\n\nSYSTEM WARNING — DEGENERATE REFINE CYCLE DETECTED:\n"
                    f"Metrics across {len(valid_metrics)} iterations: {valid_metrics}\n"
                    "All iterations produce identical/saturated results. Further REFINE "
                    "cycles CANNOT fix this — the underlying benchmark design is too "
                    "easy/hard. You SHOULD choose PROCEED with a quality caveat rather "
                    "than REFINE again.\n"
                )
                logger.warning("P6: Degenerate refine cycle detected, injecting PROCEED hint")
        except (json.JSONDecodeError, OSError):
            logger.debug("P6: Failed to parse refinement_log for degenerate cycle check", exc_info=True)

    # Phase 2: Inject experiment diagnosis into decision prompt
    _diagnosis_hint = ""
    _diag_path = run_dir / "experiment_diagnosis.json"
    if _diag_path.exists():
        try:
            _diag_data = json.loads(_diag_path.read_text(encoding="utf-8"))
            _qa = _diag_data.get("quality_assessment", {})
            _mode = _qa.get("mode", "unknown")
            _sufficient = _qa.get("sufficient", False)
            _deficiency_types = _qa.get("deficiency_types", [])
            if not _sufficient:
                _diagnosis_hint = (
                    "\n\n## EXPERIMENT DIAGNOSIS (from automated analysis)\n"
                    f"Quality mode: {_mode}\n"
                    f"Sufficient for full paper: NO\n"
                    f"Issues found: {', '.join(_deficiency_types)}\n\n"
                    "IMPORTANT: The experiment has significant issues. "
                    "If REFINE is chosen, a structured repair prompt is available "
                    "at repair_prompt.txt with specific fixes for identified issues.\n"
                    "If the same issues persist after 2+ REFINE cycles, choose PROCEED "
                    "with appropriate quality caveats.\n"
                )
                logger.info(
                    "Stage 15: Injected experiment diagnosis — mode=%s, issues=%s",
                    _mode, _deficiency_types,
                )
        except (json.JSONDecodeError, OSError):
            logger.debug("Stage 15: Failed to parse experiment diagnosis", exc_info=True)

    # Improvement C: Check ablation quality — if >50% trivial, push REFINE
    _ablation_refine_hint = ""
    # BUG-DA8-16: Prefer experiment_summary_best.json (promoted best) over
    # alphabetically-last stage-14* (which could be a stale versioned dir)
    _exp_sum_path = run_dir / "experiment_summary_best.json"
    if not _exp_sum_path.is_file():
        _exp_sum_path = None
        for _s14 in sorted(run_dir.glob("stage-14*/experiment_summary.json"), reverse=True):
            _exp_sum_path = _s14
            break
    if _exp_sum_path and _exp_sum_path.is_file():
        try:
            from researchclaw.pipeline.stage_impls._paper_writing import (
                _check_ablation_effectiveness,
            )
            _abl_exp = json.loads(_exp_sum_path.read_text(encoding="utf-8"))
            _abl_warnings = _check_ablation_effectiveness(_abl_exp, threshold=0.02)
            if _abl_warnings:
                _trivial_count = sum(1 for w in _abl_warnings if "ineffective" in w.lower() or "trivial" in w.lower())
                _total_abl = max(1, len(_abl_warnings))
                if _trivial_count / _total_abl > 0.5:
                    _ablation_refine_hint = (
                        "\n\n## ABLATION QUALITY ASSESSMENT (CRITICAL)\n"
                        f"STRONG RECOMMENDATION: Choose REFINE.\n"
                        f"{_trivial_count}/{_total_abl} ablations show <2% difference from baseline "
                        f"(trivially similar). This means the ablation design is broken.\n"
                        "Warnings:\n" + "\n".join(f"- {w}" for w in _abl_warnings) + "\n"
                    )
                    logger.warning("C: %d/%d ablations trivial → recommending REFINE", _trivial_count, _total_abl)
        except (ImportError, json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Stage 15: Ablation quality assessment skipped", exc_info=True)

    if llm is not None:
        _pm = prompts or PromptManager()
        _overlay = _get_evolution_overlay(run_dir, "research_decision")
        sp = _pm.for_stage("research_decision", evolution_overlay=_overlay, analysis=analysis)
        _user = sp.user + degenerate_hint + _diagnosis_hint + _ablation_refine_hint
        resp = _chat_with_prompt(llm, sp.system, _user)
        decision_md = resp.content
    else:
        decision_md = f"""# Research Decision

## Decision
PROCEED

## Justification
Current evidence suggests measurable progress with actionable limitations.

## Next Actions
- Build detailed paper outline
- Expand ablation and uncertainty analysis in writing

Generated: {_utcnow_iso()}
"""
    (stage_dir / "decision.md").write_text(decision_md, encoding="utf-8")

    # --- Extract structured decision ---
    decision = _parse_decision(decision_md)

    # T3.1: Validate decision quality — check for minimum experiment rigor
    _quality_warnings: list[str] = []
    _dec_lower = decision_md.lower()
    if "baseline" not in _dec_lower and "control" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention baselines")
    if "seed" not in _dec_lower and "replicat" not in _dec_lower and "run" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention multi-seed/replicate runs")
    if "metric" not in _dec_lower and "accuracy" not in _dec_lower and "loss" not in _dec_lower:
        _quality_warnings.append("Decision text does not mention evaluation metrics")
    if _quality_warnings:
        logger.warning("T3.1: Decision quality warnings: %s", _quality_warnings)

    decision_payload = {
        "decision": decision,
        "raw_text_excerpt": decision_md[:500],
        "quality_warnings": _quality_warnings,
        "generated": _utcnow_iso(),
    }
    (stage_dir / "decision_structured.json").write_text(
        json.dumps(decision_payload, indent=2), encoding="utf-8"
    )
    logger.info("Research decision: %s", decision)

    return StageResult(
        stage=Stage.RESEARCH_DECISION,
        status=StageStatus.DONE,
        artifacts=("decision.md", "decision_structured.json"),
        evidence_refs=("stage-15/decision.md",),
        decision=decision,
    )


__all__ = ["_execute_research_decision", "_parse_decision"]

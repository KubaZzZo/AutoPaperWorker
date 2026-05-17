"""Stage 16 paper outline generation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _build_context_preamble,
    _chat_with_prompt,
    _default_paper_outline,
    _get_evolution_overlay,
    _read_best_analysis,
    _read_prior_artifact,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)


def _topic_is_literature_first(config: RCConfig) -> bool:
    """Return True when the topic is a survey/review or the project uses docs-first mode.

    Literature-first topics produce papers grounded in existing work rather
    than novel experiments, so the "all simulated" and "no real metrics"
    hard blocks should be bypassed.
    """
    topic_lower = config.research.topic.lower()
    if any(kw in topic_lower for kw in ("survey", "review", "meta-analysis", "literature review")):
        return True
    project_mode = getattr(config.research, "project_mode", None)
    if isinstance(project_mode, str) and project_mode.lower() == "docs-first":
        return True
    return False


def _execute_paper_outline(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    analysis = _read_best_analysis(run_dir)
    decision = _read_prior_artifact(run_dir, "decision.md") or ""
    preamble = _build_context_preamble(
        config,
        run_dir,
        include_analysis=True,
        include_decision=True,
        include_experiment_data=True,
    )

    # WS-5.2: Read iteration feedback if available (multi-round iteration)
    feedback = ""
    iter_ctx_path = run_dir / "iteration_context.json"
    if iter_ctx_path.exists():
        try:
            ctx = json.loads(iter_ctx_path.read_text(encoding="utf-8"))
            iteration = ctx.get("iteration", 1)
            prev_score = ctx.get("quality_score")
            reviews_excerpt = ctx.get("reviews_excerpt", "")
            if iteration > 1 and reviews_excerpt:
                feedback = (
                    f"\n\n## Iteration {iteration} Feedback\n"
                    f"Previous quality score: {prev_score}/10\n"
                    f"Reviewer feedback to address:\n{reviews_excerpt[:2000]}\n"
                    f"\nYou MUST address these reviewer concerns in this revision.\n"
                )
        except (json.JSONDecodeError, KeyError):
            logger.debug("Stage 16: Failed to parse iteration feedback context", exc_info=True)

    if llm is not None:
        _pm = prompts or PromptManager()
        # IMP-20: Pass academic style guide block for outline stage
        try:
            _asg = _pm.block("academic_style_guide")
        except KeyError:
            _asg = ""
        _overlay = _get_evolution_overlay(run_dir, "paper_outline")
        sp = _pm.for_stage(
            "paper_outline",
            evolution_overlay=_overlay,
            preamble=preamble,
            topic_constraint=_pm.block("topic_constraint", topic=config.research.topic),
            feedback=feedback,
            analysis=analysis,
            decision=decision,
            academic_style_guide=_asg,
        )
        resp = _chat_with_prompt(
            llm,
            sp.system,
            sp.user,
            json_mode=sp.json_mode,
            max_tokens=sp.max_tokens,
        )
        outline = resp.content
        # Reasoning models may consume all tokens on CoT — retry with more
        if not outline.strip() and sp.max_tokens:
            logger.warning("Empty outline from LLM — retrying with 2x tokens")
            resp = _chat_with_prompt(
                llm,
                sp.system,
                sp.user,
                json_mode=sp.json_mode,
                max_tokens=sp.max_tokens * 2,
            )
            outline = resp.content
        if not outline.strip():
            logger.warning("LLM returned empty outline — using default")
            outline = _default_paper_outline(config.research.topic)
    else:
        outline = _default_paper_outline(config.research.topic)
    (stage_dir / "outline.md").write_text(outline, encoding="utf-8")
    return StageResult(
        stage=Stage.PAPER_OUTLINE,
        status=StageStatus.DONE,
        artifacts=("outline.md",),
        evidence_refs=("stage-16/outline.md",),
    )

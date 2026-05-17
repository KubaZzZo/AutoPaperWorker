"""Shared constants, data classes, and utility functions for the pipeline executor."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline.artifact_io import (
    find_prior_file as _find_prior_file_impl,
)
from researchclaw.pipeline.artifact_io import (
    load_hardware_profile as _load_hardware_profile_impl,
)
from researchclaw.pipeline.artifact_io import (
    read_best_analysis as _read_best_analysis_impl,
)
from researchclaw.pipeline.artifact_io import (
    read_prior_artifact as _read_prior_artifact_impl,
)
from researchclaw.pipeline.artifact_io import (
    write_stage_meta as _write_stage_meta_impl,
)
from researchclaw.pipeline.code_blocks import (
    extract_code_block as _extract_code_block_impl,
)
from researchclaw.pipeline.code_blocks import (
    extract_multi_file_blocks as _extract_multi_file_blocks_impl,
)
from researchclaw.pipeline.experiment_results import (
    _CONDITION_RE,
)
from researchclaw.pipeline.experiment_results import (
    collect_experiment_results as _collect_experiment_results_impl,
)
from researchclaw.pipeline.experiment_results import (
    parse_metrics_from_stdout as _parse_metrics_from_stdout_impl,
)
from researchclaw.pipeline.parsing import (
    extract_yaml_block as _extract_yaml_block_impl,
)
from researchclaw.pipeline.parsing import (
    parse_jsonl_rows as _parse_jsonl_rows_impl,
)
from researchclaw.pipeline.parsing import (
    safe_json_loads as _safe_json_loads_impl,
)
from researchclaw.pipeline.parsing import (
    write_jsonl as _write_jsonl_impl,
)
from researchclaw.pipeline.runtime_issues import (
    detect_runtime_issues as _detect_runtime_issues_impl,
)
from researchclaw.pipeline.stages import (
    Stage,
    StageStatus,
)
from researchclaw.pipeline.topic_utils import (
    build_fallback_queries as _build_fallback_queries_impl,
)
from researchclaw.pipeline.topic_utils import (
    extract_topic_keywords as _extract_topic_keywords_impl,
)
from researchclaw.pipeline.topic_utils import (
    topic_constraint_block as _topic_constraint_block_impl,
)
from researchclaw.prompts import PromptManager
from researchclaw.utils.text import BASE_STOP_WORDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageResult:
    """Outcome of executing a single stage."""

    stage: Stage
    status: StageStatus
    artifacts: tuple[str, ...]
    error: str | None = None
    decision: str = "proceed"
    evidence_refs: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_SAFE_PACKAGES = {
    "numpy", "scipy", "torch", "sklearn", "matplotlib",
    "pandas", "seaborn", "tqdm", "gymnasium", "gym",
}

_METACLAW_SKILLS_DIR = str(Path.home() / ".metaclaw" / "skills")

# User-level custom skills directory (cross-project)
_USER_SKILLS_DIR = Path.home() / ".researchclaw" / "skills"

# Lazy-initialized skill registry (singleton for the process)
_skill_registry: object | None = None


def _get_skill_registry(config: object | None = None) -> object:
    """Return the global SkillRegistry, creating it on first call.

    Loads skills from (in priority order):
    1. Built-in skills shipped with the package
    2. User-level ``~/.researchclaw/skills/``
    3. Project-level ``.claude/skills/``
    4. MetaClaw cross-run skills ``~/.metaclaw/skills/``
    5. User-configured ``config.yaml → skills.custom_dirs``
    """
    global _skill_registry  # noqa: PLW0603
    if _skill_registry is not None:
        return _skill_registry
    try:
        from researchclaw.skills.registry import SkillRegistry

        custom_dirs: list[str] = []

        # User-level skills
        if _USER_SKILLS_DIR.is_dir():
            custom_dirs.append(str(_USER_SKILLS_DIR))

        # Project-level .claude/skills/
        project_skills = Path(__file__).resolve().parent.parent.parent / ".claude" / "skills"
        if project_skills.is_dir():
            custom_dirs.append(str(project_skills))

        # MetaClaw skills
        metaclaw = Path(_METACLAW_SKILLS_DIR)
        if metaclaw.is_dir():
            custom_dirs.append(str(metaclaw))

        # Config-specified custom dirs
        if config is not None:
            skills_cfg = getattr(config, "skills", None)
            if skills_cfg:
                for d in getattr(skills_cfg, "custom_dirs", ()):
                    if d:
                        custom_dirs.append(str(d))
                for d in getattr(skills_cfg, "external_dirs", ()):
                    if d:
                        custom_dirs.append(str(d))

        _skill_registry = SkillRegistry(
            custom_dirs=custom_dirs,
            auto_match=True,
            max_skills_per_stage=getattr(
                getattr(config, "skills", None), "max_skills_per_stage", 3
            ) if config else 3,
            fallback_matching=True,
        )
        logger.info(
            "Skill registry initialized: %d skills from %d sources",
            _skill_registry.count(),
            1 + len(custom_dirs),
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        # Fallback: create empty registry so we never crash
        from researchclaw.skills.registry import SkillRegistry
        _skill_registry = SkillRegistry(builtin_dir="/dev/null")
        logger.debug("Skill registry init failed, using empty registry")
    return _skill_registry

# --- P1-1: Topic keyword extraction for domain pre-filter ---
_STOP_WORDS = BASE_STOP_WORDS

# ---------------------------------------------------------------------------
# Timestamp utility
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Fallback query builder
# ---------------------------------------------------------------------------


def _build_fallback_queries(topic: str) -> list[str]:
    """Extract meaningful search queries from a long topic string.

    Instead of using the raw topic as a query (which is often 200+ chars
    and returns garbage from search engines), extract noun phrases and
    domain keywords. Returns 5-10 targeted queries.
    """
    return _build_fallback_queries_impl(topic)


# ---------------------------------------------------------------------------
# Stage metadata I/O
# ---------------------------------------------------------------------------


def _write_stage_meta(
    stage_dir: Path, stage: Stage, run_id: str, result: StageResult
) -> None:
    _write_stage_meta_impl(
        stage_dir,
        stage,
        run_id,
        result,
        timestamp_factory=_utcnow_iso,
    )


# ---------------------------------------------------------------------------
# Sandbox dependency helper
# ---------------------------------------------------------------------------


def _ensure_sandbox_deps(code: str, python_path: str) -> list[str]:
    """P7: Scan code imports and auto-install missing common packages."""
    import subprocess as _sp

    imports: set[str] = set()
    for line in code.splitlines():
        m = re.match(r"^(?:from|import)\s+(\w+)", line.strip())
        if m:
            imports.add(m.group(1))

    to_check = imports & _SANDBOX_SAFE_PACKAGES
    if not to_check:
        return []

    py = python_path
    py_path = Path(py)
    if not py_path.is_absolute():
        py_path = Path.cwd() / py_path

    installed: list[str] = []
    for pkg in sorted(to_check):
        try:
            r = _sp.run(
                [str(py_path), "-c", f"import {pkg}"],
                capture_output=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                pip_name = "scikit-learn" if pkg == "sklearn" else pkg
                logger.info("Sandbox: installing missing dependency '%s'", pip_name)
                _sp.run(
                    [str(py_path), "-m", "pip", "install", pip_name, "--quiet"],
                    capture_output=True, timeout=120,
                    encoding="utf-8", errors="replace",
                )
                installed.append(pip_name)
        except (OSError, _sp.SubprocessError, ValueError) as exc:
            logger.warning("Sandbox: failed to check/install '%s': %s", pkg, exc, exc_info=True)

    if installed:
        logger.info("Sandbox: auto-installed packages: %s", ", ".join(installed))
    return installed


# ---------------------------------------------------------------------------
# Prior artifact I/O
# ---------------------------------------------------------------------------


def _read_best_analysis(run_dir: Path) -> str:
    """BUG-225: Read analysis.md from the best Stage 14 iteration.

    Prefers ``analysis_best.md`` at run root (written by
    ``_promote_best_stage14``) over ``_read_prior_artifact("analysis.md")``
    which may pick a degenerate non-versioned stage-14 directory.
    """
    return _read_best_analysis_impl(run_dir)


def _read_prior_artifact(run_dir: Path, filename: str) -> str | None:
    return _read_prior_artifact_impl(run_dir, filename, diagnostic_logger=logger)


def _find_prior_file(run_dir: Path, filename: str) -> Path | None:
    """Like ``_read_prior_artifact`` but returns the *Path* instead of content."""
    return _find_prior_file_impl(run_dir, filename)


def _load_hardware_profile(run_dir: Path) -> dict[str, Any] | None:
    """Load hardware_profile.json from a prior stage (usually stage-01)."""
    return _load_hardware_profile_impl(run_dir, diagnostic_logger=logger)


# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------


def _extract_yaml_block(text: str) -> str:
    """Extract YAML from text that may contain ACP noise.

    Strips [thinking] blocks, insight blocks, and other ACP artifacts
    before looking for YAML in markdown fences or raw text.
    """
    return _extract_yaml_block_impl(text)


def _safe_json_loads(text: str, default: Any) -> Any:
    """Parse JSON from text, handling noisy ACP output.

    Tries multiple strategies: direct parse, markdown fence extraction,
    balanced brace matching (largest dict wins), and array brackets.
    """
    return _safe_json_loads_impl(text, default)


def _extract_code_block(content: str) -> str:
    return _extract_code_block_impl(content)


def _extract_multi_file_blocks(content: str) -> dict[str, str]:
    """Parse LLM response containing multiple files with filename markers.

    Expected format::

        ```filename:main.py
        import model
        ...
        ```

        ```filename:model.py
        class MyModel:
        ...
        ```

    Also handles common LLM format variations:
    - ````` ```python filename:main.py````` (space before filename)
    - ````` ``` filename:main.py````` (space after backticks)
    - ``filename:main.py`` on next line after backticks
    - ``# FILE: main.py`` comment markers inside code blocks

    Falls back to treating the entire code block as ``main.py`` if no
    ``filename:`` markers are found.

    Returns a dict mapping filename to code content.
    """
    return _extract_multi_file_blocks_impl(content)


def _parse_jsonl_rows(text: str) -> list[dict[str, Any]]:
    return _parse_jsonl_rows_impl(text)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_jsonl_impl(path, rows)



# BUG-173: regex for condition=name metric=value format


def _parse_metrics_from_stdout(stdout: str) -> dict[str, Any]:
    """Parse metric lines from experiment stdout.

    Handles multiple formats:
    - ``name: value`` (e.g. ``loss: 0.0042``)
    - ``UCB (Stochastic) cumulative_regret: 361.9233``
    - ``condition=name metric=value`` (per-condition output)
    - ``condition=name/metric_name metric=value``

    Returns a flat dict of metric_name -> value.
    Filters out log/status lines using :func:`is_metric_name`.
    """
    return _parse_metrics_from_stdout_impl(
        stdout,
        condition_pattern=_CONDITION_RE,
        diagnostic_logger=logger,
    )


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _chat_with_prompt(
    llm: LLMClient,
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    max_tokens: int | None = None,
    retries: int = 0,
    strip_thinking: bool = True,
) -> Any:
    """Send a chat request with optional retry on timeout/transient errors.

    Parameters
    ----------
    retries:
        Number of extra attempts after the first failure (0 = no retry).
        Uses exponential backoff: 2s, 4s, 8s, ...
    strip_thinking:
        If True (default for pipeline usage), strip ``<think>`` tags from
        the LLM response.  This prevents chain-of-thought leakage from
        breaking YAML / JSON / LaTeX parsers downstream.
    """
    import time

    messages = [{"role": "user", "content": user}]
    last_exc: Exception | None = None
    _effective_json_mode = json_mode
    for attempt in range(1 + retries):
        try:
            if _effective_json_mode and max_tokens is not None:
                return llm.chat(messages, system=system, json_mode=True, max_tokens=max_tokens, strip_thinking=strip_thinking)
            if _effective_json_mode:
                return llm.chat(messages, system=system, json_mode=True, strip_thinking=strip_thinking)
            if max_tokens is not None:
                return llm.chat(messages, system=system, max_tokens=max_tokens, strip_thinking=strip_thinking)
            return llm.chat(messages, system=system, strip_thinking=strip_thinking)
        except (
            RuntimeError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
            urllib.error.URLError,
        ) as exc:
            last_exc = exc
            # Auto-disable json_mode on HTTP 400 — likely provider incompatibility
            _err_str = str(exc)
            if _effective_json_mode and "400" in _err_str:
                logger.warning(
                    "HTTP 400 with json_mode=True — disabling json_mode for retry "
                    "(provider may not support response_format).",
                    exc_info=True,
                )
                _effective_json_mode = False
            if attempt < retries:
                delay = 2 ** (attempt + 1)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1,
                    1 + retries,
                    exc,
                    delay,
                    exc_info=True,
                )
                time.sleep(delay)
            else:
                raise last_exc from None
    raise last_exc  # type: ignore[misc]  # unreachable but satisfies type checker


def _get_evolution_overlay(
    run_dir: Path | None,
    stage_name: str,
    *,
    config: object | None = None,
    topic: str = "",
) -> str:
    """Load evolution lessons + matched skills for prompt injection.

    Combines three sources:
    1. Intra-run lessons (from current run's evolution dir)
    2. Cross-run MetaClaw skills (from ~/.metaclaw/skills/)
    3. Matched skills from the SkillRegistry (builtin + user + external)

    The SkillRegistry automatically matches skills to the current stage
    using trigger keywords and stage applicability metadata.

    Returns empty string if no relevant lessons/skills exist or on any error.
    """
    parts: list[str] = []

    # --- Section 1: Evolution lessons + MetaClaw arc-* skills ---
    if run_dir is not None:
        try:
            from researchclaw.evolution import EvolutionStore

            store = EvolutionStore(run_dir / "evolution")
            evo_overlay = store.build_overlay(
                stage_name, max_lessons=5, skills_dir=_METACLAW_SKILLS_DIR
            )
            if evo_overlay:
                parts.append(evo_overlay)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
            logger.debug(
                "Failed to build evolution lesson overlay for stage %s: %s",
                stage_name,
                exc,
                exc_info=True,
            )

    # --- Section 2: Matched skills from SkillRegistry ---
    try:
        registry = _get_skill_registry(config)
        context = f"{stage_name} {topic}".strip()
        matched = registry.match(context, stage_name)
        if matched:
            skills_text = registry.export_for_prompt(matched, max_chars=4000)
            if skills_text:
                parts.append(f"\n## Matched Domain Skills\n{skills_text}")
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.debug(
            "Failed to build matched skill overlay for stage %s: %s",
            stage_name,
            exc,
            exc_info=True,
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------


def _collect_json_context(
    directory: Path,
    *,
    max_files: int = 30,
    max_total_chars: int = 50_000,
) -> str:
    """Collect JSON context from a directory, with size limits.

    Large fields like ``stderr`` and ``stdout`` are stripped to avoid
    exceeding LLM token limits (the raw experiment output can be 5 MB+).
    """
    chunks: list[str] = []
    total = 0
    for file_path in sorted(directory.glob("*.json"))[:max_files]:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Strip verbose fields that bloat the context
        if isinstance(data, dict):
            for key in ("stderr", "stdout", "raw_output", "traceback"):
                if key in data and isinstance(data[key], str) and len(data[key]) > 500:
                    data[key] = data[key][:500] + f"\n... [truncated, {len(data[key])} chars total]"
        chunk = json.dumps(data, indent=2, ensure_ascii=False)
        if total + len(chunk) > max_total_chars:
            remaining = max_total_chars - total
            if remaining > 200:
                chunks.append(chunk[:remaining] + "\n... [truncated]")
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)


def _collect_experiment_results(
    run_dir: Path,
    metric_key: str = "",
    metric_direction: str = "maximize",
) -> dict[str, Any]:
    """Aggregate experiment metrics from runs/ directory across prior stages.

    Returns a dict with ``runs``, ``metrics_summary``, ``best_run``,
    ``latex_table``, and optionally ``structured_results``.
    """
    return _collect_experiment_results_impl(
        run_dir,
        metric_key=metric_key,
        metric_direction=metric_direction,
        json_loader=_safe_json_loads,
        diagnostic_logger=logger,
    )


def _build_context_preamble(
    config: RCConfig,
    run_dir: Path,
    *,
    include_goal: bool = False,
    include_hypotheses: bool = False,
    include_synthesis: bool = False,
    include_exp_plan: bool = False,
    include_analysis: bool = False,
    include_decision: bool = False,
    include_experiment_data: bool = False,
) -> str:
    parts = [
        "## Research Context",
        f"**Topic**: {config.research.topic}",
        f"**Domains**: {', '.join(config.research.domains) if config.research.domains else 'general'}",
    ]
    if include_goal:
        goal = _read_prior_artifact(run_dir, "goal.md")
        if goal:
            parts.append(f"\n### Goal\n{goal[:2200]}")
    if include_hypotheses:
        hyp = _read_prior_artifact(run_dir, "hypotheses.md")
        if hyp:
            parts.append(f"\n### Hypotheses\n{hyp[:2200]}")
    if include_synthesis:
        synthesis = _read_prior_artifact(run_dir, "synthesis.md")
        if synthesis:
            parts.append(f"\n### Synthesis\n{synthesis[:2200]}")
    if include_exp_plan:
        plan = _read_prior_artifact(run_dir, "exp_plan.yaml")
        if plan:
            parts.append(f"\n### Experiment Plan\n{plan[:2000]}")
    if include_analysis:
        analysis = _read_best_analysis(run_dir)
        if analysis:
            parts.append(f"\n### Result Analysis\n{analysis[:2500]}")
    if include_decision:
        decision = _read_prior_artifact(run_dir, "decision.md")
        if decision:
            parts.append(f"\n### Research Decision\n{decision[:1500]}")
    if include_experiment_data:
        hw_profile = _load_hardware_profile(run_dir)
        if hw_profile:
            hw_lines = ["### Hardware Environment"]
            for hk, hv in hw_profile.items():
                hw_lines.append(f"- **{hk}**: {hv}")
            parts.append("\n" + "\n".join(hw_lines))
        exp_summary = _read_prior_artifact(run_dir, "experiment_summary.json")
        if exp_summary:
            summary = _safe_json_loads(exp_summary, {})
            if isinstance(summary, dict) and summary.get("metrics_summary"):
                parts.append("\n### Experiment Results (Quantitative)")
                ms = summary["metrics_summary"]
                for mk, mv in ms.items():
                    if isinstance(mv, dict):
                        parts.append(
                            f"- **{mk}**: mean={mv.get('mean', '?')}, "
                            f"min={mv.get('min', '?')}, max={mv.get('max', '?')}, n={mv.get('count', '?')}"
                        )
                if summary.get("latex_table"):
                    parts.append(
                        f"\n### LaTeX Table\n```latex\n{summary['latex_table']}\n```"
                    )
    # --- HITL guidance injection ---
    for stage_dir in sorted(run_dir.glob("stage-*/hitl_guidance.md")):
        try:
            guidance = stage_dir.read_text(encoding="utf-8").strip()
            if guidance:
                stage_name = stage_dir.parent.name
                parts.append(
                    f"\n### Human Guidance ({stage_name})\n{guidance[:1000]}"
                )
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug(
                "Failed to read HITL guidance from %s: %s",
                stage_dir,
                exc,
                exc_info=True,
            )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Topic keywords and constraints
# ---------------------------------------------------------------------------


def _extract_topic_keywords(
    topic: str, domains: tuple[str, ...] | list[str] = ()
) -> list[str]:
    """Extract meaningful keywords from the research topic + domain list.

    Returns lowercased keyword list (2+ chars, no stop words).
    Used by the domain pre-filter to drop obviously irrelevant papers.
    """
    return _extract_topic_keywords_impl(topic, domains, stop_words=_STOP_WORDS)


# --- P1-2: Topic constraint block for paper generation stages ---
def _topic_constraint_block(topic: str) -> str:
    """Return a hard constraint instruction that anchors paper content to the topic.

    Prevents the common LLM failure mode of drifting off-topic or
    presenting environmental/infrastructure issues as research contributions.
    """
    return _topic_constraint_block_impl(topic)


def _detect_runtime_issues(sandbox_result: Any) -> str:
    """Detect NaN/Inf in metrics and extract stderr warnings from sandbox run.

    Returns a formatted string describing all runtime issues, or empty string
    if no issues are found.
    """
    return _detect_runtime_issues_impl(sandbox_result, diagnostic_logger=logger)


# ---------------------------------------------------------------------------
# NeurIPS checklist
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Paper-output helpers
# ---------------------------------------------------------------------------


from researchclaw.pipeline._paper_output_helpers import (
    _default_hypotheses as _default_hypotheses_impl,
    _default_paper_outline as _default_paper_outline_impl,
    _default_quality_report as _default_quality_report_impl,
    _extract_paper_title,
    _generate_framework_diagram_prompt as _generate_framework_diagram_prompt_impl,
    _generate_neurips_checklist,
    _multi_perspective_generate,
    _safe_filename,
    _synthesize_perspectives,
    reconcile_figure_refs,
)


def _generate_framework_diagram_prompt(
    paper_text: str,
    config: RCConfig,
    *,
    llm: LLMClient | None = None,
) -> str:
    return _generate_framework_diagram_prompt_impl(
        paper_text,
        config,
        llm=llm,
        chat_with_prompt=_chat_with_prompt,
    )


def _default_hypotheses(topic: str) -> str:
    return _default_hypotheses_impl(topic, _utcnow_iso)


def _default_paper_outline(topic: str) -> str:
    return _default_paper_outline_impl(topic, _utcnow_iso)


def _default_quality_report(threshold: float) -> dict[str, Any]:
    return _default_quality_report_impl(threshold, _utcnow_iso)

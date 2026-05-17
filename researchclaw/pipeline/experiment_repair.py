"""Experiment Repair Loop — diagnose, fix, and re-run experiments.

Orchestrates the cycle:
  1. Diagnose failures (``experiment_diagnosis.py``)
  2. Generate fixes via OpenCode or LLM
  3. Re-run experiment in sandbox/Docker
  4. Re-assess quality
  5. Repeat until sufficient or max cycles reached

Integrates between Stage 14 (result_analysis) and Stage 15 (research_decision).
"""

from __future__ import annotations

import json
import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from researchclaw.pipeline.experiment_diagnosis import (
    DeficiencyType,
    ExperimentDiagnosis,
    ExperimentQualityAssessment,
    PaperMode,
    assess_experiment_quality,
    diagnose_experiment,
)
from researchclaw.pipeline.experiment_repair_helpers import (
    _build_experiment_summary_from_run,
    _extract_code_blocks,
    _run_experiment_in_sandbox,
)

logger = logging.getLogger(__name__)

MAX_REPAIR_CYCLES = 3

ProgressReporter = Callable[[str], None]


def _report_progress(reporter: ProgressReporter | None, message: str) -> None:
    if reporter is not None:
        reporter(message)


@dataclass
class RepairCycleResult:
    """Result of one repair cycle."""

    cycle: int
    diagnosis: ExperimentDiagnosis
    repair_applied: bool = False
    repair_description: str = ""
    new_assessment: ExperimentQualityAssessment | None = None
    error: str = ""


@dataclass
class ExperimentRepairResult:
    """Final result of the entire repair loop."""

    success: bool  # True if experiment is now sufficient for full_paper
    total_cycles: int
    final_mode: PaperMode
    final_assessment: ExperimentQualityAssessment | None = None
    cycle_history: list[RepairCycleResult] = field(default_factory=list)
    best_experiment_summary: dict | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "total_cycles": self.total_cycles,
            "final_mode": self.final_mode.value,
            "cycle_history": [
                {
                    "cycle": cr.cycle,
                    "repair_applied": cr.repair_applied,
                    "repair_description": cr.repair_description,
                    "error": cr.error,
                    "diagnosis_summary": cr.diagnosis.summary if cr.diagnosis else "",
                }
                for cr in self.cycle_history
            ],
        }


# ---------------------------------------------------------------------------
# Repair prompt generation
# ---------------------------------------------------------------------------


def build_repair_prompt(
    diagnosis: ExperimentDiagnosis,
    original_code: dict[str, str],
    experiment_plan: dict | None = None,
    time_budget_sec: int = 2400,
) -> str:
    """Build a structured repair prompt for OpenCode or LLM.

    Parameters
    ----------
    diagnosis:
        The structured diagnosis from ``diagnose_experiment()``.
    original_code:
        Mapping of filename → source code for the current experiment.
    experiment_plan:
        The experiment design plan.
    time_budget_sec:
        Available time budget for the experiment.

    Returns
    -------
    str
        A formatted prompt suitable for OpenCode or code-generation LLM.
    """
    sections: list[str] = []

    sections.append("# EXPERIMENT REPAIR TASK\n")
    sections.append(
        "The previous experiment run had failures. Your job is to fix "
        "the specific issues identified below. Do NOT rewrite from scratch — "
        "fix ONLY the identified problems.\n"
    )

    # Diagnosis section
    sections.append(diagnosis.to_repair_prompt())

    # Scope reduction guidance
    if any(d.type == DeficiencyType.TIME_GUARD_DOMINANT for d in diagnosis.deficiencies):
        n_planned = diagnosis.total_planned
        n_completed = len(diagnosis.conditions_completed)
        max_conditions = max(3, n_completed + 1)
        sections.append(
            f"\n## SCOPE REDUCTION REQUIRED\n"
            f"The experiment had {n_planned} conditions but only {n_completed} "
            f"completed within the time budget of {time_budget_sec}s.\n"
            f"**Reduce to at most {max_conditions} conditions:**\n"
            f"1. Keep the BASELINE condition (no modification)\n"
            f"2. Keep the PROPOSED method (paper's main contribution)\n"
            f"3. Keep 1 ablation (remove most impactful component)\n"
            f"4. Remove all other conditions\n"
            f"5. Reduce epochs by 30-50% if still tight on time\n"
            f"6. Reduce seeds from 3 to 2 if needed\n"
        )

    # Dependency fixes
    dep_issues = [d for d in diagnosis.deficiencies if d.type == DeficiencyType.MISSING_DEPENDENCY]
    if dep_issues:
        sections.append("\n## DEPENDENCY FIXES\n")
        sections.append("Add these to requirements.txt:\n")
        for d in dep_issues:
            # Extract package name from description
            sections.append(f"- {d.description}")

    # Original code
    sections.append("\n## CURRENT CODE (fix in-place)\n")
    for filename, content in sorted(original_code.items()):
        # Truncate very long files
        if len(content) > 5000:
            content = content[:5000] + "\n... (truncated)"
        sections.append(f"### {filename}\n```python\n{content}\n```\n")

    # Constraints
    sections.append(
        f"\n## CONSTRAINTS\n"
        f"- Time budget: {time_budget_sec} seconds total\n"
        f"- Pre-cached datasets: CIFAR-10, CIFAR-100, MNIST, FashionMNIST, STL-10 at /opt/datasets\n"
        f"- Every condition MUST output: condition=CONDNAME metric=VALUE\n"
        f"- The code must run without errors for at least 1 seed per condition\n"
    )

    # Output format instruction
    sections.append(
        "\n## OUTPUT FORMAT\n"
        "Output each fixed file using this format:\n"
        "```python filename.py\n"
        "<fixed code>\n"
        "```\n"
        "Include ALL files (main.py, requirements.txt, setup.py if needed).\n"
        "For requirements.txt, use:\n"
        "```python requirements.txt\n"
        "<package list>\n"
        "```\n"
    )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Best results selection
# ---------------------------------------------------------------------------


def select_best_results(
    run_dir: Path,
    cycle_history: list[RepairCycleResult],
) -> dict | None:
    """Select the best experiment_summary across all repair cycles.

    Looks for experiment_summary.json files in versioned stage directories
    and returns the one with the best primary metric / most conditions.

    Returns None if no valid summary found.
    """
    candidates: list[tuple[float, int, dict]] = []

    # Check main stage-14
    main_summary = _try_load_summary(run_dir / "stage-14" / "experiment_summary.json")
    if main_summary:
        score = _summary_quality_score(main_summary)
        candidates.append((score, 0, main_summary))

    # Check repair versions
    for i in range(1, MAX_REPAIR_CYCLES + 1):
        path = run_dir / f"stage-14_repair_v{i}" / "experiment_summary.json"
        summary = _try_load_summary(path)
        if summary:
            score = _summary_quality_score(summary)
            candidates.append((score, i, summary))

    if not candidates:
        return None

    # Sort by quality score (descending)
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_cycle, best_summary = candidates[0]
    logger.info(
        "Best experiment results from cycle %d (score=%.2f)", best_cycle, best_score
    )
    return best_summary


def _try_load_summary(path: Path) -> dict | None:
    """Try to load and parse an experiment_summary.json."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _summary_quality_score(summary: dict) -> float:
    """Compute a simple quality score for ranking summaries.

    Higher = better. Considers:
    - Number of completed conditions (×10)
    - Whether primary_metric is non-NaN (×5)
    - Number of metric keys (×1)
    """
    import math

    score = 0.0
    n_conditions = len(summary.get("condition_summaries", {}))
    score += n_conditions * 10.0

    pm = summary.get("best_run", {}).get("metrics", {}).get("primary_metric")
    if isinstance(pm, (int, float)) and math.isfinite(pm):
        score += 5.0

    n_keys = summary.get("total_metric_keys", 0)
    score += n_keys * 1.0

    return score


# ---------------------------------------------------------------------------
# Full repair loop
# ---------------------------------------------------------------------------


def run_repair_loop(
    run_dir: Path,
    config: Any,
    run_id: str = "",
    progress_reporter: ProgressReporter | None = None,
) -> ExperimentRepairResult:
    """Execute the full experiment repair loop.

    After Stage 14 diagnosis finds quality issues:
    1. Load current experiment code
    2. For each cycle: diagnose → LLM/OpenCode fix → re-run in sandbox → re-assess
    3. Select best results across all cycles
    4. Return structured result

    Parameters
    ----------
    run_dir:
        Path to the pipeline run directory (contains stage-* subdirs).
    config:
        RCConfig instance with experiment and LLM settings.
    run_id:
        Pipeline run ID for logging.

    Returns
    -------
    ExperimentRepairResult
    """
    repair_cfg = config.experiment.repair

    # Load initial experiment summary
    summary = _load_experiment_summary(run_dir)
    if not summary:
        logger.warning("[%s] Repair loop: no experiment_summary.json found", run_id)
        return ExperimentRepairResult(
            success=False, total_cycles=0, final_mode=PaperMode.TECHNICAL_REPORT,
        )

    # Initial quality assessment — pass user-configured thresholds
    ref_log = _load_refinement_log(run_dir)
    _min_cond = getattr(repair_cfg, "min_conditions", 3)
    qa = assess_experiment_quality(summary, ref_log, min_conditions=_min_cond)
    if qa.sufficient:
        logger.info("[%s] Repair loop: experiment already sufficient (%s)", run_id, qa.mode.value)
        return ExperimentRepairResult(
            success=True, total_cycles=0, final_mode=qa.mode,
            final_assessment=qa, best_experiment_summary=summary,
        )

    # Load experiment code
    code = _load_experiment_code(run_dir)
    if not code:
        logger.warning("[%s] Repair loop: no experiment code found", run_id)
        return ExperimentRepairResult(
            success=False, total_cycles=0, final_mode=qa.mode,
        )

    # Collect stdout/stderr for diagnosis
    stdout, stderr = _collect_experiment_output(run_dir)

    # Load experiment plan
    plan = _load_experiment_plan(run_dir)

    # Create LLM client
    try:
        from researchclaw.llm import create_llm_client
        llm = create_llm_client(config)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.error("[%s] Repair loop: cannot create LLM client: %s", run_id, exc, exc_info=True)
        return ExperimentRepairResult(
            success=False, total_cycles=0, final_mode=qa.mode,
        )

    cycle_history: list[RepairCycleResult] = []
    best_summary = summary
    best_mode = qa.mode
    best_updated = False
    max_cycles = min(repair_cfg.max_cycles, MAX_REPAIR_CYCLES)
    loop_start = _time.monotonic()
    prior_diagnoses: list[dict] = []

    for cycle in range(1, max_cycles + 1):
        logger.info("[%s] Repair cycle %d/%d starting...", run_id, cycle, max_cycles)
        _report_progress(
            progress_reporter,
            f"[{run_id}] Repair cycle {cycle}/{max_cycles}...",
        )

        # 1. Diagnose current state
        diag = diagnose_experiment(
            experiment_summary=summary,
            experiment_plan=plan,
            refinement_log=ref_log,
            stdout=stdout,
            stderr=stderr,
            prior_diagnoses=prior_diagnoses or None,
        )
        prior_diagnoses.append(diag.to_dict() if hasattr(diag, "to_dict") else {})

        # 2. Build repair prompt
        repair_prompt = build_repair_prompt(
            diag, code, experiment_plan=plan,
            time_budget_sec=config.experiment.time_budget_sec,
        )

        # 3. Get fixed code via LLM (with OpenCode fallback)
        fixed_code = _get_repaired_code(
            repair_prompt, code, llm, config, run_dir, cycle,
        )

        if not fixed_code:
            cycle_result = RepairCycleResult(
                cycle=cycle, diagnosis=diag,
                repair_applied=False,
                error="Failed to generate repaired code",
            )
            cycle_history.append(cycle_result)
            logger.warning("[%s] Repair cycle %d: code generation failed", run_id, cycle)
            break

        # 4. Save fixed code to versioned directory
        repair_dir = run_dir / f"stage-14_repair_v{cycle}"
        repair_dir.mkdir(parents=True, exist_ok=True)
        exp_dir = repair_dir / "experiment"
        exp_dir.mkdir(parents=True, exist_ok=True)

        for fname, content in fixed_code.items():
            (exp_dir / fname).write_text(content, encoding="utf-8")
        logger.info(
            "[%s] Repair cycle %d: saved %d files to %s",
            run_id, cycle, len(fixed_code), exp_dir,
        )

        # 5. Re-run experiment in sandbox
        sandbox_result = _run_experiment_in_sandbox(
            exp_dir, config, repair_dir,
            timeout_sec=repair_cfg.timeout_sec_per_cycle,
        )

        if sandbox_result is None:
            cycle_result = RepairCycleResult(
                cycle=cycle, diagnosis=diag,
                repair_applied=True,
                repair_description=f"Fixed {len(fixed_code)} files",
                error="Sandbox execution failed",
            )
            cycle_history.append(cycle_result)
            logger.warning("[%s] Repair cycle %d: sandbox execution failed", run_id, cycle)
            continue

        # 6. Build new experiment summary from sandbox results
        new_summary = _build_experiment_summary_from_run(sandbox_result, fixed_code)
        (repair_dir / "experiment_summary.json").write_text(
            json.dumps(new_summary, indent=2), encoding="utf-8"
        )

        # 7. Re-assess quality
        new_qa = assess_experiment_quality(new_summary, min_conditions=_min_cond)
        new_score = _summary_quality_score(new_summary)
        old_score = _summary_quality_score(best_summary)

        cycle_result = RepairCycleResult(
            cycle=cycle,
            diagnosis=diag,
            repair_applied=True,
            repair_description=(
                f"Fixed {len(fixed_code)} files; "
                f"score {old_score:.1f} → {new_score:.1f}; "
                f"mode: {new_qa.mode.value}"
            ),
            new_assessment=new_qa,
        )
        cycle_history.append(cycle_result)

        # Track best
        if new_score > _summary_quality_score(best_summary):
            best_summary = new_summary
            best_mode = new_qa.mode
            best_updated = True

        logger.info(
            "[%s] Repair cycle %d: score %.1f → %.1f, mode=%s, sufficient=%s",
            run_id, cycle, old_score, new_score, new_qa.mode.value, new_qa.sufficient,
        )
        _report_progress(
            progress_reporter,
            f"[{run_id}] Repair cycle {cycle}: "
            f"score {old_score:.1f} → {new_score:.1f}, "
            f"mode={new_qa.mode.value}",
        )

        if new_qa.sufficient:
            logger.info("[%s] Repair successful after %d cycles!", run_id, cycle)
            _report_progress(
                progress_reporter,
                f"[{run_id}] Experiment repair successful! Mode: {new_qa.mode.value}",
            )
            return ExperimentRepairResult(
                success=True,
                total_cycles=cycle,
                final_mode=new_qa.mode,
                final_assessment=new_qa,
                cycle_history=cycle_history,
                best_experiment_summary=best_summary,
            )

        # Update for next cycle
        code = fixed_code
        summary = new_summary
        stdout = sandbox_result.get("stdout", "")
        stderr = sandbox_result.get("stderr", "")

    # Exhausted all cycles — use best available
    elapsed = _time.monotonic() - loop_start
    logger.info(
        "[%s] Repair loop completed: %d cycles in %.1fs, best mode=%s",
        run_id, len(cycle_history), elapsed, best_mode.value,
    )

    # Promote best summary only if a repair cycle actually improved it
    if best_updated and best_summary is not summary:
        best_path = run_dir / "experiment_summary_best.json"
        best_path.write_text(json.dumps(best_summary, indent=2), encoding="utf-8")

    return ExperimentRepairResult(
        success=False,
        total_cycles=len(cycle_history),
        final_mode=best_mode,
        cycle_history=cycle_history,
        best_experiment_summary=best_summary,
    )


# ---------------------------------------------------------------------------
# Helper: load experiment artifacts
# ---------------------------------------------------------------------------


def _load_experiment_summary(run_dir: Path) -> dict | None:
    """Load the most recent experiment_summary.json."""
    for candidate in sorted(run_dir.glob("stage-14*/experiment_summary.json"), reverse=True):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _load_refinement_log(run_dir: Path) -> dict | None:
    """Load the most recent refinement_log.json."""
    for candidate in sorted(run_dir.glob("stage-13*/refinement_log.json"), reverse=True):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _load_experiment_code(run_dir: Path) -> dict[str, str]:
    """Load experiment code from the most recent stage directory.

    Prefers: stage-13/experiment_final/ → stage-10/experiment/ → stage-10/*.py
    """
    code: dict[str, str] = {}

    # Try refined code first
    for refine_dir in sorted(run_dir.glob("stage-13*/experiment_final"), reverse=True):
        if refine_dir.is_dir():
            for py_file in sorted(refine_dir.glob("*.py")):
                try:
                    code[py_file.name] = py_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug(
                        "Failed to read experiment code file %s: %s",
                        py_file,
                        exc,
                        exc_info=True,
                    )
            # Also grab requirements.txt, setup.py
            for extra in ("requirements.txt", "setup.py"):
                extra_path = refine_dir / extra
                if extra_path.exists():
                    try:
                        code[extra] = extra_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        logger.debug(
                            "Failed to read experiment support file %s: %s",
                            extra_path,
                            exc,
                            exc_info=True,
                        )
            if code:
                return code

    # Fall back to stage-10 experiment directory
    for exp_dir in sorted(run_dir.glob("stage-10*/experiment"), reverse=True):
        if exp_dir.is_dir():
            for py_file in sorted(exp_dir.glob("*.py")):
                try:
                    code[py_file.name] = py_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug(
                        "Failed to read experiment code file %s: %s",
                        py_file,
                        exc,
                        exc_info=True,
                    )
            for extra in ("requirements.txt", "setup.py"):
                extra_path = exp_dir / extra
                if extra_path.exists():
                    try:
                        code[extra] = extra_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        logger.debug(
                            "Failed to read experiment support file %s: %s",
                            extra_path,
                            exc,
                            exc_info=True,
                        )
            if code:
                return code

    # Last resort: any .py files in stage-10*
    for stage_dir in sorted(run_dir.glob("stage-10*"), reverse=True):
        for py_file in sorted(stage_dir.glob("*.py")):
            try:
                code[py_file.name] = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug(
                    "Failed to read experiment code file %s: %s",
                    py_file,
                    exc,
                    exc_info=True,
                )
        if code:
            return code

    return code


def _load_experiment_plan(run_dir: Path) -> dict | None:
    """Load experiment plan from stage-09."""
    for candidate in sorted(run_dir.glob("stage-09*/experiment_design.json"), reverse=True):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _collect_experiment_output(run_dir: Path) -> tuple[str, str]:
    """Collect stdout/stderr from experiment runs."""
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    for stage_dir in sorted(run_dir.glob("stage-14*")):
        runs_dir = stage_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_file in sorted(runs_dir.glob("*.json"))[:5]:
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    stdout_parts.append(data.get("stdout", ""))
                    stderr_parts.append(data.get("stderr", ""))
            except (json.JSONDecodeError, OSError):
                continue

    return "\n".join(stdout_parts).strip(), "\n".join(stderr_parts).strip()


# ---------------------------------------------------------------------------
# Helper: get repaired code from LLM or OpenCode
# ---------------------------------------------------------------------------


def _get_repaired_code(
    repair_prompt: str,
    current_code: dict[str, str],
    llm: Any,
    config: Any,
    run_dir: Path,
    cycle: int,
) -> dict[str, str] | None:
    """Get repaired code via OpenCode (if available) or LLM fallback.

    Returns merged code dict (current + repaired files) or None on failure.
    """
    repair_cfg = config.experiment.repair

    # Try OpenCode first if enabled
    if repair_cfg.use_opencode and config.experiment.opencode.enabled:
        result = _repair_via_opencode(repair_prompt, current_code, config, run_dir, cycle)
        if result:
            return result
        logger.info("OpenCode repair unavailable, falling back to LLM")

    # LLM repair
    return _repair_via_llm(repair_prompt, current_code, llm)


def _repair_via_opencode(
    repair_prompt: str,
    current_code: dict[str, str],
    config: Any,
    run_dir: Path,
    cycle: int,
) -> dict[str, str] | None:
    """Attempt repair via OpenCode agent."""
    try:
        from researchclaw.pipeline.opencode_bridge import OpenCodeBridge

        _oc_cfg = config.experiment.opencode
        bridge = OpenCodeBridge(
            model=getattr(_oc_cfg, "model", "") or "",
            llm_base_url=getattr(config.llm, "base_url", "") or "",
            api_key_env=getattr(config.llm, "api_key_env", "") or "",
            llm_provider=getattr(config.llm, "provider", "openai-compatible") or "openai-compatible",
            timeout_sec=getattr(_oc_cfg, "timeout_sec", 600),
            max_retries=getattr(_oc_cfg, "max_retries", 1),
            workspace_cleanup=getattr(_oc_cfg, "workspace_cleanup", True),
            forward_api_key_env=getattr(_oc_cfg, "forward_api_key_env", False),
        )
        workspace = run_dir / f"_repair_opencode_v{cycle}"
        workspace.mkdir(parents=True, exist_ok=True)

        result = bridge.generate(
            stage_dir=workspace,
            topic="experiment repair",
            exp_plan=repair_prompt,
            metric=getattr(config.experiment, "metric_key", "primary_metric"),
            time_budget_sec=getattr(config.experiment, "time_budget_sec", 2400),
        )

        if result.success and result.files:
            # Merge with current code
            merged = dict(current_code)
            merged.update(result.files)
            logger.info(
                "OpenCode repair: %d files generated (%d total after merge)",
                len(result.files), len(merged),
            )
            return merged

    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("OpenCode repair failed: %s", exc, exc_info=True)

    return None


def _repair_via_llm(
    repair_prompt: str,
    current_code: dict[str, str],
    llm: Any,
) -> dict[str, str] | None:
    """Repair experiment code via LLM chat."""
    system = (
        "You are an expert experiment repair assistant. "
        "Fix the experiment code based on the diagnosis below. "
        "Output ONLY the fixed files. For each file, use this exact format:\n\n"
        "```python filename.py\n"
        "<complete fixed code>\n"
        "```\n\n"
        "Include ALL files that need changes (main.py, requirements.txt, etc.). "
        "Output the COMPLETE file content, not just the changed parts."
    )

    try:
        resp = llm.chat(
            [{"role": "user", "content": repair_prompt}],
            system=system,
        )
        content = resp.content
    except (
        RuntimeError,
        OSError,
        TimeoutError,
        ValueError,
        TypeError,
        AttributeError,
        urllib.error.URLError,
    ) as exc:
        logger.warning("LLM repair call failed: %s", exc, exc_info=True)
        return None

    if not content or not content.strip():
        logger.warning("LLM repair returned empty response")
        return None

    # Extract code blocks from response
    files = _extract_code_blocks(content)

    if not files:
        logger.warning("LLM repair: no code blocks found in response")
        return None

    # Merge with current code (only update files that were fixed)
    merged = dict(current_code)
    merged.update(files)
    logger.info(
        "LLM repair: extracted %d files (%d total after merge)",
        len(files), len(merged),
    )
    return merged




# ---------------------------------------------------------------------------
# Helper: run experiment in sandbox
# ---------------------------------------------------------------------------

"""Stage 10: Code generation."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.experiment.validator import (
    format_issues_for_llm,
    validate_code,
)
from researchclaw.llm.client import LLMClient
from researchclaw.pipeline._helpers import (
    StageResult,
    _chat_with_prompt,
    _extract_code_block,
    _extract_multi_file_blocks,
    _get_evolution_overlay,
    _load_hardware_profile,
    _read_prior_artifact,
    _safe_json_loads,
    _utcnow_iso,
)
from researchclaw.pipeline.stages import Stage, StageStatus
from researchclaw.pipeline.stage_impls._code_generation_dispatch import _run_code_generation_dispatch
from researchclaw.pipeline.stage_impls._code_generation_guidance import _build_generation_guidance
from researchclaw.pipeline.stage_impls._code_generation_review import _run_review_alignment_checks
from researchclaw.prompts import PromptManager

logger = logging.getLogger(__name__)

# Improvement G: Continuous-action environments that are incompatible with DQN
_CONTINUOUS_ENVS = {
    "pendulum", "halfcheetah", "hopper", "walker2d", "ant", "humanoid",
    "swimmer", "reacher", "invertedpendulum", "inverteddoublependulum",
    "mountaincarcontinuous", "lunarlander-continuous",
}


def _check_rl_compatibility(code: str) -> list[str]:
    """Detect DQN + continuous-action environment mismatches.

    Returns a list of error strings if incompatible combinations are found.
    """
    errors: list[str] = []
    code_lower = code.lower()
    has_dqn = "dqn" in code_lower
    if not has_dqn:
        return errors

    for env_name in _CONTINUOUS_ENVS:
        if env_name in code_lower:
            errors.append(
                f"RL COMPATIBILITY ERROR: DQN is used with continuous-action "
                f"environment '{env_name}'. DQN only works with DISCRETE action "
                f"spaces. Use SAC, TD3, or PPO instead."
            )
    return errors


def _execute_code_generation(
    stage_dir: Path,
    run_dir: Path,
    config: RCConfig,
    adapters: AdapterBundle,
    *,
    llm: LLMClient | None = None,
    prompts: PromptManager | None = None,
) -> StageResult:
    exp_plan = _read_prior_artifact(run_dir, "exp_plan.yaml") or ""
    metric = config.experiment.metric_key
    max_repair = 5  # BUG-14: Increased from 3 to give more chances for critical bugs
    files: dict[str, str] = {}
    validation_log: list[str] = []

    # --- Detect available packages for sandbox ---
    _pm = prompts or PromptManager()

    pkg_hint, compute_budget, extra_guidance = _build_generation_guidance(
        run_dir, config, exp_plan, _pm
    )

    dispatch = _run_code_generation_dispatch(
        stage_dir,
        run_dir,
        config,
        adapters,
        llm,
        _pm,
        exp_plan,
        metric,
        pkg_hint,
        compute_budget,
        extra_guidance,
    )
    files = dispatch.files
    _code_agent_active = dispatch.code_agent_active
    _code_max_tokens = dispatch.code_max_tokens
    # --- Fallback: generic numerical experiment ---
    if not files:
        files = {
            "main.py": (
                "import numpy as np\n"
                "\n"
                "np.random.seed(42)\n"
                "\n"
                "# Fallback experiment: parameter sweep on a synthetic objective\n"
                "# This runs when LLM code generation fails to produce valid code.\n"
                "dim = 10\n"
                "n_conditions = 3\n"
                "results = {}\n"
                "\n"
                "for cond_idx in range(n_conditions):\n"
                "    cond_name = f'condition_{cond_idx}'\n"
                "    scores = []\n"
                "    for seed in range(3):\n"
                "        rng = np.random.RandomState(seed + cond_idx * 100)\n"
                "        x = rng.randn(dim)\n"
                "        score = float(1.0 / (1.0 + np.sum(x ** 2)))\n"
                "        scores.append(score)\n"
                "    mean_score = float(np.mean(scores))\n"
                "    results[cond_name] = mean_score\n"
                f"    print(f'condition={{cond_name}} {metric}: {{mean_score:.6f}}')\n"
                "\n"
                "best = max(results, key=results.get)\n"
                f"print(f'{metric}: {{results[best]:.6f}}')\n"
            )
        }

    # --- Validate each file + auto-repair loop ---
    all_valid = True
    attempt = 0
    for fname, code in list(files.items()):
        # Skip non-Python files (requirements.txt, setup.py, etc.)
        if not fname.endswith(".py"):
            continue
        validation = validate_code(code)
        repair_attempt = 0
        while not validation.ok and llm is not None and repair_attempt < max_repair:
            repair_attempt += 1
            attempt += 1
            # Only send errors to the LLM — warnings don't block validation
            # and confuse the LLM into over-correcting (e.g. removing runtime imports)
            errors_only = type(validation)(
                issues=[i for i in validation.issues if i.severity == "error"]
            )
            issues_text = format_issues_for_llm(errors_only)
            validation_log.append(
                f"File {fname} attempt {repair_attempt}: {validation.summary()}"
            )
            logger.info(
                "Code validation failed for %s (attempt %d/%d): %s",
                fname,
                repair_attempt,
                max_repair,
                validation.summary(),
            )
            all_files_ctx = "\n\n".join(
                f"```filename:{f}\n{c}\n```" for f, c in files.items()
            )
            rp = _pm.sub_prompt(
                "code_repair",
                fname=fname,
                issues_text=issues_text,
                all_files_ctx=all_files_ctx,
            )
            resp = _chat_with_prompt(llm, rp.system, rp.user)
            _repaired = _extract_code_block(resp.content)
            if _repaired.strip():
                files[fname] = _repaired
            else:
                logger.warning("Repair attempt returned empty code, keeping original")
            validation = validate_code(files[fname])
        if not validation.ok:
            all_valid = False
            # BUG-14: Log remaining issues prominently
            logger.warning(
                "Code validation FAILED for %s after %d repair attempts: %s",
                fname, max_repair, validation.summary(),
            )

    # Improvement G: RL algorithm-environment compatibility check
    for fname, code in list(files.items()):
        if not fname.endswith(".py"):
            continue
        _rl_errors = _check_rl_compatibility(code)
        if _rl_errors:
            for _rl_err in _rl_errors:
                logger.error("Stage 10: %s (in %s)", _rl_err, fname)
                validation_log.append(f"RL_COMPAT: {fname}: {_rl_err}")
            all_valid = False

    # BUG-14: Block on critical validation failures (syntax/import errors)
    if not all_valid:
        _has_critical = False
        for fname, code in files.items():
            _v = validate_code(code)
            if not _v.ok:
                for issue in _v.issues:
                    if issue.severity == "error" and issue.category in (
                        "syntax", "import",
                    ):
                        _has_critical = True
        if _has_critical:
            logger.error(
                "Stage 10: CRITICAL validation issues remain after %d repair "
                "attempts. Blocking stage.", max_repair,
            )
            (stage_dir / "validation_report.md").write_text(
                "# Code Validation Report\n\n"
                f"**Status**: BLOCKED — critical issues remain after {max_repair} repairs\n\n"
                + "\n".join(f"- {e}" for e in validation_log),
                encoding="utf-8",
            )
            return StageResult(
                stage=Stage.CODE_GENERATION,
                status=StageStatus.FAILED,
                artifacts=("validation_report.md",),
                evidence_refs=(),
            )

    # --- BUG-184: Cross-import validation — warn if a .py file imports a
    # local module that doesn't exist in the files dict.  This catches the
    # case where Beast Mode/CodeAgent produced an intermediate file that
    # got lost during repair iterations.
    _known_modules = {
        f.replace(".py", "") for f in files if f.endswith(".py")
    }
    _stdlib_and_common = {
        "os", "sys", "json", "math", "time", "copy", "re", "random",
        "pathlib", "argparse", "logging", "collections", "functools",
        "itertools", "abc", "typing", "dataclasses", "enum", "io",
        "csv", "pickle", "glob", "shutil", "subprocess", "datetime",
        "numpy", "np", "torch", "torchvision", "gymnasium", "gym",
        "sklearn", "scipy", "pandas", "matplotlib", "PIL", "tqdm",
        "einops", "timm", "transformers", "datasets", "peft",
        "stable_baselines3",
    }
    for fname, code in list(files.items()):
        if not fname.endswith(".py"):
            continue
        for _m in re.findall(
            r"^(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            code, re.MULTILINE,
        ):
            if (_m not in _known_modules
                    and _m not in _stdlib_and_common
                    and not _m.startswith("_")):
                logger.warning(
                    "BUG-184: %s imports '%s' which is not in generated "
                    "files — experiment may crash on import",
                    fname, _m,
                )

    # --- Write experiment directory ---
    exp_dir = stage_dir / "experiment"
    exp_dir.mkdir(parents=True, exist_ok=True)
    for fname, code in files.items():
        (exp_dir / fname).write_text(code, encoding="utf-8")

    # --- Write validation report ---
    if validation_log or not all_valid:
        report_lines = ["# Code Validation Report\n"]
        if all_valid:
            report_lines.append(f"**Status**: PASSED after {attempt} total repair(s)\n")
        else:
            report_lines.append(
                f"**Status**: FAILED after {attempt} total repair attempt(s)\n"
            )
        for entry in validation_log:
            report_lines.append(f"- {entry}")
        (stage_dir / "validation_report.md").write_text(
            "\n".join(report_lines), encoding="utf-8"
        )

    # --- R10-Fix6: Code complexity and quality check ---
    from researchclaw.experiment.validator import (
        auto_fix_unbound_locals,
        check_code_complexity,
        deep_validate_files,
    )

    # --- BUG-3 fix: Programmatic auto-fix for UnboundLocalError patterns ---
    _total_ub_fixes = 0
    for fname, code in list(files.items()):
        if fname.endswith(".py"):
            fixed_code, n_fixes = auto_fix_unbound_locals(code)
            if n_fixes > 0:
                files[fname] = fixed_code
                (exp_dir / fname).write_text(fixed_code, encoding="utf-8")
                _total_ub_fixes += n_fixes
                logger.info(
                    "Stage 10: auto-fixed %d UnboundLocalError risk(s) in %s",
                    n_fixes, fname,
                )
    if _total_ub_fixes:
        logger.info(
            "Stage 10: auto-fixed %d total UnboundLocalError risks", _total_ub_fixes
        )

    complexity_warnings: list[str] = []
    for fname, code in files.items():
        if fname.endswith(".py"):
            cw = check_code_complexity(code)
            for w in cw:
                complexity_warnings.append(f"[{fname}] {w}")
                logger.warning("Stage 10 code quality: [%s] %s", fname, w)

    # --- P1.1+P1.2: Deep quality analysis (class quality, scoping, API) ---
    deep_warnings = deep_validate_files(files)
    for w in deep_warnings:
        logger.warning("Stage 10 deep quality: %s", w)
    complexity_warnings.extend(deep_warnings)

    # --- P1.2: If critical deep issues found, attempt one repair cycle ---
    critical_deep = [w for w in deep_warnings if any(
        kw in w for kw in ("UnboundLocalError", "unregistered", "does not exist",
                           "empty or trivial subclass", "does NOT override",
                           "Import-usage mismatch", "NameError",
                           "was removed", "ptp()",
                           "copy-paste", "identical method signatures",
                           "identical AST", "NOT a real ablation",
                           "shadows stdlib/pip")
    )]
    if critical_deep and llm is not None:
        logger.info(
            "Stage 10: %d critical code issues found — triggering repair cycle",
            len(critical_deep),
        )
        repair_issues = "\n".join(f"- {w}" for w in critical_deep)
        all_code_ctx = "\n\n".join(
            f"```filename:{f}\n{c}\n```" for f, c in files.items()
        )
        repair_prompt = (
            f"CRITICAL CODE QUALITY ISSUES FOUND:\n{repair_issues}\n\n"
            f"Fix ALL these issues in the code below. Return the complete "
            f"corrected files using ```filename:xxx.py format.\n\n"
            f"RULES:\n"
            f"- nn.Linear/nn.Conv must be created in __init__(), not forward()\n"
            f"- Variables used after if/else must be defined before the branch\n"
            f"- Use scipy.special.erf, not np.erf\n"
            f"- Ablation/variant classes must have genuinely different logic\n"
            f"- Every class must have a real implementation, not just `pass`\n"
            f"- Ablation classes MUST override the parent method that implements "
            f"the component being ablated (e.g., if ablating attention, override "
            f"the attention method with a simpler alternative like mean pooling)\n"
            f"- IMPORT CONSISTENCY: if you write `from X import Y`, call `Y()` "
            f"directly — NOT `X.Y()`. Mixing styles causes NameError.\n"
            f"- NumPy 2.0: ndarray.ptp() was removed — use arr.max()-arr.min()\n"
            f"- NumPy 2.0: np.bool/np.int/np.float removed — use builtins\n"
            f"- Pretrained models (EfficientNet, ResNet, ViT) expect 224×224 input "
            f"— add `transforms.Resize(224)` when using CIFAR (32×32) or similar\n"
            f"- Copy-paste ablation: if two classes have identical bodies, REWRITE "
            f"the ablation to genuinely remove/reduce a component (e.g., zero out "
            f"attention weights, halve hidden dimensions, remove a loss term)\n"
            f"- KD: teacher must be frozen, add projection layers if teacher_dim != "
            f"student_dim, use temperature T=4 for soft targets\n"
            f"- FILENAME COLLISIONS: If a file like config.py shadows a pip/stdlib "
            f"package, rename it (e.g., config.py → experiment_config.py) and update "
            f"ALL imports referencing it\n\n"
            f"Current code:\n{all_code_ctx}\n"
        )
        try:
            repair_resp = _chat_with_prompt(
                llm,
                _pm.system("code_generation"),
                repair_prompt,
                max_tokens=_code_max_tokens,
            )
            repaired = _extract_multi_file_blocks(repair_resp.content)
            if repaired and "main.py" in repaired:
                files = repaired
                for fname, code in files.items():
                    (exp_dir / fname).write_text(code, encoding="utf-8")
                # Re-check after repair
                deep_warnings_after = deep_validate_files(files)
                fixed = len(critical_deep) - len([
                    w for w in deep_warnings_after
                    if any(kw in w for kw in (
                        "UnboundLocalError", "unregistered", "does not exist",
                        "empty or trivial subclass", "does NOT override",
                        "Import-usage mismatch", "NameError",
                        "was removed", "ptp()",
                        "copy-paste", "identical method signatures",
                        "identical AST", "NOT a real ablation",
                        "shadows stdlib/pip",
                    ))
                ])
                logger.info(
                    "Stage 10: Deep repair fixed %d/%d critical issues",
                    fixed, len(critical_deep),
                )
                complexity_warnings.append(
                    f"[REPAIR] Deep repair fixed {fixed}/{len(critical_deep)} "
                    f"critical issues"
                )
        except (RuntimeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            logger.debug("Deep repair failed: %s", exc)

    if complexity_warnings:
        health: dict[str, Any] = {}
        health["code_complexity_warnings"] = complexity_warnings
        (stage_dir / "code_complexity.json").write_text(
            json.dumps(health, indent=2), encoding="utf-8"
        )

    review_result = _run_review_alignment_checks(
        stage_dir,
        exp_dir,
        config,
        llm,
        _pm,
        exp_plan,
        files,
        pkg_hint,
        compute_budget,
        _code_max_tokens,
        _code_agent_active,
    )
    files = review_result.files
    alignment_ok = review_result.alignment_ok
    alignment_note = review_result.alignment_note
    # --- Write spec ---
    file_list = ", ".join(f"`{f}`" for f in sorted(files.keys()))
    main_validation = validate_code(files.get("main.py", ""))
    _align_status = "ALIGNED" if alignment_ok else f"MISALIGNED: {alignment_note}"
    spec = f"""# Experiment Specification

## Topic
{config.research.topic}

## Project Structure
Multi-file experiment project with {len(files)} file(s): {file_list}

## Entry Point
`main.py` \u2014 executed directly via sandbox

## Outputs
- `main.py` emits metric lines in `name: value` format
- Primary metric key: `{metric}`

## Topic-Experiment Alignment
{_align_status}

## Constraints
- Time budget per run: {config.experiment.time_budget_sec}s
- Max iterations: {config.experiment.max_iterations}
- Self-contained execution (no external data, no network)
- Validated: {main_validation.summary()}

## Generated
{_utcnow_iso()}
"""
    (stage_dir / "experiment_spec.md").write_text(spec, encoding="utf-8")

    artifacts = ["experiment/", "experiment_spec.md"]
    if (stage_dir / "validation_report.md").exists():
        artifacts.append("validation_report.md")

    # BUG-R6-01: Fail stage if alignment check detected persistent mismatch
    # after all regen attempts, instead of silently proceeding.
    if not alignment_ok:
        logger.error(
            "Stage 10: Persistent topic-experiment misalignment after all "
            "regen attempts. Failing stage. Reason: %s",
            alignment_note,
        )
        return StageResult(
            stage=Stage.CODE_GENERATION,
            status=StageStatus.FAILED,
            artifacts=tuple(artifacts),
            evidence_refs=tuple(f"stage-10/{a}" for a in artifacts),
            error=f"Topic-experiment misalignment: {alignment_note}",
        )

    return StageResult(
        stage=Stage.CODE_GENERATION,
        status=StageStatus.DONE,
        artifacts=tuple(artifacts),
        evidence_refs=tuple(f"stage-10/{a}" for a in artifacts),
    )


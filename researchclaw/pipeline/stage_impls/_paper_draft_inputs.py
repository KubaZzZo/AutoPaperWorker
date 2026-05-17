"""Prompt input assembly for Stage 17 paper drafts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from pathlib import Path
from typing import Any

from researchclaw.config import RCConfig
from researchclaw.pipeline._helpers import _read_prior_artifact, _safe_json_loads
from researchclaw.pipeline.stage_impls._paper_metrics import (
    _check_ablation_effectiveness,
    _collect_raw_experiment_metrics,
    _detect_result_contradictions,
)
from researchclaw.pipeline.stage_impls._paper_outline import _topic_is_literature_first

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperDraftInputs:
    exp_summary_text: str | None
    exp_metrics_instruction: str
    has_real_metrics: bool
    raw_metrics_block: str
    has_parsed_metrics: bool
    is_literature_first: bool
    verified_registry: Any | None


def _build_initial_draft_inputs(run_dir: Path, config: RCConfig) -> PaperDraftInputs:
    # BUG-222: Read PROMOTED BEST experiment_summary for the paper prompt.
    # Previous code (R21-1) picked the "richest" experiment_summary across
    # all stage-14* dirs.  After REFINE regression, a later iteration with
    # more conditions but worse quality could win, feeding the LLM regressed
    # data.  Now: prefer experiment_summary_best.json (written by
    # _promote_best_stage14()), fall back to richest stage-14* for
    # non-REFINE runs.
    exp_summary_text = None
    _best_path = run_dir / "experiment_summary_best.json"
    if _best_path.is_file():
        try:
            _text = _best_path.read_text(encoding="utf-8")
            _parsed = _safe_json_loads(_text, {})
            if isinstance(_parsed, dict) and (
                _parsed.get("condition_summaries") or _parsed.get("metrics_summary")
            ):
                exp_summary_text = _text
                logger.info("BUG-222: Using promoted experiment_summary_best.json")
        except OSError:
            logger.debug("Stage 17: Failed to read promoted experiment_summary_best.json", exc_info=True)
    if exp_summary_text is None:
        # Fallback: pick richest stage-14* (pre-BUG-222 behavior)
        _best_metric_count = 0
        for _s14_dir in sorted(run_dir.glob("stage-14*")):
            _candidate = _s14_dir / "experiment_summary.json"
            if _candidate.is_file():
                _text = _candidate.read_text(encoding="utf-8")
                _parsed = _safe_json_loads(_text, {})
                if isinstance(_parsed, dict):
                    _mcount = _parsed.get("total_metric_keys", 0) or len(
                        _parsed.get("metrics_summary", {})
                    )
                    _paired_count = len(_parsed.get("paired_comparisons", []))
                    _cond_count = len(_parsed.get("condition_summaries", {}))
                    _score = _mcount + _paired_count * 10 + _cond_count * 5
                    if _score > _best_metric_count:
                        _best_metric_count = _score
                        exp_summary_text = _text
                        logger.info(
                            "R21-1 fallback: Selected %s (score=%d)",
                            _s14_dir.name, _score,
                        )
        if exp_summary_text is None:
            exp_summary_text = _read_prior_artifact(run_dir, "experiment_summary.json")
    exp_metrics_instruction = ""
    has_real_metrics = False
    _verified_registry = None  # Phase 1: anti-fabrication verified data registry
    # BUG-108: Load refinement_log so VerifiedRegistry has per-iteration metrics
    _refinement_log_for_vr: dict | None = None
    _rl_candidates = sorted(run_dir.glob("stage-13*/refinement_log.json"), reverse=True)
    _rl_path = _rl_candidates[0] if _rl_candidates else None
    if _rl_path and _rl_path.is_file():
        try:
            _refinement_log_for_vr = json.loads(_rl_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.debug("Stage 17: Failed to parse refinement log for VerifiedRegistry: %s", _rl_path, exc_info=True)
    if exp_summary_text:
        exp_summary = _safe_json_loads(exp_summary_text, {})
        # Phase 1: Build VerifiedRegistry from experiment data
        if isinstance(exp_summary, dict):
            try:
                from researchclaw.pipeline.verified_registry import VerifiedRegistry
                # BUG-222: Use best_only=True to ensure paper tables reflect
                # only the promoted best iteration, not regressed data
                _verified_registry = VerifiedRegistry.from_run_dir(
                    run_dir,
                    metric_direction=config.experiment.metric_direction,
                    best_only=True,
                )
                logger.info(
                    "Stage 17: VerifiedRegistry — %d verified values, %d conditions",
                    len(_verified_registry.values),
                    len(_verified_registry.condition_names),
                )
            except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as _vr_exc:
                logger.warning("Stage 17: Failed to build VerifiedRegistry: %s", _vr_exc, exc_info=True)
        if isinstance(exp_summary, dict) and exp_summary.get("metrics_summary"):
            has_real_metrics = True
            exp_metrics_instruction = (
                "\n\nIMPORTANT: Use the ACTUAL experiment results provided in the context. "
                "All numbers in the Results and Experiments sections MUST reference real data. "
                "Do NOT write 'no quantitative results yet' or use placeholder numbers. "
                "Cite specific metrics with their actual values.\n"
            )

    # Collect raw experiment stdout metrics as hard constraint for the paper
    raw_metrics_block, _has_parsed_metrics = _collect_raw_experiment_metrics(run_dir)
    if raw_metrics_block:
        # BUG-23: Raw stdout alone is not sufficient — require either
        # metrics_summary data, parsed metrics from run JSONs,
        # OR at least 3 condition= patterns in raw block
        _has_condition_pattern = len(re.findall(
            r"condition[=:]", raw_metrics_block, re.IGNORECASE
        )) >= 3
        if has_real_metrics or _has_parsed_metrics or _has_condition_pattern:
            has_real_metrics = True
        exp_metrics_instruction += raw_metrics_block

    # R18-1 + R19-6: Inject paired statistical comparisons AND condition summaries
    if exp_summary_text:
        exp_summary_parsed = _safe_json_loads(exp_summary_text, {})
        if isinstance(exp_summary_parsed, dict):
            # R19-6: Inject experiment scale header so LLM knows the data richness
            _total_conds = exp_summary_parsed.get("total_conditions")
            _total_mkeys = exp_summary_parsed.get("total_metric_keys")
            if _total_conds or _total_mkeys:
                scale_block = "\n\n## EXPERIMENT SCALE\n"
                if _total_conds:
                    scale_block += f"- Total conditions tested: {_total_conds}\n"
                if _total_mkeys:
                    scale_block += f"- Total metric keys collected: {_total_mkeys}\n"
                scale_block += (
                    "- This is a MULTI-SEED experiment. Report mean +/- std across seeds.\n"
                    "- Do NOT describe results as 'single run' or 'preliminary'.\n"
                )
                exp_metrics_instruction += scale_block

            # Improvement B: Inject seed insufficiency warnings
            _seed_warns = exp_summary_parsed.get("seed_insufficiency_warnings", [])
            if _seed_warns:
                _sw_block = (
                    "\n\n## SEED INSUFFICIENCY WARNINGS\n"
                    "Some conditions were run with fewer than 3 seeds. "
                    "Results for these conditions MUST be footnoted as preliminary.\n"
                    "All tables MUST show mean ± std format. Single-run values "
                    "MUST be footnoted with '†single seed — interpret with caution'.\n"
                )
                for _sw in _seed_warns:
                    _sw_block += f"- {_sw}\n"
                exp_metrics_instruction += _sw_block

            # R19-6 + R33: Inject condition summaries with CIs
            cond_summaries = exp_summary_parsed.get("condition_summaries", {})
            if isinstance(cond_summaries, dict) and cond_summaries:
                cond_block = "\n\n## PER-CONDITION SUMMARY (use in Results tables)\n"
                for cname, cdata in sorted(cond_summaries.items()):
                    cond_block += f"\n### {cname}\n"
                    if not isinstance(cdata, dict):
                        continue
                    sr = cdata.get("success_rate")
                    if sr is not None:
                        try:
                            cond_block += f"- Success rate: {float(sr):.1%}\n"
                        except (ValueError, TypeError):
                            cond_block += f"- Success rate: {sr}\n"
                    ns = cdata.get("n_seeds") or cdata.get("n_seed_metrics")
                    if ns:
                        cond_block += f"- Seeds: {ns}\n"
                    ci_lo = cdata.get("ci95_low")
                    ci_hi = cdata.get("ci95_high")
                    if ci_lo is not None and ci_hi is not None:
                        try:
                            cond_block += f"- Bootstrap 95% CI: [{float(ci_lo):.4f}, {float(ci_hi):.4f}]\n"
                        except (ValueError, TypeError):
                            cond_block += f"- Bootstrap 95% CI: [{ci_lo}, {ci_hi}]\n"
                    cm = cdata.get("metrics") or {}
                    if isinstance(cm, dict) and cm:
                        for mk, mv in sorted(cm.items()):
                            if isinstance(mv, (int, float)):
                                cond_block += f"- {mk}: {mv:.4f}\n"
                            else:
                                cond_block += f"- {mk}: {mv}\n"
                exp_metrics_instruction += cond_block

            # R18-1: Inject paired statistical comparisons
            paired = exp_summary_parsed.get("paired_comparisons", [])
            if paired:
                paired_block = "\n\n## PAIRED STATISTICAL COMPARISONS (use these in Results)\n"
                paired_block += f"Total: {len(paired)} paired tests computed.\n"
                for pc in paired:
                    if not isinstance(pc, dict):
                        continue
                    method = pc.get("method", "?")
                    baseline = pc.get("baseline", "?")
                    regime = pc.get("regime", "all")
                    md = pc.get("mean_diff", "?")
                    sd = pc.get("std_diff", "?")
                    ts = pc.get("t_stat", "?")
                    pv = pc.get("p_value", "?")
                    ci_lo = pc.get("ci95_low")
                    ci_hi = pc.get("ci95_high")
                    ci_str = ""
                    if ci_lo is not None and ci_hi is not None:
                        try:
                            ci_str = f", 95% CI [{float(ci_lo):.3f}, {float(ci_hi):.3f}]"
                        except (ValueError, TypeError):
                            ci_str = f", 95% CI [{ci_lo}, {ci_hi}]"
                    paired_block += (
                        f"- {method} vs {baseline} (regime={regime}): "
                        f"mean_diff={md}, std_diff={sd}, "
                        f"t={ts}, p={pv}{ci_str}\n"
                    )
                exp_metrics_instruction += paired_block

            # R24: Method naming map — translate generic condition labels
            _cond_names = list(cond_summaries.keys()) if isinstance(cond_summaries, dict) and cond_summaries else []
            if _cond_names:
                naming_block = (
                    "\n\n## METHOD NAMING (CRITICAL — do NOT use generic labels in the paper)\n"
                    "The condition labels below come from the experiment code. In the paper, "
                    "you MUST use DESCRIPTIVE algorithm names, not generic labels.\n"
                    "- If a condition name is already descriptive (e.g., 'random_search', "
                    "'bayesian_optimization', 'ppo_policy'), use it directly as a proper name.\n"
                    "- If a condition name is generic (e.g., 'baseline_1', 'method_variant_1'), "
                    "you MUST infer the algorithm from the experiment code/context and use the "
                    "real algorithm name (e.g., 'Random Search', 'Bayesian Optimization', "
                    "'PPO', 'Curiosity-Driven RL').\n"
                    "- NEVER write `baseline_1` or `method_variant_1` in the paper text.\n"
                    f"- Conditions to name: {_cond_names}\n"
                )
                exp_metrics_instruction += naming_block

            # IMP-8: Inject broken ablation warnings
            abl_warnings = exp_summary_parsed.get("ablation_warnings", [])
            if abl_warnings:
                broken_block = (
                    "\n\n## BROKEN ABLATIONS (DO NOT discuss as valid results)\n"
                    "The following ablation conditions produced IDENTICAL outputs, "
                    "indicating implementation bugs. Do NOT present their differences "
                    "as findings. Mention them ONLY in a 'Limitations' sub-section "
                    "as known implementation issues:\n"
                )
                for _aw in abl_warnings:
                    broken_block += f"- {_aw}\n"
                broken_block += (
                    "\nIf you reference these conditions, state explicitly: "
                    "'Due to an implementation defect, conditions X and Y produced "
                    "identical outputs; their comparison is therefore uninformative.'\n"
                )
                exp_metrics_instruction += broken_block

            # R25: Statistical table format requirement
            if paired:
                stat_table_block = (
                    "\n\n## STATISTICAL TABLE REQUIREMENT (MANDATORY in Results section)\n"
                    "The Results section MUST include a statistical comparison table with columns:\n"
                    "| Comparison | Mean Diff | Std Diff | t-statistic | p-value | Significance |\n"
                    "Use the PAIRED STATISTICAL COMPARISONS data above to fill this table.\n"
                    "Mark significance: *** (p<0.001), ** (p<0.01), * (p<0.05), n.s.\n"
                    "This is non-negotiable — a top-venue paper MUST have statistical tests.\n"
                )
                exp_metrics_instruction += stat_table_block

            # R26: Metric definition requirement
            exp_metrics_instruction += (
                "\n\n## METRIC DEFINITIONS (MANDATORY in Experiments section)\n"
                "The Experiments section MUST define each metric:\n"
                "- **Primary metric**: what it measures, how it is computed, range, direction "
                "(higher/lower is better), and units if applicable.\n"
                "- **Secondary metric**: same details.\n"
                "- For time-to-event metrics: explain the horizon, what constitutes success, "
                "and how failures are handled (e.g., set to max horizon).\n"
                "- These definitions MUST appear BEFORE any results tables.\n"
            )

            # R27: Multi-seed framing enforcement
            _any_seeds = any(
                (cond_summaries.get(c) or {}).get("n_seed_metrics", 0) > 1
                for c in _cond_names
            ) if _cond_names else False
            if _any_seeds:
                exp_metrics_instruction += (
                    "\n\n## MULTI-SEED EXPERIMENT FRAMING (CRITICAL)\n"
                    "This experiment uses MULTIPLE independent random seeds per condition.\n"
                    "- Report mean +/- std (or SE) for all metrics.\n"
                    "- NEVER describe this as 'a single run' or '1 benchmark-artifact run'.\n"
                    "- Frame as: 'We evaluate each method across N seeds per regime.'\n"
                    "- The seed-level data IS the evidence base — it is NOT a single observation.\n"
                    "- Include per-regime breakdowns (easy vs hard) as separate rows in tables.\n"
                )

    # BUG-003: Inject actual evaluated datasets as a hard constraint
    if exp_summary_text:
        _ds_parsed = _safe_json_loads(exp_summary_text, {})
        if isinstance(_ds_parsed, dict):
            _datasets: set[str] = set()
            # Extract from condition names (often contain dataset info)
            for _cname in (_ds_parsed.get("condition_summaries") or {}).keys():
                _datasets.add(str(_cname))
            # Extract from explicit "datasets" field if present
            for _ds in (_ds_parsed.get("datasets") or []):
                if isinstance(_ds, str):
                    _datasets.add(_ds)
            # Extract from "benchmark" or "dataset" fields
            for _key in ("benchmark", "dataset", "dataset_name"):
                _dv = _ds_parsed.get(_key)
                if isinstance(_dv, str) and _dv:
                    _datasets.add(_dv)
            if _datasets:
                exp_metrics_instruction += (
                    "\n\n## ACTUAL EVALUATED DATASETS (HARD CONSTRAINT)\n"
                    "The following datasets/conditions were ACTUALLY tested in experiments:\n"
                    + "".join(f"- {d}\n" for d in sorted(_datasets))
                    + "\nCRITICAL: Do NOT claim evaluation on any dataset not listed above.\n"
                    "Do NOT fabricate results for datasets you did not run experiments on.\n"
                    "If you reference other datasets, clearly state they are 'not evaluated "
                    "in this work' or are 'left for future work'.\n"
                )

    # P7: Ablation effectiveness check
    if exp_summary_text:
        _exp_parsed_p7 = _safe_json_loads(exp_summary_text, {})
        if isinstance(_exp_parsed_p7, dict):
            _abl_warnings = _check_ablation_effectiveness(_exp_parsed_p7)
            if _abl_warnings:
                _abl_block = (
                    "\n\n## ABLATION EFFECTIVENESS WARNINGS\n"
                    "The following ablations showed minimal effect (within 5% of baseline). "
                    "Discuss this honestly — it may indicate the ablated component is not "
                    "important, or the ablation was not properly implemented:\n"
                )
                for _aw in _abl_warnings:
                    _abl_block += f"- {_aw}\n"
                exp_metrics_instruction += _abl_block
                logger.warning("P7: Ablation effectiveness warnings: %s", _abl_warnings)

    # P10: Contradiction detection
    if exp_summary_text:
        _exp_parsed_p10 = _safe_json_loads(exp_summary_text, {})
        if isinstance(_exp_parsed_p10, dict):
            _contradictions = _detect_result_contradictions(
                _exp_parsed_p10, metric_direction=config.experiment.metric_direction
            )
            if _contradictions:
                _contra_block = (
                    "\n\n## RESULT INTERPRETATION ADVISORIES (CRITICAL — read before writing)\n"
                )
                for _ca in _contradictions:
                    _contra_block += f"- {_ca}\n"
                exp_metrics_instruction += _contra_block
                logger.warning("P10: Contradiction advisories: %s", _contradictions)

    # R10: HARD BLOCK — refuse to write paper when all data is simulated
    # (skipped for literature-first / survey topics)
    _is_lit_first = _topic_is_literature_first(config)
    return PaperDraftInputs(
        exp_summary_text=exp_summary_text,
        exp_metrics_instruction=exp_metrics_instruction,
        has_real_metrics=has_real_metrics,
        raw_metrics_block=raw_metrics_block,
        has_parsed_metrics=_has_parsed_metrics,
        is_literature_first=_is_lit_first,
        verified_registry=_verified_registry,
    )

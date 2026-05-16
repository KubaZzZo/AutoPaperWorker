"""Focused tests for Stage 17 paper-writing helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchclaw.pipeline.stage_impls import _paper_writing
from researchclaw.workbench.run import default_workbench_config


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_topic_is_literature_first_for_review_terms() -> None:
    assert _paper_writing._topic_is_literature_first(
        default_workbench_config("A systematic survey of graph neural networks")
    )
    assert _paper_writing._topic_is_literature_first(
        default_workbench_config("Meta-analysis of clinical prediction models")
    )
    assert not _paper_writing._topic_is_literature_first(
        default_workbench_config("Train a new classifier for tabular risk prediction")
    )


def test_collect_raw_metrics_filters_non_results_and_formats_conditions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    runs_dir = run_dir / "stage-12" / "runs"
    _write_json(runs_dir / "results.json", {"metrics": {"ignored": 1}})
    _write_json(runs_dir / "bad.json", ["not", "a", "payload"])
    (runs_dir / "broken.json").write_text("{not json", encoding="utf-8")
    _write_json(
        runs_dir / "simulated.json",
        {"status": "simulated", "metrics": {"accuracy": 0.1}},
    )
    _write_json(
        runs_dir / "real.json",
        {
            "key_metrics": {"proposed/env/final/accuracy": 0.91},
            "stdout": "TRAINING_STEPS: 1000\nproposed/env/final/loss: 0.12\nnote: not numeric\n",
        },
    )

    block, has_parsed = _paper_writing._collect_raw_experiment_metrics(run_dir)

    assert has_parsed is True
    assert "ACTUAL EXPERIMENT DATA" in block
    assert "1 run(s)" in block
    assert "## Condition: proposed" in block
    assert "env/final/accuracy: 0.91" in block
    assert "env/final/loss: 0.12" in block
    assert "TRAINING_STEPS" not in block
    assert "simulated" not in block
    assert "ignored" not in block


def test_collect_raw_metrics_prefers_better_refinement_and_skips_regression(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "stage-12" / "runs" / "real.json",
        {"metrics": {"baseline/metric/primary_metric": 0.5}, "stdout": ""},
    )
    _write_json(
        run_dir / "stage-13" / "refinement_log.json",
        {
            "iterations": [
                {
                    "sandbox": {
                        "metrics": {
                            "primary_metric": 0.80,
                            "proposed/metric/accuracy_mean": 0.80,
                        },
                        "stdout": "PAIRED: proposed beats baseline p=0.03\naccuracy: 0.80",
                    },
                    "sandbox_after_fix": {
                        "metrics": {
                            "primary_metric": 0.10,
                            "proposed/metric/accuracy_mean": 0.10,
                            "extra_metric": 999,
                            "another_metric": 888,
                        },
                        "stdout": "accuracy: 0.10",
                    },
                },
                {
                    "sandbox": {
                        "metrics": {
                            "primary_metric": 0.85,
                            "proposed/metric/accuracy_mean": 0.85,
                        },
                        "stdout": "PAIRED: refined comparison p=0.01\naccuracy: 0.85",
                    }
                },
            ]
        },
    )

    block, has_parsed = _paper_writing._collect_raw_experiment_metrics(run_dir)

    assert has_parsed is True
    assert "primary_metric: 0.85" in block
    assert "metric/accuracy_mean: 0.85" in block
    assert "PAIRED: refined comparison" in block
    assert "0.10" not in block
    assert "1 run(s)" in block


def test_check_ablation_effectiveness_flags_trivial_and_near_baseline() -> None:
    summary = {
        "condition_summaries": {
            "standard_baseline": {"metrics": {"accuracy_mean": 100.0}},
            "without_attention": {"metrics": {"accuracy_mean": 99.6}},
            "reduced_model": {"metrics": {"accuracy_mean": 98.5}},
            "strong_ablation": {"metrics": {"accuracy_mean": 80.0}},
        }
    }

    warnings = _paper_writing._check_ablation_effectiveness(summary, threshold=0.02)

    assert any(w.startswith("TRIVIAL:") and "without_attention" in w for w in warnings)
    assert any("reduced_model" in w and "may be ineffective" in w for w in warnings)
    assert all("strong_ablation" not in w for w in warnings)


def test_check_ablation_effectiveness_returns_empty_without_conditions() -> None:
    assert _paper_writing._check_ablation_effectiveness({}) == []


def test_check_ablation_effectiveness_documents_non_numeric_mean_baseline() -> None:
    with pytest.raises(ValueError):
        _paper_writing._check_ablation_effectiveness(
            {"condition_summaries": {"baseline": {"metrics": {"accuracy_mean": "n/a"}}}}
        )


def test_detect_result_contradictions_reports_null_and_negative_maximize_result() -> None:
    summary = {
        "condition_summaries": {
            "random_baseline": {"metrics": {"accuracy_mean": 0.812}},
            "our_proposed_method": {"metrics": {"accuracy_mean": 0.804}},
            "control_variant": {"metrics": {"accuracy_mean": 0.810}},
        }
    }

    advisories = _paper_writing._detect_result_contradictions(summary, "maximize")

    assert any("NULL RESULT" in a for a in advisories)
    assert any("NEGATIVE RESULT" in a and "random_baseline" in a for a in advisories)


def test_detect_result_contradictions_respects_minimize_direction() -> None:
    summary = {
        "condition_summaries": {
            "vanilla_baseline": {"metrics": {"loss_mean": 0.20}},
            "proposed_method": {"metrics": {"loss_mean": 0.35}},
            "malformed": {"metrics": {"loss_mean": "unknown"}},
        }
    }

    advisories = _paper_writing._detect_result_contradictions(summary, "minimize")

    assert len(advisories) == 1
    assert "NEGATIVE RESULT" in advisories[0]
    assert "vanilla_baseline" in advisories[0]



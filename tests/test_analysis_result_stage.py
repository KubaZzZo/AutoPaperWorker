"""Focused tests for Stage 14 result analysis implementation."""

from __future__ import annotations

import json
import ast
import inspect
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.pipeline.stage_impls import _analysis
from researchclaw.pipeline.stages import StageStatus
from researchclaw.workbench.run import default_workbench_config


def _base_exp_data() -> dict[str, Any]:
    return {
        "metrics_summary": {},
        "runs": [],
        "best_run": None,
        "latex_table": "",
        "structured_results": {},
        "paired_comparisons": [],
    }


def test_result_analysis_is_decomposed_into_named_helpers() -> None:
    required_helpers = {
        "_merge_refinement_log",
        "_compute_bootstrap_ci",
        "_detect_ablation_failures",
        "_select_best_sandbox",
        "_build_analysis_summary",
    }
    for helper_name in required_helpers:
        assert hasattr(_analysis, helper_name)

    source = inspect.getsource(_analysis._execute_result_analysis)
    tree = ast.parse(source)
    nested_function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "_get_best_sandbox" not in nested_function_names
    assert len(source.splitlines()) <= 450


def test_result_analysis_merges_refinement_metrics_and_computes_seed_statistics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-14"
    stage_dir.mkdir(parents=True)
    refinement_log = {
        "best_version": "v2",
        "best_metric": 0.82,
        "iterations": [
            {
                "version_dir": "v1",
                "sandbox": {"metrics": {"primary_metric": 0.4}},
            },
            {
                "version_dir": "v2",
                "sandbox": {
                    "metrics": {
                        "primary_metric": 0.82,
                        "baseline/run/0/primary_metric": 0.50,
                        "baseline/run/1/primary_metric": 0.52,
                        "baseline/run/2/primary_metric": 0.51,
                        "proposed/run/0/primary_metric": 0.70,
                        "proposed/run/1/primary_metric": 0.73,
                        "proposed/run/2/primary_metric": 0.74,
                        "baseline/success_rate": 0.9,
                        "proposed/success_rate": 1.0,
                        "note": "skip me",
                    },
                    "stdout": "PAIRED: proposed vs baseline t=5.0 p=0.02\n",
                    "elapsed_sec": 12,
                },
            },
        ],
    }

    monkeypatch.setattr(_analysis, "_collect_experiment_results", lambda *a, **k: _base_exp_data())
    monkeypatch.setattr(
        _analysis,
        "_read_prior_artifact",
        lambda _run_dir, name: json.dumps(refinement_log) if name == "refinement_log.json" else "",
    )
    monkeypatch.setattr(_analysis, "_build_context_preamble", lambda *a, **k: "")
    monkeypatch.setattr(
        "researchclaw.experiment.visualize.generate_all_charts",
        lambda *a, **k: [],
    )

    result = _analysis._execute_result_analysis(
        stage_dir,
        run_dir,
        default_workbench_config("seeded benchmark"),
        AdapterBundle(),
        llm=None,
    )

    assert result.status is StageStatus.DONE
    summary = json.loads((stage_dir / "experiment_summary.json").read_text(encoding="utf-8"))
    assert summary["best_run"]["run_id"] == "iterative-refine-best"
    assert summary["total_runs"] == 1
    assert summary["total_conditions"] == 2
    assert summary["condition_summaries"]["baseline"]["success_rate"] == 0.9
    assert summary["condition_summaries"]["baseline"]["n_seeds"] == 3
    assert "ci95_low" in summary["condition_summaries"]["proposed"]
    assert summary["paired_comparisons"][0]["source"] == "pipeline_computed"
    assert summary["paired_comparisons"][0]["method"] == "proposed"
    assert (stage_dir / "results_table.tex").exists()
    analysis = (stage_dir / "analysis.md").read_text(encoding="utf-8")
    assert "baseline/run/0/primary_metric" in analysis


def test_result_analysis_structured_results_fallback_flags_broken_conditions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-14"
    stage_dir.mkdir(parents=True)
    exp_data = _base_exp_data()
    exp_data.update(
        {
            "runs": [{"run_id": "r1"}],
            "structured_results": {
                "control": {"primary_metric": 0.75, "loss": 0.2},
                "without_component": {"primary_metric": 0.75, "loss": 0.2},
                "metadata": {"ignored": True},
            },
        }
    )

    monkeypatch.setattr(_analysis, "_collect_experiment_results", lambda *a, **k: exp_data)
    monkeypatch.setattr(_analysis, "_read_prior_artifact", lambda *a, **k: "")
    monkeypatch.setattr(_analysis, "_build_context_preamble", lambda *a, **k: "")
    monkeypatch.setattr(
        "researchclaw.experiment.visualize.generate_all_charts",
        lambda *a, **k: [stage_dir / "charts" / "summary.png"],
    )

    result = _analysis._execute_result_analysis(
        stage_dir,
        run_dir,
        default_workbench_config("ablation benchmark"),
        AdapterBundle(),
        llm=None,
    )

    assert "charts/summary.png" in result.artifacts
    summary = json.loads((stage_dir / "experiment_summary.json").read_text(encoding="utf-8"))
    warnings = "\n".join(summary["ablation_warnings"])
    assert "ABLATION FAILURE" in warnings
    assert "ZERO VARIANCE" in warnings
    assert summary["total_conditions"] == 2
    assert summary["condition_metrics"] == summary["condition_summaries"]
    analysis = (stage_dir / "analysis.md").read_text(encoding="utf-8")
    assert "Primary metric key" in analysis
    assert "moderate confidence" in analysis



def test_result_analysis_uses_figure_agent_and_saves_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-14"
    stage_dir.mkdir(parents=True)
    captured: dict[str, Any] = {}
    exp_data = _base_exp_data()
    exp_data["best_run"] = {
        "run_id": "best",
        "metrics": {"primary_metric": 0.91, "loss": 0.08},
    }
    exp_data["metrics_summary"] = {"primary_metric": {"mean": 0.91}}

    class FakeLLM:
        def chat(self, *args: Any, **kwargs: Any) -> Any:
            return type("Response", (), {"content": "LLM analysis"})()

    class FakeFigureAgentConfig:
        def __init__(self, **kwargs: Any) -> None:
            captured["config"] = kwargs

    class FakeFigurePlan:
        figure_count = 1
        passed_count = 1
        elapsed_sec = 0.25

        def get_chart_files(self) -> list[str]:
            return ["accuracy_curve.png"]

        def to_dict(self) -> dict[str, Any]:
            return {
                "figure_count": 1,
                "passed_count": 1,
                "manifest": [{"file_path": "charts/accuracy_curve.png"}],
            }

    class FakeFigureOrchestrator:
        def __init__(self, llm: Any, cfg: Any, stage_dir: Path) -> None:
            captured["llm"] = llm
            captured["stage_dir"] = stage_dir
            captured["cfg"] = cfg

        def orchestrate(self, payload: dict[str, Any]) -> FakeFigurePlan:
            captured["payload"] = payload
            return FakeFigurePlan()

    monkeypatch.setattr(_analysis, "_collect_experiment_results", lambda *a, **k: exp_data)
    monkeypatch.setattr(
        _analysis,
        "_read_prior_artifact",
        lambda _run_dir, name: {
            "paper_draft.md": "Existing draft",
            "topic.md": "Chartable benchmark",
            "hypotheses.md": "Proposed method improves accuracy.",
        }.get(name, ""),
    )
    monkeypatch.setattr(_analysis, "_build_context_preamble", lambda *a, **k: "")
    monkeypatch.setattr(
        _analysis,
        "_chat_with_prompt",
        lambda *a, **k: type("Response", (), {"content": "# Result Analysis\nLLM analysis"})(),
    )
    monkeypatch.setattr(
        "researchclaw.agents.figure_agent.FigureOrchestrator",
        FakeFigureOrchestrator,
    )
    monkeypatch.setattr(
        "researchclaw.agents.figure_agent.orchestrator.FigureAgentConfig",
        FakeFigureAgentConfig,
    )
    monkeypatch.setattr(
        "researchclaw.experiment.visualize.generate_all_charts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy fallback should not run")),
    )

    result = _analysis._execute_result_analysis(
        stage_dir,
        run_dir,
        default_workbench_config("figure agent benchmark"),
        AdapterBundle(),
        llm=FakeLLM(),
    )

    assert (stage_dir / "figure_plan.json").exists()
    assert "charts/accuracy_curve.png" in result.artifacts
    plan = json.loads((stage_dir / "figure_plan.json").read_text(encoding="utf-8"))
    assert plan["figure_count"] == 1
    assert captured["stage_dir"] == stage_dir
    assert captured["config"]["min_figures"] == 3
    assert captured["payload"]["experiment_results"] == {
        "best_run_metrics": {"primary_metric": 0.91, "loss": 0.08}
    }
    assert captured["payload"]["paper_draft"] == "Existing draft"
    assert captured["payload"]["conditions"] == []

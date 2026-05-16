"""Focused tests for Stage 15 analysis/decision helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.llm.client import LLMResponse
from researchclaw.pipeline.stage_impls import _analysis
from researchclaw.workbench.run import default_workbench_config


class RecordingLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return LLMResponse(content=self.response, model="fake-model")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload))


def test_parse_decision_prefers_line_under_heading_and_last_keyword_fallback() -> None:
    assert _analysis._parse_decision("## Decision\n**REFINE**\nDiscussion says pivot later") == "refine"
    assert _analysis._parse_decision("Early PIVOT discussion. Final conclusion: REFINE") == "refine"
    assert _analysis._parse_decision("PIVOT is not warranted. We should PROCEED.") == "pivot"
    assert _analysis._parse_decision("No explicit decision here") == "proceed"


def test_research_decision_injects_degenerate_refine_and_diagnosis_hints(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-15"
    stage_dir.mkdir(parents=True)
    _write_text(run_dir / "stage-14" / "analysis.md", "# Analysis\naccuracy metric from baseline runs")
    _write_json(
        run_dir / "stage-13" / "refinement_log.json",
        {"iterations": [{"metric": 0.999}, {"metric": 0.999}, {"metric": 0.999}]},
    )
    _write_json(
        run_dir / "experiment_diagnosis.json",
        {
            "quality_assessment": {
                "mode": "weak_baseline",
                "sufficient": False,
                "deficiency_types": ["missing_baselines", "single_seed"],
            }
        },
    )
    llm = RecordingLLM(
        "## Decision\nPROCEED\n## Justification\nBaseline metric and seed/run caveats are documented."
    )

    result = _analysis._execute_research_decision(
        stage_dir,
        run_dir,
        default_workbench_config("robust benchmark study"),
        AdapterBundle(),
        llm=llm,
    )

    prompt = llm.calls[0]["messages"][0]["content"]
    assert result.decision == "proceed"
    assert "DEGENERATE REFINE CYCLE DETECTED" in prompt
    assert "Metrics across 3 iterations" in prompt
    assert "EXPERIMENT DIAGNOSIS" in prompt
    assert "weak_baseline" in prompt
    assert "missing_baselines, single_seed" in prompt
    structured = json.loads((stage_dir / "decision_structured.json").read_text(encoding="utf-8"))
    assert structured["decision"] == "proceed"
    assert structured["quality_warnings"] == []


def test_research_decision_injects_ablation_refine_hint_from_best_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-15"
    stage_dir.mkdir(parents=True)
    _write_text(run_dir / "stage-14" / "analysis.md", "# Analysis\nMetrics include baseline runs.")
    _write_json(
        run_dir / "experiment_summary_best.json",
        {
            "condition_summaries": {
                "standard_baseline": {"metrics": {"accuracy_mean": 100.0}},
                "without_attention": {"metrics": {"accuracy_mean": 99.7}},
                "reduced_model": {"metrics": {"accuracy_mean": 99.4}},
            }
        },
    )
    llm = RecordingLLM(
        "## Decision\nREFINE\n## Justification\nBaseline metrics across repeated runs require ablation repair."
    )

    result = _analysis._execute_research_decision(
        stage_dir,
        run_dir,
        default_workbench_config("ablation study"),
        AdapterBundle(),
        llm=llm,
    )

    prompt = llm.calls[0]["messages"][0]["content"]
    assert result.decision == "refine"
    assert "ABLATION QUALITY ASSESSMENT" in prompt
    assert "STRONG RECOMMENDATION: Choose REFINE" in prompt
    assert "without_attention" in prompt
    assert "reduced_model" in prompt


def test_research_decision_records_quality_warnings_for_thin_default_text(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-15"
    stage_dir.mkdir(parents=True)
    llm = RecordingLLM("## Decision\nPIVOT\n## Justification\nThe approach is unsuitable.")

    result = _analysis._execute_research_decision(
        stage_dir,
        run_dir,
        default_workbench_config("thin decision"),
        AdapterBundle(),
        llm=llm,
    )

    assert result.decision == "pivot"
    payload = json.loads((stage_dir / "decision_structured.json").read_text(encoding="utf-8"))
    assert payload["quality_warnings"] == [
        "Decision text does not mention baselines",
        "Decision text does not mention multi-seed/replicate runs",
        "Decision text does not mention evaluation metrics",
    ]

"""Tests for experiment diagnosis and repair workflow helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from researchclaw.pipeline import experiment_workflow


class _FakeDiagnosis:
    deficiencies: list[object] = []

    def to_dict(self) -> dict[str, object]:
        return {"summary": "ok", "deficiencies": []}


class _FakeMode:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeRepairResult:
    def __init__(self, *, success: bool, summary: dict[str, object] | None) -> None:
        self.success = success
        self.total_cycles = 1
        self.final_mode = _FakeMode("full_paper" if success else "short_report")
        self.best_experiment_summary = summary

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "total_cycles": self.total_cycles,
            "final_mode": self.final_mode.value,
            "best_experiment_summary": self.best_experiment_summary,
        }


def _install_fake_diagnosis_module(monkeypatch, *, sufficient: bool) -> None:
    module = ModuleType("researchclaw.pipeline.experiment_diagnosis")

    def diagnose_experiment(**kwargs):
        module.last_diagnosis_kwargs = kwargs
        return _FakeDiagnosis()

    def assess_experiment_quality(summary, ref_log):
        module.last_quality_args = (summary, ref_log)
        return SimpleNamespace(
            mode=_FakeMode("full_paper" if sufficient else "short_report"),
            sufficient=sufficient,
            repair_possible=not sufficient,
            deficiencies=[],
        )

    module.diagnose_experiment = diagnose_experiment
    module.assess_experiment_quality = assess_experiment_quality
    monkeypatch.setitem(sys.modules, "researchclaw.pipeline.experiment_diagnosis", module)


def _install_fake_repair_module(
    monkeypatch,
    *,
    repair_result: _FakeRepairResult | None = None,
    score_by_marker: dict[str, float] | None = None,
) -> None:
    module = ModuleType("researchclaw.pipeline.experiment_repair")

    def build_repair_prompt(diag, code, time_budget_sec):
        module.last_prompt_args = (diag, code, time_budget_sec)
        return "repair prompt"

    def run_repair_loop(**kwargs):
        module.last_repair_kwargs = kwargs
        return repair_result or _FakeRepairResult(success=False, summary=None)

    def _summary_quality_score(summary):
        marker = str(summary.get("marker", ""))
        return (score_by_marker or {}).get(marker, 0.0)

    module.build_repair_prompt = build_repair_prompt
    module.run_repair_loop = run_repair_loop
    module._summary_quality_score = _summary_quality_score
    monkeypatch.setitem(sys.modules, "researchclaw.pipeline.experiment_repair", module)


def test_diagnosis_writes_report_without_repair_prompt_when_quality_is_sufficient(
    tmp_config,
    run_dir: Path,
    monkeypatch,
) -> None:
    stage14 = run_dir / "stage-14"
    stage14.mkdir()
    (stage14 / "experiment_summary.json").write_text(
        json.dumps({"marker": "summary"}), encoding="utf-8"
    )
    _install_fake_diagnosis_module(monkeypatch, sufficient=True)
    _install_fake_repair_module(monkeypatch)
    messages: list[str] = []

    experiment_workflow.run_experiment_diagnosis(
        run_dir,
        tmp_config,
        "run-1",
        progress_reporter=messages.append,
    )

    report = json.loads((run_dir / "experiment_diagnosis.json").read_text(encoding="utf-8"))
    assert report["repair_needed"] is False
    assert report["quality_assessment"]["sufficient"] is True
    assert not (run_dir / "repair_prompt.txt").exists()
    assert messages == ["[run-1] Experiment diagnosis: full_paper - quality OK"]


def test_diagnosis_saves_repair_prompt_with_available_experiment_code(
    tmp_config,
    run_dir: Path,
    monkeypatch,
) -> None:
    stage14 = run_dir / "stage-14"
    stage14.mkdir()
    (stage14 / "experiment_summary.json").write_text("{}", encoding="utf-8")
    code_dir = run_dir / "stage-10" / "experiment"
    code_dir.mkdir(parents=True)
    (code_dir / "main.py").write_text("print('hi')", encoding="utf-8")
    _install_fake_diagnosis_module(monkeypatch, sufficient=False)
    _install_fake_repair_module(monkeypatch)

    experiment_workflow.run_experiment_diagnosis(run_dir, tmp_config, "run-2")

    assert (run_dir / "repair_prompt.txt").read_text(encoding="utf-8") == "repair prompt"
    repair_module = sys.modules["researchclaw.pipeline.experiment_repair"]
    _, code, time_budget_sec = repair_module.last_prompt_args
    assert code == {"main.py": "print('hi')"}
    assert time_budget_sec == tmp_config.experiment.time_budget_sec


def test_repair_promotes_better_summary_and_reruns_diagnosis(
    tmp_config,
    run_dir: Path,
    monkeypatch,
) -> None:
    stage14 = run_dir / "stage-14"
    stage14.mkdir()
    (stage14 / "experiment_summary.json").write_text(
        json.dumps({"marker": "old"}), encoding="utf-8"
    )
    repair_summary = {"marker": "repair", "accuracy": 0.9}
    _install_fake_repair_module(
        monkeypatch,
        repair_result=_FakeRepairResult(success=True, summary=repair_summary),
        score_by_marker={"old": 1.0, "repair": 3.0},
    )
    diagnosis_calls: list[str] = []
    monkeypatch.setattr(
        experiment_workflow,
        "run_experiment_diagnosis",
        lambda run_dir, config, run_id, *, progress_reporter=None: diagnosis_calls.append(run_id),
    )

    experiment_workflow.run_experiment_repair(run_dir, tmp_config, "run-3")

    promoted = json.loads((stage14 / "experiment_summary.json").read_text(encoding="utf-8"))
    assert promoted == repair_summary
    assert diagnosis_calls == ["run-3"]
    assert (run_dir / "experiment_repair_result.json").exists()


def test_repair_keeps_existing_summary_when_existing_score_is_higher(
    tmp_config,
    run_dir: Path,
    monkeypatch,
) -> None:
    stage14 = run_dir / "stage-14"
    stage14.mkdir()
    existing = {"marker": "old", "accuracy": 0.95}
    (stage14 / "experiment_summary.json").write_text(json.dumps(existing), encoding="utf-8")
    _install_fake_repair_module(
        monkeypatch,
        repair_result=_FakeRepairResult(success=False, summary={"marker": "repair"}),
        score_by_marker={"old": 5.0, "repair": 1.0},
    )
    messages: list[str] = []

    experiment_workflow.run_experiment_repair(
        run_dir,
        tmp_config,
        "run-4",
        progress_reporter=messages.append,
    )

    kept = json.loads((stage14 / "experiment_summary.json").read_text(encoding="utf-8"))
    assert kept == existing
    assert messages == []

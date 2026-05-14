# pyright: basic, reportMissingImports=false, reportUnusedCallResult=false
"""Tests for optional pipeline integration modules."""

from __future__ import annotations

import json
from pathlib import Path


def test_event_log_roundtrip(tmp_path: Path) -> None:
    from researchclaw.pipeline.event_log import EventLog, EventType, create_event

    log = EventLog(log_dir=tmp_path)
    event = create_event(EventType.PIPELINE_START, run_id="run-1", stages=23)
    log.append(event)

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    assert payload["type"] == "pipeline_start"
    assert payload["run_id"] == "run-1"
    assert payload["stages"] == 23


def test_cost_tracker_budget_check_and_logging(tmp_path: Path) -> None:
    from researchclaw.cost_tracker import CostTracker

    tracker = CostTracker(log_path=tmp_path / "cost_log.jsonl")
    tracker.record("openai", "gpt-test", prompt_tokens=100, completion_tokens=50, cost_usd=1.25)

    assert tracker.total_cost_usd == 1.25
    assert tracker.check_budget(2.0)
    assert not tracker.check_budget(1.0)


def test_cost_tracker_summarizes_by_stage_and_model(tmp_path: Path) -> None:
    from researchclaw.cost_tracker import (
        CostTracker,
        summarize_cost_log,
        write_cost_summary,
    )

    tracker = CostTracker(log_path=tmp_path / "cost_log.jsonl")
    tracker.record(
        "openai",
        "gpt-test",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.10,
        stage="HYPOTHESIS_GEN",
    )
    tracker.record(
        "openai",
        "gpt-test",
        prompt_tokens=25,
        completion_tokens=25,
        cost_usd=0.05,
        stage="HYPOTHESIS_GEN",
    )
    tracker.record(
        "anthropic",
        "claude-test",
        prompt_tokens=40,
        completion_tokens=10,
        cost_usd=0.20,
        stage="PAPER_DRAFT",
    )

    summary = summarize_cost_log(tmp_path / "cost_log.jsonl")

    assert summary["total_cost_usd"] == 0.35
    assert summary["total_prompt_tokens"] == 165
    assert summary["total_completion_tokens"] == 85
    assert summary["by_stage"]["HYPOTHESIS_GEN"]["cost_usd"] == 0.15
    assert summary["by_stage"]["HYPOTHESIS_GEN"]["total_tokens"] == 200
    assert summary["by_model"]["openai/gpt-test"]["calls"] == 2

    out_path = write_cost_summary(tmp_path)
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["by_stage"]["PAPER_DRAFT"]["cost_usd"] == 0.2


def test_cost_tracker_reports_forecast_variance(tmp_path: Path) -> None:
    from researchclaw.cost_tracker import CostTracker, summarize_cost_log

    tracker = CostTracker(log_path=tmp_path / "cost_log.jsonl")
    tracker.record(
        "openai",
        "gpt-4o-mini",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cost_usd=1.00,
        stage="PAPER_DRAFT",
    )

    summary = summarize_cost_log(tmp_path / "cost_log.jsonl")

    assert summary["estimated_cost_usd"] == 0.75
    assert summary["cost_variance_usd"] == 0.25
    assert summary["cost_variance_ratio"] == 0.333333
    assert summary["by_model"]["openai/gpt-4o-mini"]["estimated_cost_usd"] == 0.75
    assert summary["by_model"]["openai/gpt-4o-mini"]["cost_variance_usd"] == 0.25


def test_dashboard_collector_prefers_progress_snapshot(tmp_path: Path) -> None:
    from researchclaw.dashboard.collector import DashboardCollector

    run_dir = tmp_path / "rc-progress"
    run_dir.mkdir()
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "run_id": "run-progress",
                "status": "running",
                "current_stage": 7,
                "current_stage_name": "SYNTHESIS",
                "total_stages": 23,
                "elapsed_sec": 12.5,
                "stages_done": 6,
                "stages_failed": 0,
                "cost_usd": 1.25,
                "updated_at": "2026-05-14T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    snap = DashboardCollector().collect_run(run_dir)

    assert snap.run_id == "run-progress"
    assert snap.status == "running"
    assert snap.current_stage == 7
    assert snap.current_stage_name == "SYNTHESIS"
    assert snap.elapsed_sec == 12.5
    assert snap.stages_done == 6
    assert snap.cost_usd == 1.25


def test_dashboard_collector_logs_malformed_progress_snapshot(
    tmp_path: Path,
    caplog,
) -> None:
    from researchclaw.dashboard.collector import DashboardCollector

    run_dir = tmp_path / "rc-bad-progress"
    run_dir.mkdir()
    (run_dir / "progress.json").write_text("{not-json", encoding="utf-8")

    with caplog.at_level("DEBUG", logger="researchclaw.dashboard.collector"):
        snap = DashboardCollector().collect_run(run_dir)

    assert snap.run_id == "rc-bad-progress"
    assert snap.status == "unknown"
    assert "Failed to read progress snapshot" in caplog.text
    assert str(run_dir / "progress.json") in caplog.text


def test_experiment_spec_parse_and_validate() -> None:
    from researchclaw.pipeline.experiment_spec import (
        MetricDef,
        parse_spec,
        validate_results_against_spec,
    )

    spec_text = "# Experiment Spec\n- metric: accuracy (maximize)\n- baseline: Base\n"
    spec = parse_spec(spec_text)
    spec.metrics.append(MetricDef(name="loss", direction="minimize"))

    violations = validate_results_against_spec(
        spec,
        {"metrics": {"accuracy": 0.9, "loss": 0.2}, "baselines": ["Base"]},
    )
    assert violations == []


def test_pitfall_detector_flags_data_overlap() -> None:
    from researchclaw.pipeline.pitfall_detector import PitfallDetector, PitfallType

    code = "train_data = dataset\nval_data = train_data\n"
    pitfalls = PitfallDetector().detect_all(code=code, results={}, experiment_config={})

    assert any(p.type is PitfallType.DATA_LEAKAGE for p in pitfalls)

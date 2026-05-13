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

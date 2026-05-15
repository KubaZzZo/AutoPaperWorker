from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchclaw.pipeline.stage_impls.execution_run import persist_sandbox_run_result


def test_persist_sandbox_run_result_marks_stdout_failure_and_writes_payload(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stage_dir = tmp_path / "stage-12"
    runs_dir = stage_dir / "runs"
    sandbox_project = runs_dir / "sandbox" / "_project"
    sandbox_project.mkdir(parents=True)
    (sandbox_project / "results.json").write_text("{bad json", encoding="utf-8")
    stage_dir.mkdir(exist_ok=True)

    result = SimpleNamespace(
        returncode=0,
        stdout="FAIL: NaN/divergence detected\n",
        stderr="",
        metrics={},
        elapsed_sec=29.5,
        timed_out=False,
    )

    with caplog.at_level(
        "DEBUG",
        logger="researchclaw.pipeline.stage_impls._execution",
    ):
        summary = persist_sandbox_run_result(
            stage_dir=stage_dir,
            runs_dir=runs_dir,
            result=result,
            stdout_log_path=runs_dir / "run-1.stdout.log",
            stderr_log_path=runs_dir / "run-1.stderr.log",
            time_budget_sec=30,
            parse_metrics=lambda _stdout: {},
            timestamp_factory=lambda: "2026-05-15T00:00:00+00:00",
            diagnostic_logger=logging.getLogger(
                "researchclaw.pipeline.stage_impls._execution"
            ),
        )

    payload = json.loads((runs_dir / "run-1.json").read_text(encoding="utf-8"))
    warning = json.loads(
        (stage_dir / "time_budget_warning.json").read_text(encoding="utf-8")
    )
    assert summary.run_status == "failed"
    assert payload["status"] == "failed"
    assert payload["metrics"] == {}
    assert payload["completed_at"] == "2026-05-15T00:00:00+00:00"
    assert "structured_results" not in payload
    assert warning["budget_sec"] == 30
    assert "Failed to read sandbox structured results" in caplog.text
    assert "Experiment exited cleanly but stdout contains failure signals" in caplog.text

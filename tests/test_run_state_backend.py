from __future__ import annotations

import json

from researchclaw.run_state import JsonRunStateBackend


def test_json_run_state_backend_round_trips_progress_snapshot(tmp_path) -> None:
    backend = JsonRunStateBackend()
    run_dir = tmp_path / "rc-run"
    payload = {
        "run_id": "rc-run",
        "status": "running",
        "current_stage": 12,
        "experiment_runs": [{"run_id": "run-1", "status": "partial"}],
    }

    backend.write_progress(run_dir, payload)

    progress_text = (run_dir / "progress.json").read_text(encoding="utf-8")
    assert json.loads(progress_text) == payload
    assert backend.read_progress(run_dir) == payload


def test_json_run_state_backend_returns_none_for_missing_or_malformed_progress(
    tmp_path,
    caplog,
) -> None:
    backend = JsonRunStateBackend()
    run_dir = tmp_path / "rc-run"

    assert backend.read_progress(run_dir) is None

    run_dir.mkdir()
    (run_dir / "progress.json").write_text("{not-json", encoding="utf-8")
    with caplog.at_level("DEBUG", logger="researchclaw.run_state"):
        assert backend.read_progress(run_dir) is None

    assert "Failed to read progress snapshot" in caplog.text

from __future__ import annotations

import json

from researchclaw.run_state import JsonRunStateBackend, SQLiteRunStateBackend


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


def test_sqlite_run_state_backend_round_trips_progress_snapshot(tmp_path) -> None:
    backend = SQLiteRunStateBackend(tmp_path / "run_state.sqlite")
    run_dir = tmp_path / "rc-sqlite"
    payload = {
        "run_id": "rc-sqlite",
        "status": "running",
        "current_stage": 12,
        "experiment_runs": [{"run_id": "run-1", "status": "completed"}],
    }

    backend.write_progress(run_dir, payload)

    assert backend.read_progress(run_dir) == payload
    assert not (run_dir / "progress.json").exists()


def test_sqlite_run_state_backend_can_serve_dashboard_collector(tmp_path) -> None:
    from researchclaw.dashboard.collector import DashboardCollector

    backend = SQLiteRunStateBackend(tmp_path / "run_state.sqlite")
    run_dir = tmp_path / "rc-dashboard"
    backend.write_progress(
        run_dir,
        {
            "run_id": "rc-dashboard",
            "status": "running",
            "current_stage": 12,
            "current_stage_name": "EXPERIMENT_RUN",
            "experiment_runs": [{"run_id": "run-1", "status": "partial"}],
        },
    )

    snap = DashboardCollector(run_state_backend=backend).collect_run(run_dir)

    assert snap.run_id == "rc-dashboard"
    assert snap.current_stage == 12
    assert snap.experiment_runs == [{"run_id": "run-1", "status": "partial"}]


def test_json_and_sqlite_backends_are_independent_adapters(tmp_path) -> None:
    json_backend = JsonRunStateBackend()
    sqlite_backend = SQLiteRunStateBackend(tmp_path / "run_state.sqlite")
    run_dir = tmp_path / "rc-both"

    json_backend.write_progress(run_dir, {"run_id": "json", "status": "running"})
    sqlite_backend.write_progress(run_dir, {"run_id": "sqlite", "status": "done"})

    assert json_backend.read_progress(run_dir) == {"run_id": "json", "status": "running"}
    assert sqlite_backend.read_progress(run_dir) == {
        "run_id": "sqlite",
        "status": "done",
    }

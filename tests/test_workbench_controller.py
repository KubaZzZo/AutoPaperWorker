from __future__ import annotations

from pathlib import Path


def test_controller_delegates_backend_operations(monkeypatch) -> None:
    from researchclaw.workbench.controller import WorkbenchController
    from researchclaw.workbench.remote import RemoteProfile

    controller = WorkbenchController()
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "researchclaw.workbench.controller.search_papers_for_workbench",
        lambda topic, limit=10: (calls.__setitem__("search", (topic, limit)) or []),
    )
    monkeypatch.setattr(
        "researchclaw.workbench.controller.import_cnki_files",
        lambda paths: (calls.__setitem__("cnki", tuple(paths)) or []),
    )
    monkeypatch.setattr(
        "researchclaw.workbench.controller.create_project_plan",
        lambda topic: (calls.__setitem__("plan", topic) or {"topic": topic}),
    )
    monkeypatch.setattr(
        "researchclaw.workbench.controller.analyze_project",
        lambda root: (calls.__setitem__("project", Path(root)) or object()),
    )

    assert controller.search("topic", limit=3) == []
    assert controller.import_cnki([Path("a.ris")]) == []
    assert controller.create_project_plan("topic") == {"topic": "topic"}
    assert controller.analyze_project(Path("demo")) is not None

    profile = controller.parse_remote_profile("ssh root@example.com")
    assert isinstance(profile, RemoteProfile)
    assert calls["search"] == ("topic", 3)
    assert calls["cnki"] == (Path("a.ris"),)
    assert calls["plan"] == "topic"
    assert calls["project"] == Path("demo")


def test_run_pipeline_for_workbench_passes_progress_reporter(monkeypatch, tmp_path: Path) -> None:
    from researchclaw.workbench.controller import WorkbenchController

    controller = WorkbenchController()
    seen: dict[str, object] = {}

    def fake_execute_pipeline(**kwargs):
        seen.update(kwargs)
        reporter = kwargs["progress_reporter"]
        reporter("hello")
        return []

    monkeypatch.setattr("researchclaw.workbench.run.execute_pipeline", fake_execute_pipeline)
    monkeypatch.setattr(
        "researchclaw.workbench.run.AdapterBundle",
        lambda: object(),
    )

    messages: list[str] = []
    run_dir = controller.run_pipeline(
        topic="new topic",
        output=tmp_path / "run",
        progress_reporter=messages.append,
    )

    assert run_dir == tmp_path / "run"
    assert messages == ["hello"]
    assert seen["config"].research.topic == "new topic"
    assert seen["run_id"].startswith("rc-")
    assert seen["run_dir"] == tmp_path / "run"
    assert seen["progress_reporter"] is not None
    assert seen["auto_approve_gates"] is True

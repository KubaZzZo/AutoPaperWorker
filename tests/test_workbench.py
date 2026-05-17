from __future__ import annotations

from pathlib import Path

import pytest


def test_build_model_config_supports_cloud_and_local_endpoint() -> None:
    from researchclaw.workbench.models import build_model_config

    cloud = build_model_config(
        mode="cloud",
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    )
    assert cloud.provider == "openai"
    assert cloud.primary_model == "gpt-4o-mini"
    assert cloud.api_key_env == "OPENAI_API_KEY"

    local = build_model_config(
        mode="local",
        model="qwen2.5:14b",
        base_url="http://127.0.0.1:11434/v1",
    )
    assert local.provider == "openai-compatible"
    assert local.base_url == "http://127.0.0.1:11434/v1"
    assert local.api_key_env == ""


def test_search_papers_for_workbench_limits_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchclaw.literature.models import Paper
    from researchclaw.workbench.search import search_papers_for_workbench

    calls: dict[str, object] = {}

    def fake_search_papers(**kwargs: object) -> list[Paper]:
        calls.update(kwargs)
        return [
            Paper(
                paper_id="p1",
                title="A Paper",
                year=2026,
                source="openalex",
                url="https://example.test/p1",
                abstract="summary",
                citation_count=7,
            )
        ]

    monkeypatch.setattr("researchclaw.workbench.search.search_papers", fake_search_papers)

    results = search_papers_for_workbench("graph neural network", limit=3)

    assert calls["query"] == "graph neural network"
    assert calls["sources"] == ("openalex", "arxiv")
    assert results[0].title == "A Paper"
    assert results[0].source == "openalex"


def test_cnki_import_parses_ris_and_pdf(tmp_path: Path) -> None:
    from researchclaw.workbench.cnki_import import import_cnki_files

    ris = tmp_path / "cnki.ris"
    ris.write_text(
        "\n".join(
            [
                "TY  - JOUR",
                "TI  - 中文论文标题",
                "AU  - 张三",
                "PY  - 2024",
                "JO  - 计算机研究",
                "UR  - https://kns.cnki.net/example",
                "ER  -",
            ]
        ),
        encoding="utf-8",
    )
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    records = import_cnki_files([ris, pdf])

    assert [r.title for r in records] == ["中文论文标题", "paper"]
    assert records[0].source == "cnki"
    assert records[0].authors == ("张三",)
    assert records[1].file_path == str(pdf)


def test_cs_project_classification_and_read_only_analysis(tmp_path: Path) -> None:
    from researchclaw.workbench.cs_project import analyze_project, classify_graduation_project

    assert classify_graduation_project("基于深度学习的图像识别系统") == "algorithm"
    assert classify_graduation_project("校园二手交易平台设计与实现") == "system"

    (tmp_path / "app.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    report = analyze_project(tmp_path)

    assert report.root == tmp_path
    assert "python" in report.languages
    assert report.file_count == 2
    assert report.suggested_sections


def test_remote_profile_parses_autodl_ssh_and_redacts_password() -> None:
    from researchclaw.workbench.remote import parse_ssh_command, save_profile_dict

    profile = parse_ssh_command(
        "ssh -p 12345 root@connect.autodl.com",
        platform="autodl",
        password="secret",
    )

    assert profile.platform == "autodl"
    assert profile.host == "connect.autodl.com"
    assert profile.port == 12345
    assert profile.user == "root"
    assert profile.password == "secret"

    saved = save_profile_dict(profile)
    assert saved["password"] == ""
    assert saved["auth_method"] == "password"


def test_remote_executor_builds_key_first_commands(tmp_path: Path) -> None:
    from researchclaw.workbench.remote import RemoteExecutor, RemoteProfile

    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], timeout: int) -> tuple[int, str, str]:
        calls.append(cmd)
        return 0, "ok", ""

    profile = RemoteProfile(
        platform="gpuhome",
        host="gpu.example.com",
        user="root",
        port=2222,
        key_path="C:/keys/id_rsa",
    )
    executor = RemoteExecutor(profile, runner=fake_runner)

    assert executor.test_connection().success is True
    executor.upload_code(tmp_path, "/workspace/project")
    result = executor.run_command("python train.py", remote_dir="/workspace/project")
    executor.download_results("/workspace/project/results", tmp_path / "results")

    assert result.stdout == "ok"
    assert calls[0][:3] == ["ssh", "-p", "2222"]
    assert "-i" in calls[0]
    assert calls[1][0] in {"scp", "rsync"}
    assert calls[2][0] == "ssh"
    assert calls[3][0] in {"scp", "rsync"}


def test_remote_executor_password_requires_paramiko() -> None:
    from researchclaw.workbench.remote import RemoteExecutor, RemoteProfile

    profile = RemoteProfile(
        platform="autodl",
        host="connect.autodl.com",
        user="root",
        password="secret",
    )
    executor = RemoteExecutor(profile, paramiko_client_factory=None)

    with pytest.raises(RuntimeError, match="Paramiko"):
        executor.test_connection()


def test_remote_executor_password_uses_paramiko_factory(tmp_path: Path) -> None:
    from researchclaw.workbench.remote import RemoteExecutor, RemoteProfile

    class FakeStream:
        def __init__(self, text: str = "") -> None:
            self._text = text

        def read(self) -> bytes:
            return self._text.encode()

    class FakeSftp:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []
            self.get_calls: list[tuple[str, str]] = []
            self.tree: dict[str, list[tuple[str, bool]]] = {
                "/root/project/results": [("metrics.json", False), ("plots", True)],
                "/root/project/results/plots": [("chart.png", False)],
            }

        def mkdir(self, _path: str) -> None:
            return None

        def listdir_attr(self, path: str):
            class Attr:
                def __init__(self, filename: str, is_dir: bool) -> None:
                    self.filename = filename
                    self.st_mode = 0o040755 if is_dir else 0o100644

            return [Attr(name, is_dir) for name, is_dir in self.tree.get(path, [])]

        def put(self, local: str, remote: str) -> None:
            self.put_calls.append((local, remote))

        def get(self, remote: str, local: str) -> None:
            self.get_calls.append((remote, local))

        def close(self) -> None:
            return None

    class FakeClient:
        def __init__(self) -> None:
            self.connected: dict[str, object] = {}
            self.commands: list[str] = []
            self.sftp = FakeSftp()

        def set_missing_host_key_policy(self, _policy: object) -> None:
            return None

        def connect(self, **kwargs: object) -> None:
            self.connected = kwargs

        def exec_command(self, command: str, timeout: int) -> tuple[FakeStream, FakeStream, FakeStream]:
            self.commands.append(command)
            return FakeStream(), FakeStream("ok"), FakeStream()

        def open_sftp(self) -> FakeSftp:
            return self.sftp

        def close(self) -> None:
            return None

    fake = FakeClient()
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    profile = RemoteProfile(
        platform="autodl",
        host="connect.autodl.com",
        user="root",
        port=10022,
        password="secret",
    )
    executor = RemoteExecutor(profile, paramiko_client_factory=lambda: fake)

    assert executor.test_connection().success is True
    assert executor.run_command("python main.py", remote_dir="/root/project").stdout == "ok"
    executor.upload_code(tmp_path, "/root/project")
    executor.download_results("/root/project/results", tmp_path / "results")

    assert fake.connected["hostname"] == "connect.autodl.com"
    assert fake.connected["password"] == "secret"
    assert "cd /root/project && python main.py" in fake.commands
    assert fake.sftp.put_calls
    assert any(call[1].endswith("metrics.json") for call in fake.sftp.get_calls)


def test_workbench_run_builds_config_without_executing_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchclaw.workbench.run import build_workbench_config, default_workbench_config

    base = default_workbench_config("old topic")
    cfg = build_workbench_config(
        topic="new topic",
        base_config=base,
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        experiment_mode="simulated",
    )

    assert cfg.research.topic == "new topic"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.primary_model == "gpt-4o-mini"
    assert cfg.experiment.mode == "simulated"


def test_cli_workbench_search_uses_backend(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from researchclaw import cli
    from researchclaw.workbench.search import WorkbenchPaper

    def fake_search(topic: str, limit: int = 10) -> list[WorkbenchPaper]:
        assert topic == "test topic"
        assert limit == 2
        return [
            WorkbenchPaper(
                title="Result One",
                year=2025,
                source="arxiv",
                url="https://arxiv.org/abs/1",
                abstract="",
                citation_count=0,
            )
        ]

    monkeypatch.setattr("researchclaw.workbench.search.search_papers_for_workbench", fake_search)

    assert cli.main(["workbench", "search", "--topic", "test topic", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "Result One" in out
    assert "arxiv" in out


def test_cli_gui_dispatches_to_gui_main(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchclaw import cli

    monkeypatch.setattr("researchclaw.gui.app.main", lambda: 0)

    assert cli.main(["gui"]) == 0


def test_gui_module_imports_when_customtkinter_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import importlib

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "customtkinter":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    module = importlib.import_module("researchclaw.gui.app")

    assert module.create_app is not None

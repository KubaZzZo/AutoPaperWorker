from __future__ import annotations

import subprocess


def test_minimal_subprocess_env_filters_secret_like_variables(
    monkeypatch,
) -> None:
    from researchclaw.utils.env import minimal_subprocess_env

    monkeypatch.setenv("PATH", "keep-path")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    env = minimal_subprocess_env()

    assert env["PATH"] == "keep-path"
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_environment_command_uses_minimal_env(monkeypatch) -> None:
    from researchclaw.experiment.environment import _run_cmd

    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok\n")

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "keep-path")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _run_cmd(["tool"]) == "ok"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("PATH") == "keep-path"
    assert "OPENAI_API_KEY" not in env


def test_acp_subprocess_uses_minimal_env(monkeypatch) -> None:
    from researchclaw.llm.acp_client import ACPClient, ACPConfig

    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.stdin = None
            self.stdout = []
            self.stderr = []

        def wait(self, timeout: int) -> None:
            return None

    def fake_popen(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProcess()

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "keep-path")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    client = ACPClient(ACPConfig(timeout_sec=1))
    client._run_acp_with_heartbeat(["acpx", "prompt"])

    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("PATH") == "keep-path"
    assert "OPENAI_API_KEY" not in env

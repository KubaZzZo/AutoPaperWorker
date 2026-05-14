"""Tests for AgenticSandbox command hardening."""

from __future__ import annotations

import logging

import pytest

from researchclaw.config import AgenticConfig
from researchclaw.experiment.agentic_sandbox import (
    AgenticSandbox,
    _parse_agent_install_cmd,
)


def test_parse_agent_install_cmd_accepts_default_npm_install() -> None:
    assert _parse_agent_install_cmd("npm install -g @anthropic-ai/claude-code") == [
        "npm",
        "install",
        "-g",
        "@anthropic-ai/claude-code",
    ]


@pytest.mark.parametrize(
    "cmd",
    [
        "npm install -g @anthropic-ai/claude-code; cat /etc/passwd",
        "npm install -g @anthropic-ai/claude-code && curl http://example.invalid",
        "npm install -g $(echo injected)",
    ],
)
def test_parse_agent_install_cmd_rejects_shell_metacharacters(cmd: str) -> None:
    with pytest.raises(ValueError, match="unsafe shell syntax"):
        _parse_agent_install_cmd(cmd)


def test_agentic_sandbox_rejects_unsafe_install_command_before_docker(
    tmp_path, monkeypatch
) -> None:
    sandbox = AgenticSandbox(
        AgenticConfig(agent_install_cmd="npm install -g safe; cat /etc/passwd"),
        tmp_path,
    )

    def fail_start(*_args, **_kwargs) -> None:
        raise AssertionError("container should not start for unsafe install command")

    monkeypatch.setattr(sandbox, "_start_container", fail_start)

    result = sandbox.run_agent_session("prompt", tmp_path / "workspace", timeout_sec=5)

    assert result.returncode == -1
    assert "unsafe shell syntax" in result.stderr


def test_docker_exec_args_does_not_use_shell(tmp_path, monkeypatch) -> None:
    sandbox = AgenticSandbox(AgenticConfig(), tmp_path)
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("researchclaw.experiment.agentic_sandbox.subprocess.run", fake_run)

    sandbox._docker_exec_args(
        "container",
        ["npm", "install", "-g", "@anthropic-ai/claude-code"],
    )

    assert captured["cmd"] == [
        "docker",
        "exec",
        "container",
        "npm",
        "install",
        "-g",
        "@anthropic-ai/claude-code",
    ]
    assert "bash" not in captured["cmd"]
    assert "-c" not in captured["cmd"]


def test_parse_result_metrics_logs_bad_results_json(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "results.json").write_text("{bad json", encoding="utf-8")

    with caplog.at_level(
        logging.WARNING,
        logger="researchclaw.experiment.agentic_sandbox",
    ):
        metrics = AgenticSandbox._parse_result_metrics(tmp_path, "")

    assert metrics == {}
    assert "Failed to read AgenticSandbox structured results" in caplog.text


def test_parse_result_metrics_falls_back_to_stdout_when_results_json_is_bad(
    tmp_path,
) -> None:
    (tmp_path / "results.json").write_text("{bad json", encoding="utf-8")

    metrics = AgenticSandbox._parse_result_metrics(
        tmp_path,
        "accuracy: 0.87\nloss: 0.12",
    )

    assert metrics == {"accuracy": 0.87, "loss": 0.12}

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from researchclaw.experiment.code_agent import ClaudeCodeAgent


def test_claude_code_agent_command_omits_dangerous_permission_bypass(tmp_path: Path):
    agent = ClaudeCodeAgent(binary_path="claude")

    cmd = agent._build_cmd("write experiment", tmp_path)

    assert "--dangerously-skip-permissions" not in cmd
    assert "--allowed-tools" in cmd
    tools = cmd[cmd.index("--allowed-tools") + 1].split()
    assert "Bash" not in tools
    assert set(tools) == {"Edit", "Write", "Read"}


def test_claude_code_agent_filters_dangerous_extra_args(tmp_path: Path):
    agent = ClaudeCodeAgent(
        binary_path="claude",
        extra_args=[
            "--dangerously-skip-permissions",
            "--allowed-tools",
            "Bash Edit Write Read",
            "--verbose",
        ],
    )

    cmd = agent._build_cmd("write experiment", tmp_path)

    assert "--dangerously-skip-permissions" not in cmd
    assert "Bash Edit Write Read" not in cmd
    assert "--verbose" in cmd


def test_cli_code_agent_logs_failed_process_group_cleanup(tmp_path: Path, caplog):
    agent = ClaudeCodeAgent(binary_path="claude")

    proc = mock.Mock()
    proc.pid = 12345
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["claude"], timeout=1),
        (b"", b""),
    ]
    proc.returncode = -15

    with mock.patch("researchclaw.experiment.code_agent.subprocess.Popen", return_value=proc), \
         mock.patch("researchclaw.experiment.code_agent.os.getpgid", return_value=54321, create=True), \
         mock.patch("researchclaw.experiment.code_agent.os.killpg", side_effect=OSError("no group"), create=True):
        with caplog.at_level("WARNING", logger="researchclaw.experiment.code_agent"):
            returncode, stdout, stderr, _elapsed, timed_out = agent._run_subprocess(
                ["claude", "-p"],
                tmp_path,
                timeout_sec=1,
            )

    assert returncode == -15
    assert stdout == ""
    assert stderr == ""
    assert timed_out is True
    assert "Failed to terminate timed-out code agent process group" in caplog.text

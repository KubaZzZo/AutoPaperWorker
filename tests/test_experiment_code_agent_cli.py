from __future__ import annotations

from pathlib import Path

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

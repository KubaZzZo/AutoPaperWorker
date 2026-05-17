"""Tests for CLI setup helpers."""

from __future__ import annotations

import os
import runpy
from unittest.mock import MagicMock, patch

import pytest

from researchclaw import cli


def test_install_opencode_uses_which_resolved_npm_path():
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch(
        "researchclaw.cli.shutil.which",
        return_value=r"C:\Program Files\nodejs\npm.cmd",
    ), patch("researchclaw.cli.subprocess.run", return_value=mock_result) as run_mock:
        assert cli._install_opencode() is True

    run_mock.assert_called_once()
    assert run_mock.call_args.args[0][0] == r"C:\Program Files\nodejs\npm.cmd"


def test_install_opencode_returns_false_when_npm_missing():
    with patch("researchclaw.cli.shutil.which", return_value=None):
        assert cli._install_opencode() is False


def test_is_opencode_installed_uses_which_resolved_path():
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch(
        "researchclaw.cli.shutil.which",
        return_value=r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd",
    ), patch("researchclaw.cli.subprocess.run", return_value=mock_result) as run_mock:
        assert cli._is_opencode_installed() is True

    run_mock.assert_called_once()
    assert run_mock.call_args.args[0][0].endswith("opencode.cmd")


def test_is_opencode_installed_logs_probe_failures(caplog):
    with patch(
        "researchclaw.cli.shutil.which",
        return_value=r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd",
    ), patch(
        "researchclaw.cli.subprocess.run",
        side_effect=PermissionError("permission denied"),
    ):
        with caplog.at_level("WARNING", logger="researchclaw.cli"):
            assert cli._is_opencode_installed() is False

    assert "OpenCode version probe failed" in caplog.text
    assert "permission denied" in caplog.text


def test_module_entrypoint_defaults_pythonioencoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    def fake_main() -> int:
        assert os.environ["PYTHONIOENCODING"] == "utf-8"
        return 0

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("researchclaw.__main__", run_name="__main__")

    assert exc.value.code == 0


def test_module_entrypoint_preserves_existing_pythonioencoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "gb18030")

    def fake_main() -> int:
        assert os.environ["PYTHONIOENCODING"] == "gb18030"
        return 0

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("researchclaw.__main__", run_name="__main__")

    assert exc.value.code == 0

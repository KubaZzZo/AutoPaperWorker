"""Tests for test infrastructure configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _load_pyproject() -> dict[str, object]:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def test_dev_dependencies_include_pytest_cov() -> None:
    pyproject = _load_pyproject()
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dep.startswith("pytest-cov") for dep in dev_deps)


def test_pytest_addopts_enable_terminal_coverage_report() -> None:
    pyproject = _load_pyproject()
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]

    assert "--cov=researchclaw" in addopts
    assert "--cov-report=term-missing:skip-covered" in addopts
    assert "--cov-report=html" in addopts

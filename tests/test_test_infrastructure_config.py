"""Tests for test infrastructure configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def _load_pyproject() -> dict[str, Any]:
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


def test_pytest_registers_layering_markers() -> None:
    pyproject = _load_pyproject()
    markers = pyproject["tool"]["pytest"]["ini_options"].get("markers", [])

    assert any(marker.startswith("integration:") for marker in markers)
    assert any(marker.startswith("slow:") for marker in markers)
    assert any(marker.startswith("live_api:") for marker in markers)


def test_dev_dependencies_include_lint_and_type_tools() -> None:
    pyproject = _load_pyproject()
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dep.startswith("ruff") for dep in dev_deps)
    assert any(dep.startswith("mypy") for dep in dev_deps)


def test_ruff_configuration_matches_project_python_version() -> None:
    pyproject = _load_pyproject()
    ruff = pyproject["tool"]["ruff"]
    lint = ruff["lint"]

    assert ruff["target-version"] == "py311"
    assert ruff["line-length"] == 100
    assert lint["select"] == ["E", "F", "I", "N", "W", "UP", "B", "SIM"]


def test_mypy_configuration_matches_project_python_version() -> None:
    pyproject = _load_pyproject()
    mypy = pyproject["tool"]["mypy"]

    assert mypy["python_version"] == "3.11"
    assert mypy["warn_return_any"] is True
    assert mypy["warn_unused_configs"] is True

def _repo_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def test_dev_dependencies_include_security_scanner() -> None:
    pyproject = _load_pyproject()
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dep.startswith("bandit") for dep in dev_deps)


def test_ci_workflow_runs_lint_tests_and_coverage() -> None:
    workflow_path = _repo_path(".github", "workflows", "ci.yml")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "ruff check" in workflow
    assert "pytest -m \"not integration\"" in workflow
    assert "--cov-report=xml" in workflow
    assert "actions/upload-artifact" in workflow


def test_security_workflow_runs_bandit_scan() -> None:
    workflow_path = _repo_path(".github", "workflows", "security.yml")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "schedule:" in workflow
    assert "bandit" in workflow
    assert "bandit-report" in workflow
    assert "actions/upload-artifact" in workflow


def test_contributing_guide_documents_development_workflow() -> None:
    guide = _repo_path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "pip install -e \".[dev]\"" in guide
    assert "ruff check" in guide
    assert "mypy" in guide
    assert "pytest" in guide
    assert "CHANGELOG.md" in guide


def test_changelog_exists_and_follows_keep_a_changelog_shape() -> None:
    changelog = _repo_path("CHANGELOG.md").read_text(encoding="utf-8")

    assert "# Changelog" in changelog
    assert "## [Unreleased]" in changelog
    assert "### Added" in changelog
    assert "Keep a Changelog" in changelog

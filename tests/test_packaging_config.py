"""Packaging configuration tests."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_wheel_packages_exist_in_source_tree() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages
    missing = [package for package in packages if not (pyproject_path.parent / package).is_dir()]
    assert missing == []

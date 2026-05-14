"""README metadata and encoding checks."""

from __future__ import annotations

from pathlib import Path


def test_readme_declares_and_decodes_as_utf8() -> None:
    readme = Path("README.md")
    raw = readme.read_bytes()
    text = raw.decode("utf-8")
    first_lines = "\n".join(text.splitlines()[:5]).lower()
    assert "charset" in first_lines
    assert "utf-8" in first_lines

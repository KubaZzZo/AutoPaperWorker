from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rc_executor_source_has_no_known_mojibake_markers() -> None:
    source = (ROOT / "tests/test_rc_executor.py").read_text(encoding="utf-8")

    assert "鈥?" not in source
    assert "卤" not in source

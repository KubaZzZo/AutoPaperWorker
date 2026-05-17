from __future__ import annotations

from pathlib import Path

import pytest


UTF8_HEADER_FILES = [
    Path("researchclaw/workbench/cs_project.py"),
    Path("researchclaw/workbench/cnki_import.py"),
    Path("researchclaw/gui/app.py"),
    Path("researchclaw/voice/commands.py"),
    Path("researchclaw/server/dialog/intents.py"),
]


@pytest.mark.parametrize("path", UTF8_HEADER_FILES)
def test_chinese_source_files_declare_utf8_encoding(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# -*- coding: utf-8 -*-"

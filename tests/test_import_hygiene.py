"""Import hygiene checks for stage and configuration modules."""

from __future__ import annotations

import ast
from pathlib import Path


MODULES_WITH_TOP_LEVEL_STDLIB_IMPORTS = [
    Path("researchclaw/pipeline/stage_impls/_analysis.py"),
    Path("researchclaw/pipeline/stage_impls/_publish.py"),
    Path("researchclaw/config/parsing.py"),
    Path("researchclaw/cli.py"),
]
STDLIB_IMPORTS_TO_KEEP_TOP_LEVEL = {"logging", "math", "random", "re", "statistics"}


def test_common_stdlib_imports_stay_at_module_top_level() -> None:
    offenders: list[str] = []
    for path in MODULES_WITH_TOP_LEVEL_STDLIB_IMPORTS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        module_name = alias.name.split(".", 1)[0]
                        if module_name in STDLIB_IMPORTS_TO_KEEP_TOP_LEVEL:
                            offenders.append(f"{path}:{child.lineno}: import {alias.name}")
                elif isinstance(child, ast.ImportFrom) and child.module:
                    module_name = child.module.split(".", 1)[0]
                    if module_name in STDLIB_IMPORTS_TO_KEEP_TOP_LEVEL:
                        offenders.append(f"{path}:{child.lineno}: from {child.module}")

    assert offenders == []

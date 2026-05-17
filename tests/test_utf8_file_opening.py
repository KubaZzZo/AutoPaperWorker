from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


UTF8_JSON_OPEN_CALLS = {
    Path("scripts/test_codegen_v2.py"): {620},
    Path("tests/e2e_real_llm.py"): {32},
    Path("researchclaw/dashboard/collector.py"): {129, 148, 171},
    Path("researchclaw/server/dialog/session.py"): {100},
    Path("researchclaw/server/routes/pipeline.py"): {38},
    Path("researchclaw/server/routes/projects.py"): {30},
}


def test_json_open_calls_use_explicit_utf8_encoding() -> None:
    """JSON/config reads must not depend on the platform default encoding."""
    missing: list[str] = []

    for relative_path, lines in UTF8_JSON_OPEN_CALLS.items():
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node.lineno not in lines:
                continue
            if _is_file_open_call(node) and not _has_utf8_encoding(node):
                missing.append(f"{relative_path}:{node.lineno}")

    assert not missing, "Missing explicit UTF-8 encoding: " + ", ".join(missing)


def _is_file_open_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "open"
    return isinstance(func, ast.Attribute) and func.attr == "open"


def _has_utf8_encoding(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "encoding":
            continue
        value = keyword.value
        return isinstance(value, ast.Constant) and value.value == "utf-8"
    return False

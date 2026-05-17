from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FILES_WITH_UTF8_BYTE_STREAMS = (
    Path("researchclaw/llm/client.py"),
    Path("researchclaw/servers/ssh_executor.py"),
    Path("researchclaw/servers/slurm_executor.py"),
    Path("researchclaw/servers/monitor.py"),
    Path("researchclaw/mcp/transport.py"),
    Path("researchclaw/workbench/remote.py"),
)


def test_byte_stream_decodes_use_explicit_utf8() -> None:
    """Remote/process/SSE byte streams must not depend on platform defaults."""
    missing: list[str] = []

    for relative_path in FILES_WITH_UTF8_BYTE_STREAMS:
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_decode_call(node) and not _has_utf8_encoding(node):
                missing.append(f"{relative_path}:{node.lineno}")

    assert not missing, "Missing explicit UTF-8 decode encoding: " + ", ".join(missing)


def _is_decode_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "decode"


def _has_utf8_encoding(node: ast.Call) -> bool:
    if node.args:
        value = node.args[0]
        return isinstance(value, ast.Constant) and value.value == "utf-8"
    for keyword in node.keywords:
        if keyword.arg == "encoding":
            value = keyword.value
            return isinstance(value, ast.Constant) and value.value == "utf-8"
    return False

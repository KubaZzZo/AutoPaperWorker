"""LLM code block extraction helpers for pipeline stages."""

from __future__ import annotations

import re


def extract_code_block(content: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)\s*```", content, flags=re.DOTALL)
    if match is not None:
        return match.group(1).strip()
    return content.strip()


_MULTI_FILE_PATTERNS = [
    re.compile(
        r"```(?:python\s+)?filename:(\S+)\s*\n(.*?)```",
        flags=re.DOTALL,
    ),
    re.compile(
        r"```\s+filename:(\S+)\s*\n(.*?)```",
        flags=re.DOTALL,
    ),
    re.compile(
        r"```(?:python)?\s*\nfilename:(\S+)\s*\n(.*?)```",
        flags=re.DOTALL,
    ),
    re.compile(
        r"```(?:python)?\s*\n#\s*(?:FILE|filename)\s*:\s*(\S+\.py)\s*\n(.*?)```",
        flags=re.DOTALL,
    ),
]


def extract_multi_file_blocks(content: str) -> dict[str, str]:
    """Parse an LLM response containing Python files with filename markers."""
    matches: list[tuple[str, str]] = []
    for pattern in _MULTI_FILE_PATTERNS:
        matches.extend(pattern.findall(content))

    if matches:
        files: dict[str, str] = {}
        for fname, code in matches:
            fname = fname.strip()
            if ".." in fname or fname.startswith("/"):
                continue
            fname = fname.replace("\\", "/").split("/")[-1]
            if fname and fname.endswith(".py"):
                files[fname] = code.strip()
        if files:
            if "main.py" not in files:
                first_key = next(iter(files))
                files["main.py"] = files.pop(first_key)
            return files
        return {}

    code = extract_code_block(content)
    if code.strip():
        return {"main.py": code}
    return {}

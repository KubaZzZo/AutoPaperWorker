"""Parsing helpers shared by pipeline stages."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("researchclaw.pipeline._helpers")


def extract_yaml_block(text: str) -> str:
    """Extract YAML from text that may contain ACP noise."""
    cleaned = re.sub(
        r"\[thinking\].*?(?=\n```|\n[A-Z]|\Z)",
        "",
        text,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"\[plan\].*?\n\n", "", cleaned, flags=re.DOTALL)

    if "```yaml" in cleaned:
        return cleaned.split("```yaml", 1)[1].split("```", 1)[0].strip()
    if "```yml" in cleaned:
        return cleaned.split("```yml", 1)[1].split("```", 1)[0].strip()
    if "```" in cleaned:
        block = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
        if block:
            return block

    if "```yaml" in text:
        return text.split("```yaml", 1)[1].split("```", 1)[0].strip()
    if "```yml" in text:
        return text.split("```yml", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        block = text.split("```", 1)[1].split("```", 1)[0].strip()
        if block:
            return block

    yaml_lines: list[str] = []
    in_yaml = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not in_yaml and re.match(r"^[a-z_]+:", stripped):
            in_yaml = True
        if in_yaml:
            if stripped and not stripped.startswith("#"):
                yaml_lines.append(line)
            elif not stripped and yaml_lines:
                yaml_lines.append(line)
    if yaml_lines:
        return "\n".join(yaml_lines).strip()

    return text.strip()


_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def safe_json_loads(text: str, default: Any) -> Any:
    """Parse JSON from text, handling noisy ACP output."""
    if not text or not text.strip():
        return default

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        logger.debug(
            "Failed to parse JSON directly from LLM text: %s",
            exc,
            exc_info=True,
        )

    for match in _JSON_FENCE_PATTERN.finditer(text):
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                "Failed to parse fenced JSON candidate: %s",
                exc,
                exc_info=True,
            )

    brace_depth = 0
    start = -1
    candidates: list[str] = []
    for i, ch in enumerate(text):
        if ch == "{":
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and start >= 0:
                candidates.append(text[start : i + 1])
                start = -1

    candidates.sort(key=len, reverse=True)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue

    bracket_depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "[":
            if bracket_depth == 0:
                start = i
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
            if bracket_depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                    if isinstance(parsed, list):
                        return parsed
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.debug(
                        "Failed to parse bracketed JSON candidate: %s",
                        exc,
                        exc_info=True,
                    )
                start = -1

    return default


def parse_jsonl_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = safe_json_loads(line, {})
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

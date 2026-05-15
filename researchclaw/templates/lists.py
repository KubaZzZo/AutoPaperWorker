"""List collection and rendering helpers for Markdown-to-LaTeX conversion."""

from __future__ import annotations

import re
from collections.abc import Callable


InlineConverter = Callable[[str], str]


def _collect_list(
    lines: list[str], start: int, pattern: re.Pattern[str]
) -> tuple[list[str], int]:
    """Collect consecutive list items matching *pattern*."""
    items: list[str] = []
    i = start
    while i < len(lines):
        match = pattern.match(lines[i])
        if match:
            items.append(match.group(2))
            i += 1
        elif lines[i].strip() == "":
            if i + 1 < len(lines) and pattern.match(lines[i + 1]):
                i += 1
            else:
                break
        elif lines[i].startswith("  ") or lines[i].startswith("\t"):
            if items:
                items[-1] += " " + lines[i].strip()
            i += 1
        else:
            break
    return items, i


def _render_itemize(
    items: list[str],
    *,
    inline_converter: InlineConverter,
) -> str:
    inner = "\n".join(f"  \\item {inline_converter(item)}" for item in items)
    return f"\\begin{{itemize}}\n{inner}\n\\end{{itemize}}"


def _render_enumerate(
    items: list[str],
    *,
    inline_converter: InlineConverter,
) -> str:
    inner = "\n".join(f"  \\item {inline_converter(item)}" for item in items)
    return f"\\begin{{enumerate}}\n{inner}\n\\end{{enumerate}}"

"""Body rendering helpers for Markdown-to-LaTeX conversion."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from researchclaw.templates.codeblocks import _render_code_block
from researchclaw.templates.figures import _render_figure
from researchclaw.templates.inline import _convert_inline, _escape_latex
from researchclaw.templates.lists import (
    _collect_list,
    _render_enumerate,
    _render_itemize,
)
from researchclaw.templates.sections import _Section
from researchclaw.templates.tables import _collect_table, _render_table

_SKIP_HEADINGS = {"title", "abstract"}

Counter = Callable[[], int]


def _build_body(
    sections: list[_Section],
    *,
    title: str = "",
    next_table_num: Counter | None = None,
    next_figure_num: Counter | None = None,
) -> str:
    """Convert all non-title/abstract sections to LaTeX body text."""
    title_lower = title.strip().lower()

    title_h1_found = any(
        sec.level == 1
        and sec.heading
        and sec.heading.strip().lower() == title_lower
        for sec in sections
    )

    body_levels: set[int] = set()
    for sec in sections:
        if sec.heading_lower not in _SKIP_HEADINGS and sec.level >= 1:
            if not (sec.level == 1 and sec.heading.strip().lower() == title_lower):
                body_levels.add(sec.level)

    min_body_level = min(body_levels) if body_levels else 1
    if title_h1_found:
        level_offset = 1 if min_body_level >= 2 else 0
    elif min_body_level >= 2:
        level_offset = min_body_level - 1
    else:
        level_offset = 0

    level_map = {
        1: "section",
        2: "subsection",
        3: "subsubsection",
        4: "paragraph",
    }

    parts: list[str] = []
    for sec in sections:
        if sec.heading_lower in _SKIP_HEADINGS:
            continue
        if (
            sec.level == 1
            and sec.heading
            and sec.heading.strip().lower() == title_lower
        ):
            continue
        if sec.level == 0:
            parts.append(
                _convert_block(
                    sec.body,
                    next_table_num=next_table_num,
                    next_figure_num=next_figure_num,
                )
            )
            continue

        effective_level = max(1, sec.level - level_offset)
        cmd = level_map.get(effective_level, "paragraph")
        heading_tex = _escape_latex(sec.heading)
        heading_tex = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading_tex)
        parts.append(f"\\{cmd}{{{heading_tex}}}")
        if cmd in ("section", "subsection", "subsubsection"):
            label_key = re.sub(r"[^a-z0-9]+", "_", heading_tex.lower()).strip("_")[:40]
            if label_key:
                parts.append(f"\\label{{sec:{label_key}}}")
        if sec.body:
            parts.append(
                _convert_block(
                    sec.body,
                    next_table_num=next_table_num,
                    next_figure_num=next_figure_num,
                )
            )

    return "\n\n".join(parts) + "\n"


def _deduplicate_tables(body: str) -> str:
    """Remove duplicate tables that share the same header row."""
    table_env_re = re.compile(
        r"(\\begin\{table\}.*?\\end\{table\})", re.DOTALL
    )
    tables = list(table_env_re.finditer(body))
    if len(tables) < 2:
        return body

    seen_headers: dict[str, int] = {}
    drop_spans: list[tuple[int, int]] = []
    for match in tables:
        table_text = match.group(1)
        header_match = re.search(r"\\toprule\s*\n(.+?)\\\\", table_text)
        if not header_match:
            continue
        header_key = re.sub(r"\s+", " ", header_match.group(1).strip())
        if header_key in seen_headers:
            drop_spans.append((match.start(), match.end()))
            logging.getLogger(__name__).info(
                "IMP-30: Dropping duplicate table (same header as table #%d)",
                seen_headers[header_key],
            )
        else:
            seen_headers[header_key] = len(seen_headers) + 1

    for start, end in reversed(drop_spans):
        body = body[:start] + body[end:]

    return body


_DISPLAY_MATH_RE = re.compile(r"^\\\[(.+?)\\\]$", re.MULTILINE | re.DOTALL)
_DISPLAY_MATH_DOLLAR_RE = re.compile(
    r"^\$\$\s*\n?(.*?)\n?\s*\$\$$", re.MULTILINE | re.DOTALL
)
_FENCED_CODE_RE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_TABLE_SEP_RE = re.compile(r"^\|[-:| ]+\|$")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_BULLET_RE = re.compile(r"^(\s*)-\s+(.+)")
_NUMBERED_RE = re.compile(r"^(\s*)\d+\.\s+(.+)")


def _convert_block(
    text: str,
    *,
    next_table_num: Counter | None = None,
    next_figure_num: Counter | None = None,
) -> str:
    """Convert a block of Markdown body text to LaTeX."""
    next_table_num = next_table_num or (lambda: 1)
    next_figure_num = next_figure_num or (lambda: 1)
    math_blocks: list[str] = []

    def _stash_math(match: re.Match[str]) -> str:
        idx = len(math_blocks)
        math_blocks.append(match.group(0))
        return f"%%MATH_BLOCK_{idx}%%"

    def _stash_dollar_math(match: re.Match[str]) -> str:
        idx = len(math_blocks)
        inner = match.group(1).strip()
        math_blocks.append(
            f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
        )
        return f"%%MATH_BLOCK_{idx}%%"

    text = _DISPLAY_MATH_RE.sub(_stash_math, text)
    text = _DISPLAY_MATH_DOLLAR_RE.sub(_stash_dollar_math, text)

    code_blocks: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        idx = len(code_blocks)
        lang = match.group(1) or ""
        code = match.group(2)
        code_blocks.append(_render_code_block(lang, code, inline_converter=_convert_inline))
        return f"%%CODE_BLOCK_{idx}%%"

    text = _FENCED_CODE_RE.sub(_stash_code, text)

    latex_env_blocks: list[str] = []

    def _stash_latex_env(match: re.Match[str]) -> str:
        idx = len(latex_env_blocks)
        latex_env_blocks.append(match.group(0))
        return f"%%LATEX_ENV_{idx}%%"

    text = re.sub(
        r"\\begin\{(table|figure|tabular|algorithm|algorithmic|equation|align"
        r"|gather|multline|minipage|tikzpicture)\*?\}.*?"
        r"\\end\{\1\*?\}",
        _stash_latex_env,
        text,
        flags=re.DOTALL,
    )

    lines = text.split("\n")
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("%%MATH_BLOCK_"):
            idx = int(re.search(r"\d+", line.strip()).group())  # type: ignore[union-attr]
            output.append(math_blocks[idx])
            i += 1
            continue

        if line.strip().startswith("%%CODE_BLOCK_"):
            idx = int(re.search(r"\d+", line.strip()).group())  # type: ignore[union-attr]
            output.append(code_blocks[idx])
            i += 1
            continue

        if line.strip().startswith("%%LATEX_ENV_"):
            idx = int(re.search(r"\d+", line.strip()).group())  # type: ignore[union-attr]
            output.append(latex_env_blocks[idx])
            i += 1
            continue

        if _BULLET_RE.match(line):
            items, i = _collect_list(lines, i, _BULLET_RE)
            output.append(_render_itemize(items, inline_converter=_convert_inline))
            continue

        if _NUMBERED_RE.match(line):
            items, i = _collect_list(lines, i, _NUMBERED_RE)
            output.append(_render_enumerate(items, inline_converter=_convert_inline))
            continue

        if (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1].strip())
        ):
            table_caption = ""
            if output:
                prev = output[-1].strip()
                cap_m = re.match(
                    r"(?:\\textbf\{|[*]{2})\s*Table\s+\d+[.:]?\s*(.*?)(?:\}|[*]{2})$",
                    prev,
                )
                if cap_m:
                    table_caption = f"Table {cap_m.group(1)}" if cap_m.group(1) else ""
                    if not table_caption:
                        table_caption = prev
                    output.pop()
            table_lines, i = _collect_table(lines, i)
            output.append(
                _render_table(
                    table_lines,
                    table_caption,
                    inline_converter=_convert_inline,
                    next_table_num=next_table_num,
                )
            )
            continue

        img_match = _IMAGE_RE.match(line.strip())
        if img_match:
            output.append(
                _render_figure(
                    img_match.group(1),
                    img_match.group(2),
                    inline_converter=_convert_inline,
                    next_figure_num=next_figure_num,
                )
            )
            i += 1
            continue

        output.append(_convert_inline(line))
        i += 1

    return "\n".join(output)

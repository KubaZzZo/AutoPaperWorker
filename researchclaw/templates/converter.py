"""Markdown-to-LaTeX converter with conference template support.

Converts a ResearchClaw paper (Markdown with embedded LaTeX math) into a
complete ``.tex`` file using a :class:`ConferenceTemplate` for preamble,
author block, bibliography style, and document structure.

Design constraints:
- **Zero new dependencies** — stdlib only (``re``, ``textwrap``).
- Handles inline math ``\\(...\\)``, display math ``\\[...\\]``,
  bold/italic, bullet lists, numbered lists, code blocks, tables,
  and ``\\cite{...}`` references.
- Extracts abstract from ``# Abstract`` or ``## Abstract`` section.
- ICML two-column structure handled via template's ``render_preamble``.
"""

from __future__ import annotations

import re
import threading

from researchclaw.templates.body import (
    _build_body as _build_body_impl,
    _convert_block as _convert_block_impl,
    _deduplicate_tables,
)
from researchclaw.templates.codeblocks import (
    _UNICODE_TO_ASCII,
    _escape_algo_line as _escape_algo_line_impl,
    _render_code_block as _render_code_block_impl,
)
from researchclaw.templates.completeness import check_paper_completeness
from researchclaw.templates.conference import ConferenceTemplate
from researchclaw.templates.document import markdown_to_latex as _markdown_to_latex_impl
from researchclaw.templates.figures import _render_figure as _render_figure_impl
from researchclaw.templates.inline import (
    _convert_inline,
    _escape_latex,
)
from researchclaw.templates.lists import (
    _collect_list as _collect_list_impl,
    _render_enumerate as _render_enumerate_impl,
    _render_itemize as _render_itemize_impl,
)
from researchclaw.templates.preprocessing import (
    _preprocess_markdown,
    _round_raw_metrics,
)
from researchclaw.templates.sections import (
    _Section,
    _extract_abstract,
    _extract_title,
    _parse_sections,
    _separate_heading_body,
)
from researchclaw.templates.sanitization import (
    _normalize_latex_unicode,
    _sanitize_latex_output,
)
from researchclaw.templates.tables import (
    _parse_alignments,
    _parse_table_row,
    _render_table as _render_table_impl,
)

_render_counters = threading.local()


def _reset_render_counters() -> None:
    """Reset per-render figure and table counters for the current thread."""
    _render_counters.table = 0
    _render_counters.figure = 0


def _next_table_num() -> int:
    """Return the next table number for the current thread."""
    next_num = getattr(_render_counters, "table", 0) + 1
    _render_counters.table = next_num
    return next_num


def _next_figure_num() -> int:
    """Return the next figure number for the current thread."""
    next_num = getattr(_render_counters, "figure", 0) + 1
    _render_counters.figure = next_num
    return next_num

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def markdown_to_latex(
    paper_md: str,
    template: ConferenceTemplate,
    *,
    title: str = "",
    authors: str = "Anonymous",
    bib_file: str = "references",
    bib_entries: dict[str, str] | None = None,
) -> str:
    """Convert a Markdown paper to a complete LaTeX document."""
    return _markdown_to_latex_impl(
        paper_md,
        template,
        title=title,
        authors=authors,
        bib_file=bib_file,
        bib_entries=bib_entries,
        reset_render_counters=_reset_render_counters,
        next_table_num=_next_table_num,
        next_figure_num=_next_figure_num,
    )


# ---------------------------------------------------------------------------
# Post-processing: sanitize final LaTeX
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pre-processing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Body rendering
# ---------------------------------------------------------------------------


def _build_body(sections: list[_Section], *, title: str = "") -> str:
    return _build_body_impl(
        sections,
        title=title,
        next_table_num=_next_table_num,
        next_figure_num=_next_figure_num,
    )


def _convert_block(text: str) -> str:
    return _convert_block_impl(
        text,
        next_table_num=_next_table_num,
        next_figure_num=_next_figure_num,
    )


# ---------------------------------------------------------------------------
# List handling
# ---------------------------------------------------------------------------


def _collect_list(
    lines: list[str], start: int, pattern: re.Pattern[str]
) -> tuple[list[str], int]:
    return _collect_list_impl(lines, start, pattern)


def _render_itemize(items: list[str]) -> str:
    return _render_itemize_impl(items, inline_converter=_convert_inline)


def _render_enumerate(items: list[str]) -> str:
    return _render_enumerate_impl(items, inline_converter=_convert_inline)


def _render_table(table_lines: list[str], caption: str = "") -> str:
    return _render_table_impl(
        table_lines,
        caption,
        inline_converter=_convert_inline,
        next_table_num=_next_table_num,
    )


def _escape_algo_line(line: str) -> str:
    return _escape_algo_line_impl(line)


def _render_code_block(lang: str, code: str) -> str:
    return _render_code_block_impl(
        lang,
        code,
        inline_converter=_convert_inline,
    )


# ---------------------------------------------------------------------------
# Figure rendering
# ---------------------------------------------------------------------------

def _render_figure(caption: str, path: str) -> str:
    return _render_figure_impl(
        caption,
        path,
        inline_converter=_convert_inline,
        next_figure_num=_next_figure_num,
    )

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
    """Convert a Markdown paper to a complete LaTeX document.

    Parameters
    ----------
    paper_md:
        Full paper in Markdown with embedded LaTeX math.
    template:
        Conference template controlling preamble and structure.
    title:
        Paper title.  If empty, extracted from ``# Title`` heading or the
        first ``# ...`` heading in *paper_md*.
    authors:
        Author string inserted into the template author block.
    bib_file:
        Bibliography filename (without ``.bib`` extension).
    bib_entries:
        Optional mapping of author-year patterns to cite_keys for
        recovering author-year citations that slipped through earlier
        processing, e.g. ``{"Raissi et al., 2019": "raissi2019physics"}``.

    Returns
    -------
    str
        A complete ``.tex`` file ready for compilation.
    """
    _reset_render_counters()

    paper_md = _preprocess_markdown(paper_md)
    paper_md = _round_raw_metrics(paper_md)
    sections = _parse_sections(paper_md)

    # Extract title from first H1 heading if not provided
    if not title:
        title = _extract_title(sections, paper_md)

    # Extract abstract
    abstract = _extract_abstract(sections)

    # Build body (everything except title/abstract headings)
    body = _build_body(sections, title=title)

    # IMP-30: Detect and remove duplicate tables
    body = _deduplicate_tables(body)

    # R10-Fix5: Completeness check
    completeness_warnings = check_paper_completeness(sections)
    if completeness_warnings:
        import logging

        _logger = logging.getLogger(__name__)
        for warning in completeness_warnings:
            _logger.warning("LaTeX completeness check: %s", warning)
        # BUG-28: Log warnings only — don't inject comments into LaTeX body

    preamble = template.render_preamble(
        title=_escape_latex(title),
        authors=authors,
        abstract=_convert_inline(abstract),
    )
    footer = template.render_footer(bib_file)

    tex = preamble + "\n" + body + footer

    # Final sanitization pass on the complete LaTeX output
    tex = _sanitize_latex_output(tex, bib_entries=bib_entries)

    return tex


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

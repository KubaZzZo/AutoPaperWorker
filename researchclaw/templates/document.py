"""Document assembly for Markdown-to-LaTeX conversion."""

from __future__ import annotations

import logging
from collections.abc import Callable

from researchclaw.templates.body import _build_body, _deduplicate_tables
from researchclaw.templates.completeness import check_paper_completeness
from researchclaw.templates.conference import ConferenceTemplate
from researchclaw.templates.inline import _convert_inline, _escape_latex
from researchclaw.templates.preprocessing import _preprocess_markdown, _round_raw_metrics
from researchclaw.templates.sanitization import _sanitize_latex_output
from researchclaw.templates.sections import (
    _extract_abstract,
    _extract_title,
    _parse_sections,
)


CounterReset = Callable[[], None]
Counter = Callable[[], int]


def markdown_to_latex(
    paper_md: str,
    template: ConferenceTemplate,
    *,
    title: str = "",
    authors: str = "Anonymous",
    bib_file: str = "references",
    bib_entries: dict[str, str] | None = None,
    reset_render_counters: CounterReset | None = None,
    next_table_num: Counter | None = None,
    next_figure_num: Counter | None = None,
) -> str:
    """Convert a Markdown paper to a complete LaTeX document."""
    if reset_render_counters:
        reset_render_counters()

    paper_md = _preprocess_markdown(paper_md)
    paper_md = _round_raw_metrics(paper_md)
    sections = _parse_sections(paper_md)

    if not title:
        title = _extract_title(sections, paper_md)

    abstract = _extract_abstract(sections)

    body = _build_body(
        sections,
        title=title,
        next_table_num=next_table_num,
        next_figure_num=next_figure_num,
    )
    body = _deduplicate_tables(body)

    completeness_warnings = check_paper_completeness(sections)
    if completeness_warnings:
        logger = logging.getLogger(__name__)
        for warning in completeness_warnings:
            logger.warning("LaTeX completeness check: %s", warning)

    preamble = template.render_preamble(
        title=_escape_latex(title),
        authors=authors,
        abstract=_convert_inline(abstract),
    )
    footer = template.render_footer(bib_file)

    tex = preamble + "\n" + body + footer
    return _sanitize_latex_output(tex, bib_entries=bib_entries)

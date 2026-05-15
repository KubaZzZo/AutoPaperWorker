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
import textwrap
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


_OUTER_FENCE_RE = re.compile(
    r"^\s*```(?:markdown|md|latex|tex)?\s*\n(.*?)^\s*```\s*$",
    re.MULTILINE | re.DOTALL,
)

# Greedy variant — matches the *last* closing fence so inner code blocks
# (```text … ```) don't truncate the capture prematurely.
_OUTER_FENCE_GREEDY_RE = re.compile(
    r"^\s*```(?:markdown|md|latex|tex)?\s*\n(.*)^\s*```\s*$",
    re.MULTILINE | re.DOTALL,
)

# Pattern for raw metric values with excessive decimal places
# e.g. 0.9717036975193437 → 0.972
_RAW_METRIC_RE = re.compile(r"(\d+\.\d{5,})")


def _round_raw_metrics(text: str) -> str:
    """Round excessively precise metric values (>4 decimal places).

    Uses significant-figure-aware rounding so small values like
    learning rates (e.g. 0.00001) are preserved instead of becoming 0.0000.
    """
    def _rounder(m: re.Match[str]) -> str:
        try:
            val = float(m.group(1))
            if val == 0.0:
                return "0.0"
            # For very small values (< 0.001), use 2 significant figures
            # to preserve scientific meaning (e.g. lr=0.00003 → 0.00003)
            import math
            abs_val = abs(val)
            if abs_val < 0.001:
                sig_figs = 2
                digits = sig_figs - int(math.floor(math.log10(abs_val))) - 1
                return f"{val:.{digits}f}"
            # Normal range: 4 decimal places
            return f"{val:.4f}"
        except (ValueError, OverflowError):
            return m.group(0)
    return _RAW_METRIC_RE.sub(_rounder, text)


def _preprocess_markdown(md: str) -> str:
    """Clean up common LLM artifacts before parsing.

    1. Strip outer fenced code blocks (e.g. triple-backtick markdown) that LLMs
       around the entire paper content.
    2. Remove standalone Markdown horizontal rules (``---``, ``***``, ``___``).
    3. Convert blockquotes (``> text``) to a form the converter can handle.
    4. Round excessively precise metric values.
    """
    text = md

    # 1. Strip outer markdown fences (LLMs sometimes wrap entire paper in them)
    #    Repeatedly strip in case of double-wrapping.
    #    Try greedy match first (handles papers with inner code blocks),
    #    then fall back to non-greedy if greedy doesn't help.
    for _ in range(3):
        stripped = False
        for pat in (_OUTER_FENCE_GREEDY_RE, _OUTER_FENCE_RE):
            m = pat.search(text)
            if m and len(m.group(1)) > len(text) * 0.5:
                text = m.group(1)
                stripped = True
                break
        if not stripped:
            # Also handle the case where the first line is ```markdown
            # and the last non-blank line is ``` (simple boundary strip)
            lines = text.split("\n")
            first = lines[0].strip() if lines else ""
            last_idx = len(lines) - 1
            while last_idx > 0 and not lines[last_idx].strip():
                last_idx -= 1
            last = lines[last_idx].strip() if last_idx > 0 else ""
            if (
                re.match(r"^```(?:markdown|md|latex|tex)?\s*$", first)
                and last == "```"
            ):
                text = "\n".join(lines[1:last_idx])
                stripped = True
        if not stripped:
            break

    # 2. Remove standalone horizontal rules (---, ***, ___)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # 2a. Strip HTML entities that LLMs inject into markdown
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&mdash;", "---")
    text = text.replace("&ndash;", "--")

    # 2b. Note: stray code fences are handled in _sanitize_latex_output
    #     after conversion, not here (to avoid breaking real code blocks).

    # 2c. Round excessively precise metric values (e.g. 0.9717036975 → 0.9717)
    text = _round_raw_metrics(text)

    # 2d. Remove raw \texttt{...} or backtick-wrapped metric key paths
    # Pattern: \texttt{some/long/metric_path/name: 0.1234} or `path/to/metric: val`
    text = re.sub(
        r"\\texttt\{[a-zA-Z0-9_/.:=-]+(?:/[a-zA-Z0-9_/.:=-]+){2,}(?:\s*[=:]\s*[^}]*)?\}",
        "",
        text,
    )
    # Also strip backtick-wrapped metric paths in markdown source
    text = re.sub(
        r"`[a-zA-Z0-9_/.-]+(?:/[a-zA-Z0-9_/.-]+){2,}(?:\s*[=:]\s*[^`]*)?`",
        "",
        text,
    )

    # 2e. Clean NOT_IN_BIB citation markers: [?key:NOT_IN_BIB] → remove
    text = re.sub(r"\[\?[a-zA-Z0-9_:-]+:NOT_IN_BIB\]", "", text)

    # 3. Convert blockquotes: > text → \begin{quote}text\end{quote}
    #    Collect consecutive > lines into a single quote block.
    lines = text.split("\n")
    out_lines: list[str] = []
    in_quote = False
    quote_buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> "):
            if not in_quote:
                in_quote = True
                quote_buf = []
            quote_buf.append(stripped[2:])
        elif stripped == ">" and in_quote:
            quote_buf.append("")
        else:
            if in_quote:
                out_lines.append("\\begin{quote}")
                out_lines.extend(quote_buf)
                out_lines.append("\\end{quote}")
                in_quote = False
                quote_buf = []
            out_lines.append(line)
    if in_quote:
        out_lines.append("\\begin{quote}")
        out_lines.extend(quote_buf)
        out_lines.append("\\end{quote}")
    text = "\n".join(out_lines)

    # 4. T1.2: Remove stray markdown/latex/text fences that appear mid-document.
    #    LLMs sometimes emit ```markdown or ```latex between sections.
    #    Only remove documentation fences — preserve code fences (```python etc.)
    _CODE_LANGS = frozenset({
        "python", "java", "cpp", "c", "javascript", "typescript", "rust",
        "go", "ruby", "bash", "sh", "sql", "r", "julia", "lua", "perl",
        "scala", "kotlin", "swift", "haskell", "algorithm", "pseudocode",
    })
    _lines = text.split("\n")
    _cleaned: list[str] = []
    _in_code = False
    for _l in _lines:
        _stripped = _l.strip()
        if _stripped.startswith("```") and not _in_code:
            _lang = _stripped[3:].strip().lower()
            if _lang in _CODE_LANGS or _lang.startswith("algorithm"):
                # Real code block — keep
                _in_code = True
                _cleaned.append(_l)
            elif _lang in ("markdown", "md", "latex", "tex", "text", "", "bibtex"):
                # Documentation/wrapper fence — remove
                pass
            else:
                # Unknown lang — keep to be safe
                _in_code = True
                _cleaned.append(_l)
        elif _stripped == "```" and _in_code:
            # Closing fence for a code block — keep
            _in_code = False
            _cleaned.append(_l)
        elif _stripped == "```" and not _in_code:
            # Stray fence — remove
            pass
        else:
            _cleaned.append(_l)
    text = "\n".join(_cleaned)

    # 5. Normalize mid-line section headings (IMP-17)
    #    LLM output may concatenate sections onto single long lines:
    #      "...text ## Abstract Body text ## 1. Introduction More text..."
    #    Ensure each heading marker starts on its own line so _parse_sections
    #    can detect them with the ^-anchored regex.
    text = re.sub(r"(?<=[^\n]) +(#{1,4}) +", r"\n\n\1 ", text)

    return text


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

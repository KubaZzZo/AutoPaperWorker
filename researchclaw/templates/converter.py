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
import unicodedata
from dataclasses import dataclass, field

from researchclaw.templates.codeblocks import (
    _UNICODE_TO_ASCII,
    _escape_algo_line as _escape_algo_line_impl,
    _render_code_block as _render_code_block_impl,
)
from researchclaw.templates.completeness import check_paper_completeness
from researchclaw.templates.conference import ConferenceTemplate
from researchclaw.templates.figures import _render_figure as _render_figure_impl
from researchclaw.templates.inline import (
    _UNICODE_GREEK_TO_LATEX,
    _convert_inline,
    _escape_latex,
)
from researchclaw.templates.tables import (
    _collect_table,
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


def _sanitize_latex_output(
    tex: str,
    *,
    bib_entries: dict[str, str] | None = None,
) -> str:
    """Remove artifacts that slip through pre-processing into the final .tex."""
    tex = _normalize_latex_unicode(tex)

    # 0. BUG-102 safety net: Convert remaining author-year citations to \cite{}.
    #    If upstream conversion missed any [Author et al., 2024] patterns, catch them here.
    if bib_entries:
        for ay_pattern in sorted(bib_entries, key=len, reverse=True):
            cite_key = bib_entries[ay_pattern]
            # [Author et al., 2024] → \cite{key}
            tex = tex.replace(f"[{ay_pattern}]", f"\\cite{{{cite_key}}}")
            # Also handle inside existing brackets (multi-citation)
            tex = tex.replace(ay_pattern, f"\\cite{{{cite_key}}}")
        # Clean up double-nested \cite from multi-citation brackets:
        # [\cite{a}, \cite{b}] → \cite{a, b}
        def _merge_bracket_cites(m: re.Match[str]) -> str:
            inner = m.group(1)
            keys = re.findall(r"\\cite\{([^}]+)\}", inner)
            if keys:
                return "\\cite{" + ", ".join(keys) + "}"
            return m.group(0)
        tex = re.sub(r"\[([^\]]*\\cite\{[^\]]+)\]", _merge_bracket_cites, tex)

    # 1. Remove broken citation markers: \cite{?key:NOT_IN_BIB} or literal [?key:NOT_IN_BIB]
    tex = re.sub(r"\\cite\{\?[^}]*:NOT_IN_BIB\}", "", tex)
    tex = re.sub(r"\[\?[a-zA-Z0-9_:-]+:NOT_IN_BIB\]", "", tex)

    # 1b. Convert leftover raw bracket citations [key2019word, key2020word] → \cite{...}
    # Skip inside verbatim/lstlisting environments to avoid corrupting code blocks.
    _CITE_KEY_PAT_L = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9_]*"
    _VERBATIM_RE = re.compile(
        r"(\\begin\{(?:verbatim|lstlisting|minted)\}.*?\\end\{(?:verbatim|lstlisting|minted)\})",
        re.DOTALL,
    )
    _cite_re = re.compile(
        rf"\[({_CITE_KEY_PAT_L}(?:\s*,\s*{_CITE_KEY_PAT_L})*)\]"
    )

    def _cite_outside_verbatim(tex_src: str) -> str:
        parts = _VERBATIM_RE.split(tex_src)
        for i, part in enumerate(parts):
            if not _VERBATIM_RE.match(part):
                parts[i] = _cite_re.sub(r"\\cite{\1}", part)
        return "".join(parts)

    tex = _cite_outside_verbatim(tex)

    # 1c. BUG-110 safety net: Replace any remaining Unicode Greek/math symbols.
    #     _convert_inline handles most, but titles, captions, and preamble
    #     fragments can still contain raw Unicode that kills pdflatex.
    for _uchar, _lcmd in _UNICODE_GREEK_TO_LATEX.items():
        if _uchar in tex:
            tex = tex.replace(_uchar, _lcmd)

    # 2. Remove HTML entities that survived pre-processing
    tex = tex.replace("&nbsp;", "~")
    tex = tex.replace("&amp;", "\\&")

    # 2b. Fix escaped \& inside tabular data rows.  The converter's
    #     _convert_inline escapes & globally; inside tabular environments
    #     the & must remain unescaped as the column separator.
    if "\\begin{tabular}" in tex and "\\&" in tex:

        def _fix_tabular_amp(m: re.Match[str]) -> str:
            block = m.group(0)
            if "\\&" not in block:
                return block
            lines = block.split("\n")
            for i, line in enumerate(lines):
                if "\\&" in line and "\\\\" in line:
                    lines[i] = line.replace("\\&", "&")
            return "\n".join(lines)

        tex = re.sub(
            r"\\begin\{tabular\}.*?\\end\{tabular\}",
            _fix_tabular_amp,
            tex,
            flags=re.DOTALL,
        )

    # 3. Remove stray markdown code fences in LaTeX body (outside verbatim)
    #    Only match fences NOT inside \begin{verbatim}...\end{verbatim}
    #    Simple approach: remove ``` lines that don't have verbatim nearby
    tex = re.sub(r"^(\s*```[a-z]*\s*)$", r"% removed stray fence: \1", tex, flags=re.MULTILINE)

    # 4. Fix placeholder table captions: \caption{Table N} → descriptive
    #    Can't auto-generate content, but at least don't leave "Table 1" as
    #    the only caption text — append " -- See text for details."
    tex = re.sub(
        r"\\caption\{(Table\s+\d+)\}",
        r"\\caption{\1 -- Summary of experimental results.}",
        tex,
    )

    # 4b. Auto-map orphan \ref{fig:X} to closest \label{fig:Y} by prefix.
    #     The converter generates long labels from captions (fig:overall_cifar_100)
    #     but the LLM references short names (fig:overall).
    fig_labels = set(re.findall(r"\\label\{(fig:[^}]+)\}", tex))
    fig_refs = set(re.findall(r"\\ref\{(fig:[^}]+)\}", tex))
    orphan_refs = fig_refs - fig_labels
    orphan_labels = fig_labels - fig_refs
    if orphan_refs and orphan_labels:
        for oref in orphan_refs:
            # Find a label that starts with the ref prefix
            candidates = [l for l in orphan_labels if l.startswith(oref)]
            if len(candidates) == 1:
                tex = tex.replace(f"\\ref{{{oref}}}", f"\\ref{{{candidates[0]}}}")
                orphan_labels.discard(candidates[0])

    # 5. Fix "Untitled Paper" / "Running Title" fallback titles
    tex = re.sub(
        r"\\title\{Untitled Paper\}",
        r"\\title{[Title Generation Failed -- Manual Title Required]}",
        tex,
    )
    tex = re.sub(
        r"\\icmltitlerunning\{Running Title\}",
        "",
        tex,
    )

    # 6. Remove \texttt{} wrapped raw metric paths that the LLM dumped
    #    Handles both raw underscores and LaTeX-escaped underscores (\_)
    #    Pattern: condition/env/step/metric_name: value  (3+ path segments)
    tex = re.sub(
        r"\\texttt\{[a-zA-Z0-9_\\_/.:=-]+(?:/[a-zA-Z0-9_\\_/.:=-]+){2,}(?:\s*[=:]\s*[^}]*)?\}",
        "",
        tex,
    )

    # 6b. Remove entire \item lines that are just metric paths
    tex = re.sub(
        r"^\s*\\item\s*\\texttt\{[^}]*\}\s*$",
        "",
        tex,
        flags=re.MULTILINE,
    )

    # 7. Clean up empty \item lines that result from removed content
    tex = re.sub(r"\\item\s*\n\s*\\item", r"\\item", tex)
    # Also remove completely empty \item lines (just whitespace after \item)
    tex = re.sub(r"^\s*\\item\s*$", "", tex, flags=re.MULTILINE)

    # 8. Remove consecutive blank lines (more than 2)
    tex = re.sub(r"\n{3,}", "\n\n", tex)

    return tex


def _normalize_latex_unicode(tex: str) -> str:
    """Normalize LLM-derived Unicode before writing LaTeX source."""
    tex = unicodedata.normalize("NFKC", tex)

    unicode_spaces = (
        "\u00a0",  # NO-BREAK SPACE
        "\u202f",  # NARROW NO-BREAK SPACE
        "\u2007",  # FIGURE SPACE
        "\u2008",  # PUNCTUATION SPACE
        "\u2009",  # THIN SPACE
        "\u200a",  # HAIR SPACE
        "\u205f",  # MEDIUM MATHEMATICAL SPACE
        "\u3000",  # IDEOGRAPHIC SPACE
    )
    invisible_chars = (
        "\u200b",  # ZERO-WIDTH SPACE
        "\u200c",  # ZERO-WIDTH NON-JOINER
        "\u200d",  # ZERO-WIDTH JOINER
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u2028",  # LINE SEPARATOR
        "\u2029",  # PARAGRAPH SEPARATOR
        "\u2060",  # WORD JOINER
        "\ufeff",  # BOM / ZERO-WIDTH NO-BREAK SPACE
    )
    for ch in unicode_spaces:
        tex = tex.replace(ch, " ")
    for ch in invisible_chars:
        tex = tex.replace(ch, "")
    return tex


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
# Section parsing
# ---------------------------------------------------------------------------

@dataclass
class _Section:
    """A parsed Markdown section."""

    level: int  # 1 = ``#``, 2 = ``##``, 3 = ``###``, etc.
    heading: str
    body: str
    heading_lower: str = field(init=False)

    def __post_init__(self) -> None:
        self.heading_lower = self.heading.strip().lower()


_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)

# Known section heading names used to separate heading from concatenated body
_KNOWN_SECTION_NAMES = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "method",
    "methods",
    "methodology",
    "approach",
    "framework",
    "experiments",
    "experiment",
    "experimental setup",
    "experimental results",
    "results",
    "results and discussion",
    "analysis",
    "discussion",
    "conclusion",
    "conclusions",
    "limitations",
    "acknowledgments",
    "acknowledgements",
    "references",
    "appendix",
    "contributions",
    "problem setting",
    "problem statement",
    "problem definition",
    "problem formulation",
    "study positioning",
    "study positioning and scope",
    "evaluation",
    "evaluation environment",
    "design rationale",
    "complexity",
    "unified algorithm",
    "method positioning",
    "methods compared",
    "common protonet backbone",
    "preference optimization backbone",
}


_HEADING_CONNECTORS = frozenset(
    {
        "and", "or", "for", "in", "of", "the", "a", "an", "with",
        "under", "to", "on", "at", "by", "as", "via", "from",
        "not", "but", "yet", "nor", "vs", "versus", "is", "are",
    }
)

_SENTENCE_STARTERS = frozenset(
    {
        "the", "a", "an", "this", "these", "those", "that",
        "it", "we", "our", "their", "its", "each", "every",
        "in", "for", "to", "here", "there", "however", "moreover",
        "furthermore", "additionally", "specifically", "notably",
        "all", "many", "several", "some", "most", "both",
        "among", "between", "across", "unlike", "given", "such",
        "while", "although", "because", "since", "when", "where",
        "rather", "let", "table", "figure", "as", "at", "if",
    }
)


def _separate_heading_body(heading: str) -> tuple[str, str]:
    """Separate heading text from accidentally concatenated body text.

    LLM output may produce lines like ``## Abstract Body text here...``
    where the heading is just ``Abstract`` and the rest is body.

    Returns (heading, extra_body) where extra_body may be empty.
    """
    # Very short headings are fine as-is
    if len(heading) <= 60:
        return heading, ""

    # Strip optional leading section number for matching
    num_match = re.match(r"^(\d+(?:\.\d+)*\.?\s+)", heading)
    num_prefix = num_match.group(1) if num_match else ""
    rest = heading[len(num_prefix):]
    rest_lower = rest.lower()

    # Check against known section heading names
    for name in sorted(_KNOWN_SECTION_NAMES, key=len, reverse=True):
        if rest_lower.startswith(name) and len(rest) > len(name) + 1:
            after = rest[len(name) :]
            if after and after[0] in " \t":
                return (num_prefix + rest[: len(name)]).strip(), after.strip()

    # Word-count heuristic for unknown subsection headings.
    # Scan for the first plausible heading-body boundary.
    words = heading.split()
    if len(words) > 6:
        for n in range(2, min(12, len(words) - 2)):
            curr = words[n]
            if not curr or not curr[0].isupper():
                continue
            prev_word = words[n - 1].rstrip(".,;:").lower()
            if prev_word in _HEADING_CONNECTORS:
                continue
            remaining = " ".join(words[n:])
            if len(remaining) <= 30:
                continue
            # Strong signal: common sentence-starting word
            if curr.lower() in _SENTENCE_STARTERS:
                return " ".join(words[:n]).strip(), remaining.strip()
            # Medium signal: next word is lowercase (sentence-like)
            # and heading has >= 4 words, body is substantial (> 100 chars)
            if n >= 4 and n + 1 < len(words):
                next_w = words[n + 1].rstrip(".,;:")
                if next_w and next_w[0].islower() and len(remaining) > 100:
                    return " ".join(words[:n]).strip(), remaining.strip()
            # Weak fallback for very long headings (conservative)
            if n >= 8 and len(remaining) > 100:
                return " ".join(words[:n]).strip(), remaining.strip()

    # Detect repeated multi-word opening phrase: the body often starts with
    # the same words as the heading (e.g. "Graph-memory methods Graph-memory
    # methods maintain a graph...").
    half = len(rest) // 2
    for phrase_len in range(min(30, half), 14, -1):
        phrase = rest[:phrase_len]
        if " " not in phrase:
            continue
        repeat_pos = rest.find(phrase, phrase_len)
        if repeat_pos > 0:
            return (
                (num_prefix + rest[:repeat_pos]).strip(),
                rest[repeat_pos:].strip(),
            )

    # Fallback: try to split at a sentence boundary within first 200 chars
    if len(heading) > 200:
        m = re.search(r"[.;:]\s+([A-Z])", heading[:300])
        if m and m.start() > 10:
            return heading[: m.start() + 1].strip(), heading[m.start() + 2 :].strip()

    return heading, ""


def _parse_sections(md: str) -> list[_Section]:
    """Split Markdown into a flat list of sections by heading."""
    matches = list(_HEADING_RE.finditer(md))
    if not matches:
        return [_Section(level=1, heading="", body=md)]

    sections: list[_Section] = []

    # Text before first heading (if any)
    if matches[0].start() > 0:
        preamble_text = md[: matches[0].start()].strip()
        if preamble_text:
            sections.append(_Section(level=0, heading="", body=preamble_text))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()

        # IMP-17: Handle concatenated heading+body on same line
        heading, body_prefix = _separate_heading_body(heading)
        if body_prefix:
            body = body_prefix + ("\n\n" + body if body else "")

        sections.append(_Section(level=level, heading=heading, body=body))

    return sections


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_TITLE_SKIP = {
    "title",
    "abstract",
    "references",
    "appendix",
    "acknowledgments",
    "acknowledgements",
}

# T1.1: Headings that are NOT valid paper titles (tables, figures, etc.)
_TITLE_REJECT_RE = re.compile(
    r"^(?:table|figure|fig\.|tab\.|algorithm|listing|appendix)\s",
    re.IGNORECASE,
)

# T1.1: Headings that look like metric dumps rather than titles
_METRIC_DUMP_RE = re.compile(
    r"(?:primary_metric|accuracy|loss|f1_score|precision|recall)\b",
    re.IGNORECASE,
)


def _extract_title(sections: list[_Section], raw_md: str) -> str:
    """Extract paper title from sections or raw markdown."""
    # Look for an explicit "# Title" or "## Title" section whose body is the
    # actual title, or whose heading is "## Title Actual Paper Title".
    for sec in sections:
        if sec.level in (1, 2) and sec.heading_lower == "title":
            # The body often starts with **Bold Title** on the first line
            first_line = sec.body.split("\n")[0].strip()
            # Strip bold markers
            first_line = re.sub(r"\*\*(.+?)\*\*", r"\1", first_line)
            if first_line and not _is_bad_title(first_line):
                return first_line
        # Handle "## Title Actual Paper Title" pattern (title embedded in heading)
        if sec.level in (1, 2) and sec.heading_lower.startswith("title ") and len(sec.heading) > 6:
            return sec.heading[6:].strip()

    # Fallback: first H1/H2 heading that isn't a meta-heading or artefact
    for sec in sections:
        if (
            sec.level in (1, 2)
            and sec.heading
            and sec.heading_lower not in _TITLE_SKIP
            and not _is_bad_title(sec.heading)
        ):
            return sec.heading

    # Last resort: first non-empty line (still filtered)
    for line in raw_md.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not _is_bad_title(stripped):
            return stripped
    return "Untitled Paper"


def _is_bad_title(candidate: str) -> bool:
    """Return True if *candidate* is clearly not a paper title."""
    # Reject "Table 1 – ...", "Figure 2: ...", etc.
    if _TITLE_REJECT_RE.match(candidate):
        return True
    # Reject raw metric key dumps
    if _METRIC_DUMP_RE.search(candidate):
        return True
    # Reject if it contains raw underscore variable names (e.g. primary_metric)
    if re.search(r"\w+_\w+/\w+", candidate):
        return True
    return False


def _extract_abstract(sections: list[_Section]) -> str:
    """Extract abstract text from sections."""
    for sec in sections:
        if sec.heading_lower == "abstract":
            return sec.body
        # IMP-17 fallback: heading may still contain body text if
        # _separate_heading_body didn't recognise the pattern.
        if sec.heading_lower.startswith("abstract ") and len(sec.heading) > 20:
            extra = sec.heading[len("Abstract") :].strip()
            return extra + ("\n\n" + sec.body if sec.body else "")
    return ""


# ---------------------------------------------------------------------------
# Body building
# ---------------------------------------------------------------------------

_SKIP_HEADINGS = {"title", "abstract"}


def _build_body(sections: list[_Section], *, title: str = "") -> str:
    """Convert all non-title/abstract sections to LaTeX body text.

    When a paper has its title as an H1 heading (``# My Paper Title``),
    that heading is already rendered via ``\\title{}`` in the preamble.
    We skip it here and promote remaining headings so that H2 (``##``)
    maps to ``\\section``, H3 to ``\\subsection``, etc.
    """
    title_lower = title.strip().lower()

    # Determine minimum heading level used for real body sections
    # (skip title/abstract/references).
    title_h1_found = False
    for sec in sections:
        if (
            sec.level == 1
            and sec.heading
            and sec.heading.strip().lower() == title_lower
        ):
            title_h1_found = True
            break

    # T1.3: Auto-detect when all body sections use H2 (##) instead of H1 (#).
    # This happens when the LLM uses ## for main sections (Introduction, Method, etc.)
    # without an explicit H1 title heading. We must promote H2→\section.
    body_levels: set[int] = set()
    for sec in sections:
        if sec.heading_lower not in _SKIP_HEADINGS and sec.level >= 1:
            if not (sec.level == 1 and sec.heading.strip().lower() == title_lower):
                body_levels.add(sec.level)

    min_body_level = min(body_levels) if body_levels else 1

    # Promote if: (a) title was H1 and body starts at H2, OR
    # (b) no title H1 found but all body sections are H2+ (LLM omitted H1 title)
    # BUG-166: When title is H1 AND body also uses H1 for main sections,
    # offset must be 0 — otherwise H1→max(1,1-1)=1 and H2→max(1,2-1)=1
    # both collapse to \section, losing all subsection hierarchy.
    if title_h1_found:
        level_offset = 1 if min_body_level >= 2 else 0
    elif min_body_level >= 2:
        # All body sections are H2 or deeper — promote so H2→\section
        level_offset = min_body_level - 1
    else:
        level_offset = 0

    _level_map = {
        1: "section",
        2: "subsection",
        3: "subsubsection",
        4: "paragraph",
    }

    parts: list[str] = []
    for sec in sections:
        # Skip title-only and abstract sections
        if sec.heading_lower in _SKIP_HEADINGS:
            continue
        # Skip the H1 heading that was used as the paper title
        if (
            sec.level == 1
            and sec.heading
            and sec.heading.strip().lower() == title_lower
        ):
            continue
        if sec.level == 0:
            # Preamble text before any heading — include as-is
            parts.append(_convert_block(sec.body))
            continue

        effective_level = max(1, sec.level - level_offset)
        cmd = _level_map.get(effective_level, "paragraph")
        heading_tex = _escape_latex(sec.heading)
        # Strip leading manual section numbers: "1. Introduction" → "Introduction"
        # Handles: "1 Intro", "2.1 Related", "3.2.1 Details", "1. Intro"
        heading_tex = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading_tex)
        parts.append(f"\\{cmd}{{{heading_tex}}}")
        # Generate a label for cross-referencing
        if cmd in ("section", "subsection", "subsubsection"):
            label_key = re.sub(r"[^a-z0-9]+", "_", heading_tex.lower()).strip("_")[:40]
            if label_key:
                parts.append(f"\\label{{sec:{label_key}}}")
        if sec.body:
            parts.append(_convert_block(sec.body))

    return "\n\n".join(parts) + "\n"


def _deduplicate_tables(body: str) -> str:
    """IMP-30: Remove duplicate tables that share the same header row.

    LLMs sometimes repeat tables (e.g. same results table in Results and
    Discussion). We keep the first occurrence and drop subsequent copies.
    """
    import logging as _dup_log

    _TABLE_ENV_RE = re.compile(
        r"(\\begin\{table\}.*?\\end\{table\})", re.DOTALL
    )
    tables = list(_TABLE_ENV_RE.finditer(body))
    if len(tables) < 2:
        return body

    seen_headers: dict[str, int] = {}
    drop_spans: list[tuple[int, int]] = []
    for m in tables:
        table_text = m.group(1)
        # Extract header row (first row after \toprule)
        header_match = re.search(r"\\toprule\s*\n(.+?)\\\\", table_text)
        if not header_match:
            continue
        header_key = re.sub(r"\s+", " ", header_match.group(1).strip())
        if header_key in seen_headers:
            drop_spans.append((m.start(), m.end()))
            _dup_log.getLogger(__name__).info(
                "IMP-30: Dropping duplicate table (same header as table #%d)",
                seen_headers[header_key],
            )
        else:
            seen_headers[header_key] = len(seen_headers) + 1

    # Remove duplicates in reverse order to preserve offsets
    for start, end in reversed(drop_spans):
        body = body[:start] + body[end:]

    return body


# ---------------------------------------------------------------------------
# Block-level conversion
# ---------------------------------------------------------------------------

# Patterns for block-level structures
_DISPLAY_MATH_RE = re.compile(r"^\\\[(.+?)\\\]$", re.MULTILINE | re.DOTALL)
# $$...$$ display math (single- or multi-line)
_DISPLAY_MATH_DOLLAR_RE = re.compile(
    r"^\$\$\s*\n?(.*?)\n?\s*\$\$$", re.MULTILINE | re.DOTALL
)
_FENCED_CODE_RE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_TABLE_SEP_RE = re.compile(r"^\|[-:| ]+\|$")

# Markdown image pattern: ![caption](path)
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")

# Bullet / numbered list patterns
_BULLET_RE = re.compile(r"^(\s*)-\s+(.+)")
_NUMBERED_RE = re.compile(r"^(\s*)\d+\.\s+(.+)")


def _convert_block(text: str) -> str:
    """Convert a block of Markdown body text to LaTeX."""
    # Protect display math from further processing
    math_blocks: list[str] = []

    def _stash_math(m: re.Match[str]) -> str:
        idx = len(math_blocks)
        math_blocks.append(m.group(0))  # Keep \\[...\\] as-is
        return f"%%MATH_BLOCK_{idx}%%"

    def _stash_dollar_math(m: re.Match[str]) -> str:
        """Convert $$...$$ to \\begin{equation}...\\end{equation}."""
        idx = len(math_blocks)
        inner = m.group(1).strip()
        math_blocks.append(
            f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
        )
        return f"%%MATH_BLOCK_{idx}%%"

    text = _DISPLAY_MATH_RE.sub(_stash_math, text)
    # Also handle $$...$$ display math
    text = _DISPLAY_MATH_DOLLAR_RE.sub(_stash_dollar_math, text)

    # Protect fenced code blocks
    code_blocks: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        idx = len(code_blocks)
        lang = m.group(1) or ""
        code = m.group(2)
        code_blocks.append(_render_code_block(lang, code))
        return f"%%CODE_BLOCK_{idx}%%"

    text = _FENCED_CODE_RE.sub(_stash_code, text)

    # Protect raw LaTeX environments (table, figure, algorithm, etc.)
    # These appear when pre-built LaTeX (e.g. anti-fabrication result tables)
    # is embedded directly in the markdown.  Without protection, their
    # contents go through _convert_inline which double-escapes {, }, _, &.
    latex_env_blocks: list[str] = []

    def _stash_latex_env(m: re.Match[str]) -> str:
        idx = len(latex_env_blocks)
        latex_env_blocks.append(m.group(0))
        return f"%%LATEX_ENV_{idx}%%"

    # Match \begin{env}...\end{env} for environments that should pass through.
    text = re.sub(
        r"\\begin\{(table|figure|tabular|algorithm|algorithmic|equation|align"
        r"|gather|multline|minipage|tikzpicture)\*?\}.*?"
        r"\\end\{\1\*?\}",
        _stash_latex_env,
        text,
        flags=re.DOTALL,
    )

    # Process line by line for lists, tables, and paragraphs
    lines = text.split("\n")
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for stashed blocks
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

        # Stashed LaTeX environments — pass through unchanged
        if line.strip().startswith("%%LATEX_ENV_"):
            idx = int(re.search(r"\d+", line.strip()).group())  # type: ignore[union-attr]
            output.append(latex_env_blocks[idx])
            i += 1
            continue

        # Bullet list
        if _BULLET_RE.match(line):
            items, i = _collect_list(lines, i, _BULLET_RE)
            output.append(_render_itemize(items))
            continue

        # Numbered list
        if _NUMBERED_RE.match(line):
            items, i = _collect_list(lines, i, _NUMBERED_RE)
            output.append(_render_enumerate(items))
            continue

        # Table detection (line starts with |)
        if (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1].strip())
        ):
            # Check if previous line is a table caption (e.g. **Table 1: ...**)
            table_caption = ""
            if output:
                prev = output[-1].strip()
                # Match bold caption: \textbf{Table N...} (already converted)
                # or raw markdown: **Table N: ...**
                cap_m = re.match(
                    r"(?:\\textbf\{|[*]{2})\s*Table\s+\d+[.:]?\s*(.*?)(?:\}|[*]{2})$",
                    prev,
                )
                if cap_m:
                    table_caption = f"Table {cap_m.group(1)}" if cap_m.group(1) else ""
                    if not table_caption:
                        table_caption = prev
                    output.pop()  # Remove caption line from output (now inside table)
            table_lines, i = _collect_table(lines, i)
            output.append(_render_table(table_lines, caption=table_caption))
            continue

        # Markdown image: ![caption](path)
        img_match = _IMAGE_RE.match(line.strip())
        if img_match:
            output.append(_render_figure(img_match.group(1), img_match.group(2)))
            i += 1
            continue

        # Regular paragraph line
        output.append(_convert_inline(line))
        i += 1

    return "\n".join(output)


# ---------------------------------------------------------------------------
# List handling
# ---------------------------------------------------------------------------


def _collect_list(
    lines: list[str], start: int, pattern: re.Pattern[str]
) -> tuple[list[str], int]:
    """Collect consecutive list items matching *pattern*."""
    items: list[str] = []
    i = start
    while i < len(lines):
        m = pattern.match(lines[i])
        if m:
            items.append(m.group(2))
            i += 1
        elif lines[i].strip() == "":
            # Blank line — might continue list or end it
            if i + 1 < len(lines) and pattern.match(lines[i + 1]):
                i += 1  # skip blank, continue
            else:
                break
        elif lines[i].startswith("  ") or lines[i].startswith("\t"):
            # Continuation of previous item
            if items:
                items[-1] += " " + lines[i].strip()
            i += 1
        else:
            break
    return items, i


def _render_itemize(items: list[str]) -> str:
    inner = "\n".join(f"  \\item {_convert_inline(item)}" for item in items)
    return f"\\begin{{itemize}}\n{inner}\n\\end{{itemize}}"


def _render_enumerate(items: list[str]) -> str:
    inner = "\n".join(f"  \\item {_convert_inline(item)}" for item in items)
    return f"\\begin{{enumerate}}\n{inner}\n\\end{{enumerate}}"




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

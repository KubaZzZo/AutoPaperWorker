"""Final LaTeX sanitization helpers."""

from __future__ import annotations

import re
import unicodedata

from researchclaw.templates.inline import _UNICODE_GREEK_TO_LATEX


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



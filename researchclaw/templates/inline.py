"""Inline Markdown-to-LaTeX conversion helpers."""

from __future__ import annotations

import re

# BUG-110: Unicode Greek → LaTeX math replacements for inline text.
# Used in _convert_inline() and _sanitize_latex_output().
_UNICODE_GREEK_TO_LATEX: dict[str, str] = {
    # Lowercase
    "\u03b1": "$\\alpha$", "\u03b2": "$\\beta$", "\u03b3": "$\\gamma$",
    "\u03b4": "$\\delta$", "\u03b5": "$\\epsilon$", "\u03b6": "$\\zeta$",
    "\u03b7": "$\\eta$", "\u03b8": "$\\theta$", "\u03b9": "$\\iota$",
    "\u03ba": "$\\kappa$", "\u03bb": "$\\lambda$", "\u03bc": "$\\mu$",
    "\u03bd": "$\\nu$", "\u03be": "$\\xi$", "\u03c0": "$\\pi$",
    "\u03c1": "$\\rho$", "\u03c3": "$\\sigma$", "\u03c4": "$\\tau$",
    "\u03c5": "$\\upsilon$", "\u03c6": "$\\phi$", "\u03c7": "$\\chi$",
    "\u03c8": "$\\psi$", "\u03c9": "$\\omega$",
    # Uppercase
    "\u0393": "$\\Gamma$", "\u0394": "$\\Delta$", "\u0398": "$\\Theta$",
    "\u039b": "$\\Lambda$", "\u039e": "$\\Xi$", "\u03a0": "$\\Pi$",
    "\u03a3": "$\\Sigma$", "\u03a6": "$\\Phi$", "\u03a8": "$\\Psi$",
    "\u03a9": "$\\Omega$",
    # Common math symbols not already handled
    "\u2200": "$\\forall$", "\u2203": "$\\exists$",
    "\u2207": "$\\nabla$", "\u2202": "$\\partial$",
    "\u2026": "\\ldots{}", "\u22c5": "$\\cdot$",
    "\u2113": "$\\ell$", "\u222b": "$\\int$",
    "\u2209": "$\\notin$",
    # Common symbols that cause null-byte corruption if not converted
    "\u00b1": "$\\pm$",        # ±
    "\u00d7": "$\\times$",     # ×
    "\u2248": "$\\approx$",    # ≈
    "\u2264": "$\\leq$",       # ≤
    "\u2265": "$\\geq$",       # ≥
    "\u2260": "$\\neq$",       # ≠
    "\u221e": "$\\infty$",     # ∞
    # Additional symbols found in Runs 49-52
    "\u2212": "$-$",           # − (minus sign, distinct from hyphen)
    "\u2282": "$\\subset$",    # ⊂
    "\u222a": "$\\cup$",       # ∪
    "\u211d": "$\\mathbb{R}$", # ℝ
    "\u0302": "\\^{}",         # ̂  (combining circumflex)
    "\u0303": "\\~{}",         # ̃  (combining tilde — Run 61 pseudocode)
    "\u221d": "$\\propto$",    # ∝ (proportional to)
    "\u2208": "$\\in$",        # ∈
}

# Order matters: process bold before italic to avoid conflicts.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Characters that need escaping in LaTeX (but NOT inside math or \cite)
_LATEX_SPECIAL = re.compile(r"([#%&_{}])")
_LATEX_TILDE = re.compile(r"~")
_LATEX_CARET = re.compile(r"\^")
_LATEX_DOLLAR = re.compile(r"(?<!\\)\$")


def _convert_inline(text: str) -> str:
    """Convert inline Markdown formatting to LaTeX.

    Preserves:
    - Inline math ``\\(...\\)`` and ``$...$``
    - ``\\cite{...}`` references
    - Display math markers (already handled at block level)
    """
    # Normalize Unicode punctuation to LaTeX equivalents
    text = text.replace("\u2014", "---")          # em-dash —
    text = text.replace("\u2013", "--")            # en-dash –
    text = text.replace("\u201c", "``")            # left double quote "
    text = text.replace("\u201d", "''")            # right double quote "
    text = text.replace("\u2018", "`")             # left single quote '
    text = text.replace("\u2019", "'")             # right single quote '
    text = text.replace("\u00b1", "$\\pm$")        # ±
    text = text.replace("\u2248", "$\\approx$")    # ≈
    text = text.replace("\u2264", "$\\leq$")       # ≤
    text = text.replace("\u2265", "$\\geq$")       # ≥
    text = text.replace("\u2192", "$\\rightarrow$")  # →
    text = text.replace("\u2190", "$\\leftarrow$")   # ←
    text = text.replace("\u00d7", "$\\times$")     # ×
    text = text.replace("\u2260", "$\\neq$")       # ≠
    text = text.replace("\u2208", "$\\in$")         # ∈
    text = text.replace("\u221e", "$\\infty$")      # ∞

    # BUG-110: Replace Unicode Greek letters with LaTeX math equivalents.
    # These appear when LLMs emit raw Unicode (e.g. "ε-greedy" instead of
    # "$\epsilon$-greedy") and cause fatal pdflatex errors.
    for _uchar, _lcmd in _UNICODE_GREEK_TO_LATEX.items():
        if _uchar in text:
            text = text.replace(_uchar, _lcmd)

    # Protect math and cite from escaping
    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        idx = len(protected)
        protected.append(m.group(0))
        return f"\x00PROT{idx}\x00"

    # Protect inline math: \(...\) and $...$
    text = re.sub(r"\\\(.+?\\\)", _protect, text)
    text = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", _protect, text)

    # Protect display math residuals: \[...\] and $$...$$
    text = re.sub(r"\\\[.+?\\\]", _protect, text, flags=re.DOTALL)
    text = re.sub(r"\$\$.+?\$\$", _protect, text, flags=re.DOTALL)

    # Protect \cite{...} and \textbf etc.
    text = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", _protect, text)

    # BUG-182: Protect already-escaped LaTeX specials from double-escaping.
    # LLMs often pre-escape underscores/etc: e.g. RawObs\_PPO → should stay
    # as \_, not become \\_ which pdflatex interprets as linebreak + subscript.
    text = re.sub(r"\\([#%&_{}])", _protect, text)

    # Protect \(...\) patterns with linebreaks already handled
    # (should be caught above, but safety net)

    # Convert markdown links BEFORE escaping so URLs with _ are preserved.
    # Protect images first so they don't get matched as links.
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _protect, text)

    def _convert_and_protect_link(m: re.Match[str]) -> str:
        href = f"\\href{{{m.group(2)}}}{{{m.group(1)}}}"
        idx = len(protected)
        protected.append(href)
        return f"\x00PROT{idx}\x00"

    text = _LINK_RE.sub(_convert_and_protect_link, text)

    # Escape special LaTeX characters
    text = _LATEX_SPECIAL.sub(r"\\\1", text)
    text = _LATEX_TILDE.sub(r"\\textasciitilde{}", text)
    text = _LATEX_CARET.sub(r"\\textasciicircum{}", text)
    text = _LATEX_DOLLAR.sub(r"\\$", text)

    # Convert bold **text** → \textbf{text}
    text = _BOLD_RE.sub(r"\\textbf{\1}", text)

    # Convert italic *text* → \textit{text}
    text = _ITALIC_RE.sub(r"\\textit{\1}", text)

    # Convert inline code `text` → \texttt{text}
    text = _INLINE_CODE_RE.sub(r"\\texttt{\1}", text)

    # Links and images were already converted+protected before escaping.

    # Fallback: convert any remaining [cite_key] patterns to \cite{key}
    # This catches citations that were not converted upstream.
    # BUG-32 fix: key pattern must also match author2017keyword style keys
    # (e.g., roijers2017multiobjective, abels2019dynamic)
    _CITE_KEY_PAT = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9_]*"
    text = re.sub(
        rf"\[({_CITE_KEY_PAT}(?:\s*,\s*{_CITE_KEY_PAT})*)\]",
        r"\\cite{\1}",
        text,
    )

    # Restore protected segments in reverse order so that nested
    # markers (e.g. PROT0 inside PROT1's value) are resolved correctly.
    for idx in range(len(protected) - 1, -1, -1):
        text = text.replace(f"\x00PROT{idx}\x00", protected[idx])

    return text



def _escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text (titles, headings).

    Does NOT escape inside math delimiters or \\commands.
    """
    # Protect math first
    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        idx = len(protected)
        protected.append(m.group(0))
        return f"\x00PROT{idx}\x00"

    text = re.sub(r"\\\(.+?\\\)", _protect, text)
    text = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", _protect, text)
    text = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", _protect, text)

    text = _LATEX_SPECIAL.sub(r"\\\1", text)
    text = text.replace("~", "\\textasciitilde{}")
    text = text.replace("^", "\\textasciicircum{}")

    for idx, val in enumerate(protected):
        text = text.replace(f"\x00PROT{idx}\x00", val)

    return text

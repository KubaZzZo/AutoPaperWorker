"""Unicode and BibTeX sanitization helpers for LaTeX compilation."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("researchclaw.templates.compiler")

_CYRILLIC_TO_LATIN_MAP: dict[str, str] = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E",
    "Ё": "E", "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K",
    "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
    "Э": "E", "Ю": "Yu", "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def _sanitize_tex_unicode(tex_path: Path) -> None:
    """Strip problematic Unicode characters from .tex source.

    BUG-197: Characters like U+202F (NARROW NO-BREAK SPACE), U+2009 (THIN
    SPACE), U+00A0 (NO-BREAK SPACE), and other non-ASCII whitespace cause
    pdflatex to emit broken UTF-8 in error messages, which crashes Python's
    ``subprocess.run(text=True)`` and prevents the bibtex + multi-pass
    pipeline from completing.  These characters appear when LLMs copy-paste
    text from web sources or academic papers.

    The safe replacement is a normal ASCII space for whitespace-like chars,
    and empty string for invisible control chars.
    """
    if not tex_path.exists():
        return
    try:
        text = tex_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return

    # Whitespace-like Unicode → ASCII space
    _UNICODE_SPACES = (
        "\u00a0",  # NO-BREAK SPACE
        "\u202f",  # NARROW NO-BREAK SPACE (BUG-197 trigger)
        "\u2009",  # THIN SPACE
        "\u2007",  # FIGURE SPACE
        "\u2008",  # PUNCTUATION SPACE
        "\u200a",  # HAIR SPACE
        "\u205f",  # MEDIUM MATHEMATICAL SPACE
        "\u3000",  # IDEOGRAPHIC SPACE
    )
    # Invisible control characters → remove
    _INVISIBLE_CHARS = (
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\ufeff",  # BOM / ZERO-WIDTH NO-BREAK SPACE
        "\u200b",  # ZERO-WIDTH SPACE
        "\u200c",  # ZERO-WIDTH NON-JOINER
        "\u200d",  # ZERO-WIDTH JOINER
        "\u00ad",  # SOFT HYPHEN
        "\u2060",  # WORD JOINER
        "\u2028",  # LINE SEPARATOR
        "\u2029",  # PARAGRAPH SEPARATOR
    )

    changed = False
    for ch in _UNICODE_SPACES:
        if ch in text:
            text = text.replace(ch, " ")
            changed = True
    for ch in _INVISIBLE_CHARS:
        if ch in text:
            text = text.replace(ch, "")
            changed = True

    # BUG-201: Transliterate any Cyrillic that leaked into .tex (from bib
    # entries inlined by bibtex, or from LLM-generated text).
    _has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in text)
    if _has_cyrillic:
        for cyr, lat in _CYRILLIC_TO_LATIN_MAP.items():
            if cyr in text:
                text = text.replace(cyr, lat)
        changed = True

    if changed:
        tex_path.write_text(text, encoding="utf-8")
        logger.info("BUG-197: Sanitized problematic Unicode in %s", tex_path.name)


def _sanitize_bib_file(bib_path: Path) -> None:
    """Sanitize .bib files: escape bare ``&`` and strip invisible Unicode.

    BibTeX treats ``&`` as a special character; journal names like
    "Science & Technology" must use ``\\&``.

    BUG-180: Invisible Unicode characters (U+200E LEFT-TO-RIGHT MARK,
    U+200F RIGHT-TO-LEFT MARK, U+FEFF BOM, U+200B ZERO-WIDTH SPACE,
    U+200C/U+200D joiners, U+00AD soft hyphen) can appear in
    copy-pasted author names and break pdflatex.
    """
    if not bib_path.exists():
        return
    try:
        text = bib_path.read_text(encoding="utf-8")
    except Exception:
        return

    # BUG-180: Strip invisible Unicode characters
    _INVISIBLE_CHARS = (
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\ufeff",  # BOM / ZERO-WIDTH NO-BREAK SPACE
        "\u200b",  # ZERO-WIDTH SPACE
        "\u200c",  # ZERO-WIDTH NON-JOINER
        "\u200d",  # ZERO-WIDTH JOINER
        "\u00ad",  # SOFT HYPHEN
        "\u2060",  # WORD JOINER
        "\u2028",  # LINE SEPARATOR
        "\u2029",  # PARAGRAPH SEPARATOR
    )
    for ch in _INVISIBLE_CHARS:
        if ch in text:
            text = text.replace(ch, "")

    # BUG-201: Transliterate Cyrillic characters to Latin equivalents.
    # Russian author names (e.g. "А. И. Колесников") from Semantic Scholar
    # cause "! LaTeX Error: Unicode character" when pdflatex runs without T2A
    # font encoding.  Transliterating preserves name readability.
    _orig_text = text
    for cyr, lat in _CYRILLIC_TO_LATIN_MAP.items():
        if cyr in text:
            text = text.replace(cyr, lat)

    # BUG-217: Strip literal escape sequences (\n, \r, \t) in bib field values.
    # These appear when API responses embed Python-style escapes into titles.
    # A literal `\n` is never a valid BibTeX/LaTeX command and causes
    # "Undefined control sequence" errors during compilation.
    text = re.sub(r"\\n(?=\s)", " ", text)
    text = re.sub(r"\\r(?=\s)", "", text)
    text = re.sub(r"\\t(?=\s)", " ", text)

    lines = text.split("\n")
    changed = text != _orig_text
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Only fix field-value lines (e.g.  journal = {Science & Technology},)
        # Skip @type{ lines, key lines, and URL/DOI fields (BUG-DA8-12)
        if "=" in stripped and "{" in stripped and "&" in stripped and "\\&" not in stripped:
            _field_name = stripped.split("=", 1)[0].strip().lower()
            if _field_name in ("url", "doi", "howpublished", "eprint"):
                continue  # Don't escape & in URLs
            lines[i] = line.replace("&", "\\&")
            changed = True

    new_text = "\n".join(lines)
    if new_text != text or changed:
        bib_path.write_text(new_text, encoding="utf-8")
        logger.info("Sanitized bib file %s", bib_path.name)


__all__ = ["_sanitize_bib_file", "_sanitize_tex_unicode"]

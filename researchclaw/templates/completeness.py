"""Paper completeness checks used during Markdown-to-LaTeX conversion."""

from __future__ import annotations

import re
from typing import Protocol


class SectionLike(Protocol):
    level: int
    heading: str
    heading_lower: str
    body: str


_EXPECTED_SECTIONS = {
    "introduction",
    "related work",
    "method",
    "experiment",
    "result",
    "discussion",
    "conclusion",
}

_SECTION_ALIASES: dict[str, str] = {
    "methodology": "method",
    "methods": "method",
    "proposed method": "method",
    "approach": "method",
    "experiments": "experiment",
    "experimental setup": "experiment",
    "experimental results": "result",
    "results": "result",
    "results and discussion": "result",
    "results and analysis": "result",
    "discussion and results": "result",
    "conclusions": "conclusion",
    "conclusion and future work": "conclusion",
    "summary": "conclusion",
    "background": "related work",
    "literature review": "related work",
    "prior work": "related work",
}


def _canonical_heading(h: str) -> str:
    h = h.strip().lower()
    h = re.sub(r"^\d+(?:\.\d+)*\s+", "", h)
    h = re.sub(r"[^a-z0-9 ]+", "", h)
    h = re.sub(r"\s+", " ", h).strip()
    return _SECTION_ALIASES.get(h, h)


def check_paper_completeness(sections: list[SectionLike]) -> list[str]:
    """Return warnings for missing or suspiciously thin paper sections."""
    warnings: list[str] = []

    present = {
        _canonical_heading(sec.heading)
        for sec in sections
        if sec.level in (1, 2) and sec.heading
    }
    missing = _EXPECTED_SECTIONS - present
    if missing:
        warnings.append(
            "Missing expected paper sections: "
            + ", ".join(sorted(missing))
        )

    for sec in sections:
        if sec.level not in (1, 2) or not sec.heading:
            continue
        heading = _canonical_heading(sec.heading)
        if heading in _EXPECTED_SECTIONS and len(sec.body.split()) < 50:
            warnings.append(
                f"Section '{sec.heading}' is very short "
                f"({len(sec.body.split())} words)."
            )

    from researchclaw.prompts import _SECTION_TARGET_ALIASES, SECTION_WORD_TARGETS

    for sec in sections:
        if sec.level not in (1, 2) or not sec.heading:
            continue
        canon = sec.heading_lower
        if canon not in SECTION_WORD_TARGETS:
            canon = _SECTION_TARGET_ALIASES.get(sec.heading_lower, "")
        if not canon or canon not in SECTION_WORD_TARGETS:
            continue
        lo, hi = SECTION_WORD_TARGETS[canon]
        wc = len(sec.body.split())
        if wc < int(lo * 0.6):
            warnings.append(
                f"Section '{sec.heading}' is only {wc} words "
                f"(expected {lo}-{hi}). Content may be severely truncated."
            )
        elif wc > int(hi * 1.5):
            warnings.append(
                f"Section '{sec.heading}' is {wc} words "
                f"(expected {lo}-{hi}). Consider trimming."
            )

    _bullet_re_cc = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
    _numbered_re_cc = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
    _bullet_ok_sections = {"introduction", "limitations", "limitation", "abstract"}
    for sec in sections:
        if sec.level not in (1, 2) or not sec.heading:
            continue
        hl = sec.heading_lower
        if hl in _bullet_ok_sections:
            continue
        if not sec.body:
            continue
        total_lines = len([ln for ln in sec.body.splitlines() if ln.strip()])
        if total_lines < 4:
            continue
        bullet_count = (
            len(_bullet_re_cc.findall(sec.body))
            + len(_numbered_re_cc.findall(sec.body))
        )
        density = bullet_count / total_lines
        if density > 0.30:
            warnings.append(
                f"Section '{sec.heading}' has high bullet-point density "
                f"({bullet_count}/{total_lines} lines = {density:.0%}). "
                f"Conference papers should use flowing prose."
            )

    return warnings

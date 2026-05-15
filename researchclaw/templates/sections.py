"""Section parsing and metadata extraction helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


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



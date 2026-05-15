"""Citation resolution helpers for the review/publish stages."""

from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

# Minimum title-similarity between search result and expected title/query
# for a result to be accepted. Prevents unrelated papers from replacing
# known citation keys.
CITATION_RESOLVE_MIN_SIMILARITY = 0.30


def load_seminal_papers_by_key() -> dict[str, dict]:
    """Load seminal_papers.yaml and index entries by cite_key."""
    try:
        from researchclaw.data import _load_all as _load_seminal_all

        all_papers = _load_seminal_all()
        return {p["cite_key"]: p for p in all_papers if "cite_key" in p}
    except Exception:  # noqa: BLE001
        return {}


def seminal_to_bibtex(paper: dict, cite_key: str) -> str:
    """Convert a seminal_papers.yaml entry dict to a BibTeX string."""
    title = paper.get("title", "Unknown")
    authors = paper.get("authors", "Unknown")
    year = paper.get("year", "")
    venue = paper.get("venue", "")

    venue_lower = (venue or "").lower()
    is_conf = any(
        kw in venue_lower
        for kw in (
            "neurips",
            "nips",
            "icml",
            "iclr",
            "cvpr",
            "eccv",
            "iccv",
            "aaai",
            "acl",
            "emnlp",
            "naacl",
            "sigir",
            "kdd",
            "www",
            "ijcai",
            "conference",
            "proc",
            "workshop",
        )
    )
    if is_conf:
        return (
            f"@inproceedings{{{cite_key},\n"
            f"  title = {{{title}}},\n"
            f"  author = {{{authors}}},\n"
            f"  year = {{{year}}},\n"
            f"  booktitle = {{{venue}}},\n"
            f"}}"
        )
    return (
        f"@article{{{cite_key},\n"
        f"  title = {{{title}}},\n"
        f"  author = {{{authors}}},\n"
        f"  year = {{{year}}},\n"
        f"  journal = {{{venue}}},\n"
        f"}}"
    )


def resolve_missing_citations(
    missing_keys: set[str],
    existing_bib: str,
    *,
    diagnostic_logger: logging.Logger | None = None,
) -> tuple[set[str], list[str]]:
    """Try to find BibTeX entries for citation keys missing from references.bib.

    Resolution is intentionally conservative:
    1. Exact local lookup in seminal_papers.yaml.
    2. API search with title-overlap and year validation.
    3. Skip unresolved keys rather than injecting a wrong paper.
    """
    log = diagnostic_logger or logger
    resolved: set[str] = set()
    new_entries: list[str] = []

    def _parse_cite_key(key: str) -> tuple[str, str, str]:
        m = re.match(r"([a-zA-Z]+?)(\d{4})(.*)", key)
        if m:
            return m.group(1), m.group(2), m.group(3)
        return key, "", ""

    def _title_word_overlap(title: str, query_words: list[str]) -> float:
        if not query_words:
            return 0.0
        title_lower = set(re.sub(r"[^a-z0-9\s]", "", title.lower()).split()) - {""}
        if not title_lower:
            return 0.0
        matched = sum(1 for w in query_words if w.lower() in title_lower)
        return matched / len(query_words)

    seminal_by_key = load_seminal_papers_by_key()

    for key in sorted(missing_keys):
        if key in seminal_by_key and key not in existing_bib:
            sp = seminal_by_key[key]
            bib_entry = seminal_to_bibtex(sp, key)
            new_entries.append(bib_entry)
            resolved.add(key)
            log.info(
                "BUG-194: Resolved %r via seminal_papers.yaml -> %r (%s)",
                key,
                sp.get("title", "")[:60],
                sp.get("year", ""),
            )

    remaining = sorted(k for k in (missing_keys - resolved) if k not in existing_bib)
    if not remaining:
        return resolved, new_entries

    try:
        from researchclaw.literature.search import search_papers
    except ImportError:
        log.debug("BUG-176: literature.search not available, skipping resolution")
        return resolved, new_entries

    for key in remaining:
        author, year, hint = _parse_cite_key(key)
        if not author or not year:
            continue

        hint_words = re.findall(r"[a-zA-Z]+", hint) if hint else []
        query_words = [author] + hint_words
        query = " ".join([author] + hint_words + [year])

        try:
            results = search_papers(query, limit=5, deduplicate=True)
        except Exception as exc:
            log.debug("BUG-176: Search failed for %r: %s", key, exc)
            continue

        if not results:
            log.debug(
                "BUG-194: No search results for %r (query=%r), skipping",
                key,
                query,
            )
            continue

        best = None
        best_score = -1.0
        for paper in results:
            overlap = _title_word_overlap(paper.title, query_words)
            year_bonus = 0.2 if str(paper.year) == year else 0.0
            author_bonus = 0.0
            if any(author.lower() in a.name.lower() for a in paper.authors):
                author_bonus = 0.2
            score = overlap + year_bonus + author_bonus
            if score > best_score:
                best_score = score
                best = paper

        if best is None:
            continue

        overlap = _title_word_overlap(best.title, query_words)
        if overlap < CITATION_RESOLVE_MIN_SIMILARITY:
            log.info(
                "BUG-194: Rejecting search result for %r; title %r has "
                "too-low overlap (%.2f < %.2f) with query words %r",
                key,
                best.title[:60],
                overlap,
                CITATION_RESOLVE_MIN_SIMILARITY,
                query_words,
            )
            continue

        if year and best.year:
            year_diff = abs(int(year) - int(best.year))
            if year_diff > 1:
                log.info(
                    "BUG-194: Rejecting search result for %r; year mismatch "
                    "(%s vs %s, diff=%d)",
                    key,
                    year,
                    best.year,
                    year_diff,
                )
                continue

        bib_entry = best.to_bibtex()
        orig_key_match = re.match(r"@(\w+)\{([^,]+),", bib_entry)
        if orig_key_match:
            bib_entry = bib_entry.replace(
                f"@{orig_key_match.group(1)}{{{orig_key_match.group(2)},",
                f"@{orig_key_match.group(1)}{{{key},",
                1,
            )

        if key not in existing_bib:
            new_entries.append(bib_entry)
            resolved.add(key)
            log.info(
                "BUG-194: Resolved %r via API -> %r (%s, overlap=%.2f)",
                key,
                best.title[:60],
                best.year,
                overlap,
            )
        else:
            log.debug("BUG-194: Key %r already in bib, skipping API result", key)

        time.sleep(0.5)

    return resolved, new_entries

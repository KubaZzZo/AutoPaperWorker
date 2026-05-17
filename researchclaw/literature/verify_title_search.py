"""Title-search verification helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from researchclaw.literature.models import Paper
from researchclaw.literature.verify_models import CitationResult, VerifyStatus

logger = logging.getLogger("researchclaw.literature.verify")

def title_similarity(a: str, b: str) -> float:
    """Word-overlap Jaccard-ish similarity between two titles.

    Returns 0.0–1.0.  Uses max(len) as denominator so short titles don't
    inflate the score.
    """

    def _words(t: str) -> set[str]:
        return set(re.sub(r"[^a-z0-9\s]", "", t.lower()).split()) - {""}

    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


_CACHE_DIR = Path.home() / ".cache" / "researchclaw" / "citation_verify"


def _cache_key(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def _read_cache(title: str) -> CitationResult | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{_cache_key(title)}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return CitationResult(
                cite_key=data.get("cite_key", ""),
                title=data.get("title", title),
                status=VerifyStatus(data["status"]),
                confidence=data["confidence"],
                method=data["method"],
                details=data.get("details", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
    return None


def _write_cache(title: str, result: CitationResult) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{_cache_key(title)}.json"
    cache_file.write_text(
        json.dumps(result.to_dict(), indent=2),
        encoding="utf-8",
    )


def verify_by_title_search(
    title: str,
    *,
    s2_api_key: str = "",
) -> CitationResult | None:
    """Search for a paper by title and verify its existence.

    Uses the unified ``search_papers`` function from our literature module.
    Returns *None* only on total network failure.
    """
    from researchclaw.literature.search import search_papers

    try:
        results = search_papers(
            title,
            limit=5,
            s2_api_key=s2_api_key,
            deduplicate=True,
        )
    except Exception as exc:
        logger.debug("Title search failed for %r: %s", title, exc)
        return None

    if not results:
        return CitationResult(
            cite_key="",
            title=title,
            status=VerifyStatus.HALLUCINATED,
            confidence=0.7,
            method="title_search",
            details="No results found via Semantic Scholar + arXiv",
        )

    # Find best title match
    best_sim = 0.0
    best_paper: Paper | None = None
    for paper in results:
        sim = title_similarity(title, paper.title)
        if sim > best_sim:
            best_sim = sim
            best_paper = paper

    if best_sim >= 0.80:
        return CitationResult(
            cite_key="",
            title=title,
            status=VerifyStatus.VERIFIED,
            confidence=best_sim,
            method="title_search",
            details=f"Found via search: '{best_paper.title}'" if best_paper else "",
            matched_paper=best_paper,
        )
    elif best_sim >= 0.50:
        return CitationResult(
            cite_key="",
            title=title,
            status=VerifyStatus.SUSPICIOUS,
            confidence=best_sim,
            method="title_search",
            details=(
                f"Partial match (sim={best_sim:.2f}): '{best_paper.title}'"
                if best_paper
                else ""
            ),
            matched_paper=best_paper,
        )
    else:
        return CitationResult(
            cite_key="",
            title=title,
            status=VerifyStatus.HALLUCINATED,
            confidence=1.0 - best_sim,
            method="title_search",
            details=(
                f"Best match too weak (sim={best_sim:.2f}): '{best_paper.title}'"
                if best_paper
                else "No match found"
            ),
        )


__all__ = ["_read_cache", "_write_cache", "title_similarity", "verify_by_title_search"]

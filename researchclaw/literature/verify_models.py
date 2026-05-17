"""Citation verification data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from researchclaw.literature.models import Paper

class VerifyStatus(str, Enum):
    """Verification outcome for a single citation."""

    VERIFIED = "verified"
    SUSPICIOUS = "suspicious"
    HALLUCINATED = "hallucinated"
    SKIPPED = "skipped"


@dataclass
class CitationResult:
    """Verification result for one BibTeX entry."""

    cite_key: str
    title: str
    status: VerifyStatus
    confidence: float  # 0.0–1.0
    method: str  # "arxiv_id" | "doi" | "title_search" | "skipped"
    details: str = ""
    matched_paper: Paper | None = None
    relevance_score: float | None = None  # 0.0–1.0, set by LLM relevance check

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "cite_key": self.cite_key,
            "title": self.title,
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "details": self.details,
        }
        if self.relevance_score is not None:
            d["relevance_score"] = round(self.relevance_score, 2)
        if self.matched_paper:
            d["matched_paper"] = {
                "title": self.matched_paper.title,
                "authors": [a.name for a in self.matched_paper.authors],
                "year": self.matched_paper.year,
                "source": self.matched_paper.source,
            }
        return d


@dataclass
class VerificationReport:
    """Aggregate report for all citations in a paper."""

    total: int = 0
    verified: int = 0
    suspicious: int = 0
    hallucinated: int = 0
    skipped: int = 0
    results: list[CitationResult] = field(default_factory=list)

    @property
    def integrity_score(self) -> float:
        """Fraction of verifiable citations that are verified (0.0–1.0)."""
        verifiable = self.total - self.skipped
        if verifiable <= 0:
            return 1.0
        return round(self.verified / verifiable, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "total": self.total,
                "verified": self.verified,
                "suspicious": self.suspicious,
                "hallucinated": self.hallucinated,
                "skipped": self.skipped,
                "integrity_score": self.integrity_score,
            },
            "results": [r.to_dict() for r in self.results],
        }


__all__ = ["CitationResult", "VerificationReport", "VerifyStatus"]

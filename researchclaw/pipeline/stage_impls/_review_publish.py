"""Compatibility facade for review, revision, and publish stage implementations.

The stage implementations moved into focused modules:
- ``_review`` for peer review and experiment evidence collection.
- ``_revision`` for paper revision and the quality gate.
- ``_publish`` for archival, export/publish, sanitization, and citation checks.

This module keeps legacy private imports working while callers migrate to the
focused modules.
"""

from __future__ import annotations

from researchclaw.pipeline.stage_impls._publish import (
    _check_citation_relevance,
    _execute_citation_verify,
    _execute_export_publish,
    _execute_knowledge_archive,
    _get_review_compiled_pdf,
    _load_seminal_papers_by_key,
    _remove_bibtex_entries,
    _remove_citations_from_text,
    _resolve_missing_citations,
    _sanitize_fabricated_data,
    _seminal_to_bibtex,
)
from researchclaw.pipeline.stage_impls._review import (
    _collect_experiment_evidence,
    _execute_peer_review,
)
from researchclaw.pipeline.stage_impls._revision import (
    _execute_paper_revision,
    _execute_quality_gate,
    _get_collect_raw_experiment_metrics,
)

__all__ = [
    "_check_citation_relevance",
    "_collect_experiment_evidence",
    "_execute_citation_verify",
    "_execute_export_publish",
    "_execute_knowledge_archive",
    "_execute_paper_revision",
    "_execute_peer_review",
    "_execute_quality_gate",
    "_get_collect_raw_experiment_metrics",
    "_get_review_compiled_pdf",
    "_load_seminal_papers_by_key",
    "_remove_bibtex_entries",
    "_remove_citations_from_text",
    "_resolve_missing_citations",
    "_sanitize_fabricated_data",
    "_seminal_to_bibtex",
]

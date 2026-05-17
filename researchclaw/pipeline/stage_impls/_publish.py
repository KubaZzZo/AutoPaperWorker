"""Compatibility facade for publish-stage implementations.

The Stage 21-23 implementations live in focused modules:
- ``_stage21_archive`` for knowledge archive.
- ``_stage22_export`` for export/publish packaging.
- ``_stage23_citations`` for citation verification.
- ``_fabrication_sanitizer`` for fabricated-results sanitization.
"""

from __future__ import annotations

import logging

from researchclaw.pipeline._helpers import (
    _generate_framework_diagram_prompt,
    _read_prior_artifact,
    reconcile_figure_refs,
)
from researchclaw.pipeline.stage_impls import (
    _stage21_archive,
    _stage22_export,
    _stage23_citations,
)
from researchclaw.pipeline.stage_impls._fabrication_sanitizer import _sanitize_fabricated_data
from researchclaw.pipeline.stage_impls._stage22_export import (
    _get_collect_raw_experiment_metrics,
    _get_review_compiled_pdf,
    _load_seminal_papers_by_key,
    _seminal_to_bibtex,
)
from researchclaw.pipeline.stage_impls._stage23_citations import (
    _check_citation_relevance,
    _remove_bibtex_entries,
    _remove_citations_from_text,
)

logger = logging.getLogger(__name__)

_resolve_missing_citations_impl = _stage22_export._resolve_missing_citations


def _resolve_missing_citations(*args, **kwargs):
    return _resolve_missing_citations_impl(*args, **kwargs)


def _execute_knowledge_archive(*args, **kwargs):
    return _stage21_archive._execute_knowledge_archive(*args, **kwargs)


def _execute_export_publish(*args, **kwargs):
    _stage22_export._read_prior_artifact = _read_prior_artifact
    _stage22_export._resolve_missing_citations = _resolve_missing_citations
    _stage22_export.reconcile_figure_refs = reconcile_figure_refs
    _stage22_export._generate_framework_diagram_prompt = _generate_framework_diagram_prompt
    _stage22_export._sanitize_fabricated_data = _sanitize_fabricated_data
    return _stage22_export._execute_export_publish(*args, **kwargs)


def _execute_citation_verify(*args, **kwargs):
    _stage23_citations._read_prior_artifact = _read_prior_artifact
    return _stage23_citations._execute_citation_verify(*args, **kwargs)

__all__ = [
    "_check_citation_relevance",
    "_execute_citation_verify",
    "_execute_export_publish",
    "_execute_knowledge_archive",
    "_get_collect_raw_experiment_metrics",
    "_get_review_compiled_pdf",
    "_load_seminal_papers_by_key",
    "_remove_bibtex_entries",
    "_remove_citations_from_text",
    "_resolve_missing_citations",
    "_sanitize_fabricated_data",
    "_seminal_to_bibtex",
]

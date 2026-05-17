"""Shared accessors for paper-writing helpers used by later stages."""

from __future__ import annotations

from researchclaw.pipeline.stage_impls._paper_metrics import _collect_raw_experiment_metrics
from researchclaw.pipeline.stage_impls._paper_sections import _review_compiled_pdf

__all__ = ["_collect_raw_experiment_metrics", "_review_compiled_pdf"]

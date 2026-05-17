"""Stages 16-17: Paper outline and paper draft generation.

This module keeps the historic import surface while the implementation lives
in focused paper-writing modules.
"""

from __future__ import annotations

from researchclaw.pipeline._domain import _detect_domain
from researchclaw.pipeline.stage_impls._paper_draft_builder import (
    PaperDraftBuilder,
    _execute_paper_draft,
)
from researchclaw.pipeline.stage_impls._paper_metrics import (
    _check_ablation_effectiveness,
    _collect_raw_experiment_metrics,
    _detect_result_contradictions,
)
from researchclaw.pipeline.stage_impls._paper_outline import (
    _execute_paper_outline,
    _topic_is_literature_first,
)
from researchclaw.pipeline.stage_impls._paper_sections import (
    _review_compiled_pdf,
    _validate_draft_quality,
    _write_paper_sections,
)
from researchclaw.pipeline.stage_impls.paper_draft_quality import (
    BALANCE_SECTIONS as _BALANCE_SECTIONS,
    BULLET_LENIENT_SECTIONS as _BULLET_LENIENT_SECTIONS,
)

__all__ = [
    "PaperDraftBuilder",
    "_BALANCE_SECTIONS",
    "_BULLET_LENIENT_SECTIONS",
    "_check_ablation_effectiveness",
    "_collect_raw_experiment_metrics",
    "_detect_domain",
    "_detect_result_contradictions",
    "_execute_paper_draft",
    "_execute_paper_outline",
    "_review_compiled_pdf",
    "_topic_is_literature_first",
    "_validate_draft_quality",
    "_write_paper_sections",
]

"""Default LLM-facing prompt templates for pipeline stages."""

from __future__ import annotations

from typing import Any

from researchclaw.prompt_defaults.stage_prompts_experiment import _EXPERIMENT_STAGES
from researchclaw.prompt_defaults.stage_prompts_finalization import _FINALIZATION_STAGES
from researchclaw.prompt_defaults.stage_prompts_literature import _LITERATURE_STAGES
from researchclaw.prompt_defaults.stage_prompts_scoping import _SCOPING_STAGES
from researchclaw.prompt_defaults.stage_prompts_writing import _WRITING_STAGES

_DEFAULT_STAGES: dict[str, dict[str, Any]] = {
    **_SCOPING_STAGES,
    **_LITERATURE_STAGES,
    **_EXPERIMENT_STAGES,
    **_WRITING_STAGES,
    **_FINALIZATION_STAGES,
}

__all__ = ["_DEFAULT_STAGES"]

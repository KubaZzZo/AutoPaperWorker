"""Default prompt data for ResearchClaw.

These modules keep the public prompt manager small while preserving the
legacy imports re-exported from ``researchclaw.prompts``.
"""

from researchclaw.prompt_defaults.blocks import (
    SECTION_WORD_TARGETS,
    _DEFAULT_BLOCKS,
    _SECTION_TARGET_ALIASES,
)
from researchclaw.prompt_defaults.debate_roles import (
    DEBATE_ROLES_ANALYSIS,
    DEBATE_ROLES_HYPOTHESIS,
)
from researchclaw.prompt_defaults.stage_prompts import _DEFAULT_STAGES
from researchclaw.prompt_defaults.sub_prompts import _DEFAULT_SUB_PROMPTS

__all__ = [
    "DEBATE_ROLES_ANALYSIS",
    "DEBATE_ROLES_HYPOTHESIS",
    "SECTION_WORD_TARGETS",
    "_DEFAULT_BLOCKS",
    "_DEFAULT_STAGES",
    "_DEFAULT_SUB_PROMPTS",
    "_SECTION_TARGET_ALIASES",
]

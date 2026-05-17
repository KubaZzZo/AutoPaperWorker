"""Dynamic skills library for AutoResearchClaw.

Provides a registry of reusable research/engineering/writing skills
that can be automatically matched to pipeline stages and injected
into LLM prompts.
"""

from researchclaw.skills.registry import SkillRegistry
from researchclaw.skills.schema import Skill

__all__ = ["Skill", "SkillRegistry"]

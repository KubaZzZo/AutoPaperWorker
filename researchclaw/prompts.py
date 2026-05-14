"""Prompt externalization for the ResearchClaw pipeline.

All 23 stage prompts are defined here as defaults and can be overridden
via a user-provided YAML file.  Users customize prompts without touching
Python source code.

Architecture
------------
* ``_DEFAULT_STAGES`` — every LLM-facing prompt, keyed by stage name.
* ``_DEFAULT_BLOCKS`` — reusable prompt fragments (topic constraint, etc.).
* ``_DEFAULT_SUB_PROMPTS`` — secondary prompts (code repair, etc.).
* ``PromptManager`` — loads defaults → merges user overrides → renders templates.
* ``_render()`` — safe ``{variable}`` substitution that leaves unmatched
  patterns (JSON schemas, curly-brace literals) untouched.

Usage
-----
::

    from researchclaw.prompts import PromptManager

    pm = PromptManager()                           # defaults only
    pm = PromptManager("my_prompts.yaml")          # with user overrides

    sp = pm.for_stage("topic_init", topic="RL for drug discovery", domains="ml, bio")
    resp = llm.chat(
        [{"role": "user", "content": sp.user}],
        system=sp.system,
        json_mode=sp.json_mode,
        max_tokens=sp.max_tokens,
    )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from researchclaw.prompt_defaults import (
    DEBATE_ROLES_ANALYSIS,
    DEBATE_ROLES_HYPOTHESIS,
    SECTION_WORD_TARGETS,
    _DEFAULT_BLOCKS,
    _DEFAULT_STAGES,
    _DEFAULT_SUB_PROMPTS,
    _SECTION_TARGET_ALIASES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _render(template: str, variables: dict[str, str]) -> str:
    """Replace ``{var_name}`` placeholders with *variables* values.

    Only bare ``{word_chars}`` tokens are substituted — JSON schema
    examples like ``{candidates:[...]}`` or ``{score_1_to_10:number}``
    are left untouched because the regex requires the closing ``}``
    immediately after the identifier.
    """

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)

    return re.sub(r"\{(\w+)\}", _replacer, template)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedPrompt:
    """Fully rendered prompt ready for ``llm.chat()``."""

    system: str
    user: str
    json_mode: bool = False
    max_tokens: int | None = None


# ---------------------------------------------------------------------------
# PromptManager
# ---------------------------------------------------------------------------


class PromptManager:
    """Central registry for pipeline prompts with optional YAML overrides."""

    def __init__(self, overrides_path: str | Path | None = None) -> None:
        # Deep-copy defaults so mutations don't leak across instances
        self._stages: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in _DEFAULT_STAGES.items()
        }
        self._blocks: dict[str, str] = dict(_DEFAULT_BLOCKS)
        self._sub_prompts: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in _DEFAULT_SUB_PROMPTS.items()
        }
        if overrides_path:
            self._load_overrides(Path(overrides_path))

    # -- loading ----------------------------------------------------------

    def _load_overrides(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Prompts file not found: %s — using defaults", path)
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("Bad prompts YAML %s: %s — using defaults", path, exc)
            return

        for stage_name, stage_data in (data.get("stages") or {}).items():
            if stage_name in self._stages and isinstance(stage_data, dict):
                self._stages[stage_name].update(stage_data)
            else:
                logger.warning("Unknown stage in prompts file: %s", stage_name)

        for block_name, block_text in (data.get("blocks") or {}).items():
            if isinstance(block_text, str):
                self._blocks[block_name] = block_text

        for sub_name, sub_data in (data.get("sub_prompts") or {}).items():
            if sub_name in self._sub_prompts and isinstance(sub_data, dict):
                self._sub_prompts[sub_name].update(sub_data)

        logger.info("Loaded prompt overrides from %s", path)

    # -- primary API ------------------------------------------------------

    def for_stage(
        self,
        stage: str,
        *,
        evolution_overlay: str = "",
        **kwargs: Any,
    ) -> RenderedPrompt:
        """Return a fully rendered prompt for *stage* with variables filled.

        If *evolution_overlay* is provided, it is appended to the user prompt
        so the LLM can learn from prior run lessons.
        """
        entry = self._stages[stage]
        kw = {k: str(v) for k, v in kwargs.items()}
        user_text = _render(entry["user"], kw)
        if evolution_overlay:
            user_text = f"{user_text}\n\n{evolution_overlay}"
        return RenderedPrompt(
            system=_render(entry["system"], kw),
            user=user_text,
            json_mode=entry.get("json_mode", False),
            max_tokens=entry.get("max_tokens"),
        )

    def system(self, stage: str) -> str:
        """Return the raw system prompt template for *stage*."""
        return self._stages[stage]["system"]

    def user(self, stage: str, **kwargs: Any) -> str:
        """Return the rendered user prompt for *stage*."""
        return _render(
            self._stages[stage]["user"],
            {k: str(v) for k, v in kwargs.items()},
        )

    def json_mode(self, stage: str) -> bool:
        return self._stages[stage].get("json_mode", False)

    def max_tokens(self, stage: str) -> int | None:
        return self._stages[stage].get("max_tokens")

    # -- blocks -----------------------------------------------------------

    def block(self, name: str, **kwargs: Any) -> str:
        """Render a reusable prompt block."""
        return _render(
            self._blocks[name],
            {k: str(v) for k, v in kwargs.items()},
        )

    # -- sub-prompts (code repair, etc.) ----------------------------------

    def sub_prompt(self, name: str, **kwargs: Any) -> RenderedPrompt:
        """Return a rendered sub-prompt (e.g. code_repair)."""
        entry = self._sub_prompts[name]
        kw = {k: str(v) for k, v in kwargs.items()}
        return RenderedPrompt(
            system=_render(entry["system"], kw),
            user=_render(entry["user"], kw),
        )

    # -- introspection ----------------------------------------------------

    def stage_names(self) -> list[str]:
        return list(self._stages.keys())

    def has_stage(self, stage: str) -> bool:
        return stage in self._stages

    def export_yaml(self, path: Path) -> None:
        """Write current prompts (defaults + overrides) to a YAML file."""
        data: dict[str, Any] = {
            "version": "1.0",
            "blocks": dict(self._blocks),
            "stages": {k: dict(v) for k, v in self._stages.items()},
            "sub_prompts": {k: dict(v) for k, v in self._sub_prompts.items()},
        }
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, width=120),
            encoding="utf-8",
        )

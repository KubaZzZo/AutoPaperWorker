from __future__ import annotations

from pathlib import Path

from researchclaw.prompts import (
    DEBATE_ROLES_ANALYSIS,
    DEBATE_ROLES_HYPOTHESIS,
    PromptManager,
)


def test_prompt_manager_keeps_default_stage_and_sub_prompt_behavior() -> None:
    manager = PromptManager()

    assert manager.has_stage("topic_init")

    stage_prompt = manager.for_stage(
        "topic_init",
        topic="AI safety",
        domains="ml",
    )
    assert "AI safety" in stage_prompt.user
    assert stage_prompt.system
    assert stage_prompt.json_mode is False

    repair_prompt = manager.sub_prompt(
        "code_repair",
        fname="main.py",
        issues_text="boom",
        all_files_ctx="```filename:main.py\nprint(1)\n```",
    )
    assert "print(1)" in repair_prompt.user
    assert "boom" in repair_prompt.user


def test_prompt_public_constants_remain_available_from_prompts_module() -> None:
    assert DEBATE_ROLES_HYPOTHESIS
    assert DEBATE_ROLES_ANALYSIS
    assert "innovator" in DEBATE_ROLES_HYPOTHESIS
    assert "optimist" in DEBATE_ROLES_ANALYSIS


def test_prompt_overrides_still_merge_after_defaults_move(tmp_path: Path) -> None:
    overrides = tmp_path / "prompts.yaml"
    overrides.write_text(
        """
stages:
  topic_init:
    system: Override system for {topic}
    user: Override user for {topic} in {domains}
    json_mode: false
blocks:
  custom_block: Hello {person}
sub_prompts:
  code_repair:
    user: Repair {code} because {error}
""",
        encoding="utf-8",
    )

    manager = PromptManager(overrides)

    stage_prompt = manager.for_stage(
        "topic_init",
        topic="graph learning",
        domains="ml",
    )
    assert stage_prompt.system == "Override system for graph learning"
    assert stage_prompt.user == "Override user for graph learning in ml"
    assert stage_prompt.json_mode is False
    assert manager.block("custom_block", person="Ada") == "Hello Ada"
    assert manager.sub_prompt(
        "code_repair",
        code="x = 1",
        error="bad",
    ).user == "Repair x = 1 because bad"

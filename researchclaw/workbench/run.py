"""Pipeline launch helpers for the workbench."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import (
    DEFAULT_ARTIFACTS_DIR,
    ExperimentConfig,
    KnowledgeBaseConfig,
    NotificationsConfig,
    OpenClawBridgeConfig,
    ProjectConfig,
    RCConfig,
    ResearchConfig,
    RuntimeConfig,
)
from researchclaw.pipeline.runner import execute_pipeline
from researchclaw.pipeline.stages import Stage
from researchclaw.workbench.models import build_model_config


def default_workbench_config(topic: str) -> RCConfig:
    """Create a minimal valid config for workbench operations."""
    return RCConfig(
        project=ProjectConfig(name="AutoPaperWorker Workbench", mode="full-auto"),
        research=ResearchConfig(topic=topic),
        runtime=RuntimeConfig(timezone="Asia/Shanghai"),
        notifications=NotificationsConfig(channel="none"),
        knowledge_base=KnowledgeBaseConfig(backend="markdown", root="kb"),
        openclaw_bridge=OpenClawBridgeConfig(),
        llm=build_model_config(mode="cloud", provider="openai"),
        experiment=ExperimentConfig(mode="simulated"),
    )


def build_workbench_config(
    *,
    topic: str,
    base_config: RCConfig | None = None,
    provider: str = "openai",
    model: str = "",
    api_key_env: str = "",
    base_url: str = "",
    model_mode: str = "cloud",
    experiment_mode: str = "simulated",
) -> RCConfig:
    """Build a runtime config for a workbench run without mutating files."""
    config = base_config or default_workbench_config(topic)
    return replace(
        config.with_research_overrides(topic=topic),
        llm=build_model_config(
            mode=model_mode,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
        ),
        experiment=replace(config.experiment, mode=experiment_mode),
    )


def run_workbench_pipeline(
    *,
    topic: str,
    output: str | Path | None = None,
    provider: str = "openai",
    model: str = "",
    api_key_env: str = "",
    base_url: str = "",
    model_mode: str = "cloud",
    experiment_mode: str = "simulated",
    progress_reporter=None,
) -> Path:
    """Start the existing pipeline from a workbench-friendly config."""
    from researchclaw.cli import _generate_run_id

    config = build_workbench_config(
        topic=topic,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        base_url=base_url,
        model_mode=model_mode,
        experiment_mode=experiment_mode,
    )
    run_id = _generate_run_id(topic)
    run_dir = Path(output) if output else DEFAULT_ARTIFACTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    execute_pipeline(
        run_dir=run_dir,
        run_id=run_id,
        config=config,
        adapters=AdapterBundle(),
        from_stage=Stage.TOPIC_INIT,
        auto_approve_gates=True,
        stop_on_gate=False,
        progress_reporter=progress_reporter,
    )
    return run_dir

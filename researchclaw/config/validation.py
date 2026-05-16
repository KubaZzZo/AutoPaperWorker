"""Validation helpers for ResearchClaw configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from researchclaw.config.defaults import (
    CLI_AGENT_PROVIDERS,
    EXPERIMENT_MODES,
    KB_BACKENDS,
    KB_SUBDIRS,
    MAX_CONFIG_NESTING_DEPTH,
    PROJECT_MODES,
    REQUIRED_FIELDS,
)
from researchclaw.config.schema import ValidationResult
from researchclaw.llm import PROVIDER_DETAILS


def _config_depth_error(data: object, max_depth: int = MAX_CONFIG_NESTING_DEPTH) -> str | None:
    """Return an error if a config object exceeds the supported nesting depth."""
    stack: list[tuple[object, int, str]] = [(data, 0, "config")]
    while stack:
        value, depth, path = stack.pop()
        if depth > max_depth:
            return f"Config exceeds maximum nesting depth ({max_depth}) at {path}"
        if isinstance(value, dict):
            for key, child in value.items():
                stack.append((child, depth + 1, f"{path}.{key}"))
        elif isinstance(value, (list, tuple)):
            for idx, child in enumerate(value):
                stack.append((child, depth + 1, f"{path}[{idx}]"))
    return None


def _get_by_path(data: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_config(
    data: dict[str, Any],
    *,
    project_root: Path | None = None,
    check_paths: bool = True,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    depth_error = _config_depth_error(data)
    if depth_error is not None:
        errors.append(depth_error)
        return ValidationResult(ok=False, errors=tuple(errors), warnings=())

    llm_provider = _get_by_path(data, "llm.provider")
    for key in REQUIRED_FIELDS:
        # ACP provider doesn't need base_url or api_key_env
        if llm_provider == "acp" and key in ("llm.base_url", "llm.api_key_env"):
            continue
        value = _get_by_path(data, key)
        if _is_blank(value):
            errors.append(f"Missing required field: {key}")

    project_mode = _get_by_path(data, "project.mode")
    if not _is_blank(project_mode) and project_mode not in PROJECT_MODES:
        errors.append(f"Invalid project.mode: {project_mode}")

    if not _is_blank(llm_provider) and llm_provider not in PROVIDER_DETAILS:
        errors.append(f"Invalid llm.provider: {llm_provider}")

    kb_backend = _get_by_path(data, "knowledge_base.backend")
    if not _is_blank(kb_backend) and kb_backend not in KB_BACKENDS:
        errors.append(f"Invalid knowledge_base.backend: {kb_backend}")

    llm_wire_api = _get_by_path(data, "llm.wire_api")
    if not _is_blank(llm_wire_api) and llm_wire_api not in (
        "chat_completions",
        "responses",
    ):
        errors.append(f"Invalid llm.wire_api: {llm_wire_api}")

    inline_llm_api_key = _get_by_path(data, "llm.api_key")
    if not _is_blank(inline_llm_api_key):
        errors.append(
            "llm.api_key is deprecated and must not be stored in YAML; "
            "set llm.api_key_env instead."
        )

    hitl_required_stages = _get_by_path(data, "security.hitl_required_stages")
    if hitl_required_stages is not None:
        if not isinstance(hitl_required_stages, list):
            errors.append("security.hitl_required_stages must be a list")
        else:
            for stage in hitl_required_stages:
                if not isinstance(stage, int) or not 1 <= stage <= 23:
                    errors.append(
                        f"Invalid security.hitl_required_stages entry: {stage}"
                    )

    exp_mode = _get_by_path(data, "experiment.mode")
    if not _is_blank(exp_mode) and exp_mode not in EXPERIMENT_MODES:
        errors.append(f"Invalid experiment.mode: {exp_mode}")

    exp_direction = _get_by_path(data, "experiment.metric_direction")
    if not _is_blank(exp_direction) and exp_direction not in ("minimize", "maximize"):
        errors.append(f"Invalid experiment.metric_direction: {exp_direction}")

    cli_agent_provider = _get_by_path(data, "experiment.cli_agent.provider")
    if (
        not _is_blank(cli_agent_provider)
        and cli_agent_provider not in CLI_AGENT_PROVIDERS
    ):
        errors.append(f"Invalid experiment.cli_agent.provider: {cli_agent_provider}")

    kb_root_raw = _get_by_path(data, "knowledge_base.root")
    if check_paths and not _is_blank(kb_root_raw) and project_root is not None:
        kb_root = project_root / str(kb_root_raw)
        if not kb_root.exists():
            errors.append(f"Missing path: {kb_root}")
        else:
            for subdir in KB_SUBDIRS:
                candidate = kb_root / subdir
                if not candidate.exists():
                    warnings.append(f"Missing recommended kb subdir: {candidate}")

    return ValidationResult(
        ok=not errors, errors=tuple(errors), warnings=tuple(warnings)
    )



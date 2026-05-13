"""LLM integration — OpenAI-compatible and ACP agent clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from researchclaw.config import RCConfig
    from researchclaw.llm.acp_client import ACPClient
    from researchclaw.llm.client import LLMClient


@dataclass(frozen=True)
class ProviderPreset:
    """Shared metadata for a built-in LLM provider."""

    provider: str
    base_url: str | None
    api_key_env: str
    primary_model: str
    fallback_models: tuple[str, ...]
    menu_label: str | None = None


# Provider presets for common LLM services.
#
# Keep endpoint URLs and credentials metadata here so runtime clients, setup
# wizards, and CLI config generation all read from the same provider source.
PROVIDER_DETAILS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        primary_model="gpt-4o",
        fallback_models=("gpt-4.1", "gpt-4o-mini"),
    ),
    "openrouter": ProviderPreset(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        primary_model="anthropic/claude-3.5-sonnet",
        fallback_models=(
            "google/gemini-pro-1.5",
            "meta-llama/llama-3.1-70b-instruct",
        ),
    ),
    "deepseek": ProviderPreset(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        primary_model="deepseek-chat",
        fallback_models=("deepseek-reasoner",),
    ),
    "anthropic": ProviderPreset(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        primary_model="claude-3-5-sonnet-latest",
        fallback_models=(),
    ),
    "kimi-anthropic": ProviderPreset(
        provider="kimi-anthropic",
        base_url="https://api.kimi.com/coding/",
        api_key_env="KIMI_API_KEY",
        primary_model="kimi-k2",
        fallback_models=(),
    ),
    "novita": ProviderPreset(
        provider="novita",
        base_url="https://api.novita.ai/openai",
        api_key_env="NOVITA_API_KEY",
        primary_model="meta-llama/llama-3.1-70b-instruct",
        fallback_models=(),
    ),
    "minimax": ProviderPreset(
        provider="minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_API_KEY",
        primary_model="MiniMax-M2.5",
        fallback_models=("MiniMax-M2.5-highspeed",),
    ),
    "volcengine": ProviderPreset(
        provider="volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="VOLCENGINE_API_KEY",
        primary_model="doubao-seed-2-0-pro-260215",
        fallback_models=(
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-mini-260215",
            "doubao-seed-2-0-code-preview-260215",
            "kimi-k2-5-260127",
            "glm-4-7-251222",
            "deepseek-v3-2-251201",
        ),
    ),
    "volcengine-coding-plan": ProviderPreset(
        provider="volcengine-coding-plan",
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        api_key_env="VOLCENGINE_API_KEY",
        primary_model="doubao-seed-2.0-code",
        fallback_models=(
            "doubao-seed-2.0-pro",
            "doubao-seed-2.0-lite",
            "doubao-seed-code",
            "minimax-m2.5",
            "glm-4.7",
            "deepseek-v3.2",
            "kimi-k2.5",
        ),
    ),
    "byteplus": ProviderPreset(
        provider="byteplus",
        base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
        api_key_env="BYTEPLUS_API_KEY",
        primary_model="seed-2-0-pro-260328",
        fallback_models=(
            "seed-2-0-lite-260228",
            "seed-2-0-mini-260215",
            "kimi-k2-5-260127",
            "glm-4-7-251222",
        ),
    ),
    "byteplus-coding-plan": ProviderPreset(
        provider="byteplus-coding-plan",
        base_url="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        api_key_env="BYTEPLUS_API_KEY",
        primary_model="dola-seed-2.0-pro",
        fallback_models=(
            "dola-seed-2.0-lite",
            "bytedance-seed-code",
            "glm-4.7",
            "kimi-k2.5",
            "gpt-oss-120b",
        ),
    ),
    "gemini": ProviderPreset(
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
        primary_model="gemini-2.0-flash",
        fallback_models=(),
    ),
    "openai-compatible": ProviderPreset(
        provider="openai-compatible",
        base_url=None,
        api_key_env="OPENAI_API_KEY",
        primary_model="gpt-4o",
        fallback_models=("gpt-4.1", "gpt-4o-mini"),
    ),
    "acp": ProviderPreset(
        provider="acp",
        base_url=None,
        api_key_env="",
        primary_model="",
        fallback_models=(),
        menu_label="local AI agent - no API key needed",
    ),
}

PROVIDER_PRESETS = {
    provider: {"base_url": preset.base_url}
    for provider, preset in PROVIDER_DETAILS.items()
    if provider != "acp"
}

CLI_PROVIDER_ORDER = (
    "openai",
    "openrouter",
    "deepseek",
    "minimax",
    "volcengine",
    "volcengine-coding-plan",
    "byteplus",
    "byteplus-coding-plan",
    "acp",
)


def cli_provider_choices() -> dict[str, tuple[str, str]]:
    """Return numbered setup choices from the shared provider metadata."""

    return {
        str(index): (
            provider,
            PROVIDER_DETAILS[provider].api_key_env,
        )
        for index, provider in enumerate(CLI_PROVIDER_ORDER, start=1)
    }


def provider_base_urls() -> dict[str, str]:
    """Return configured base URLs for providers that define endpoints."""

    return {
        provider: preset.base_url
        for provider, preset in PROVIDER_DETAILS.items()
        if preset.base_url
    }


def provider_model_defaults() -> dict[str, tuple[str, list[str]]]:
    """Return default primary/fallback models for setup config generation."""

    return {
        provider: (preset.primary_model, list(preset.fallback_models))
        for provider, preset in PROVIDER_DETAILS.items()
        if preset.primary_model
    }


def cli_provider_menu_lines() -> list[str]:
    """Return user-facing setup menu lines derived from provider metadata."""

    lines: list[str] = []
    for index, provider in enumerate(CLI_PROVIDER_ORDER, start=1):
        preset = PROVIDER_DETAILS[provider]
        label = preset.menu_label or f"requires {preset.api_key_env}"
        lines.append(f"  {index}) {provider:<23} ({label})")
    return lines

def create_llm_client(config: RCConfig) -> LLMClient | ACPClient:
    """Factory: return the right LLM client based on ``config.llm.provider``.

    - ``"acp"`` → :class:`ACPClient` (spawns an ACP-compatible agent)
    - ``"anthropic"`` → :class:`LLMClient` with Anthropic Messages API adapter
    - ``"kimi-anthropic"`` → :class:`LLMClient` with Kimi Coding Anthropic adapter
    - ``"openrouter"`` → :class:`LLMClient` with OpenRouter base URL
    - ``"openai"`` → :class:`LLMClient` with OpenAI base URL
    - ``"deepseek"`` → :class:`LLMClient` with DeepSeek base URL
    - ``"novita"`` → :class:`LLMClient` with Novita AI base URL
    - ``"minimax"`` → :class:`LLMClient` with MiniMax base URL
    - ``"volcengine"`` → :class:`LLMClient` with Volcengine ARK base URL
    - ``"volcengine-coding-plan"`` → :class:`LLMClient` with Volcengine
      Coding Plan base URL
    - ``"byteplus"`` → :class:`LLMClient` with BytePlus ModelArk base URL
    - ``"byteplus-coding-plan"`` → :class:`LLMClient` with BytePlus
      Coding Plan base URL
    - ``"gemini"`` → :class:`LLMClient` with Gemini Native Adapter
    - ``"openai-compatible"`` (default) → :class:`LLMClient` with custom base_url

    OpenRouter is fully compatible with the OpenAI API format, making it
    a drop-in replacement with access to 200+ models from Anthropic, Google,
    Meta, Mistral, and more. See: https://openrouter.ai/models
    """
    if config.llm.provider == "acp":
        from researchclaw.llm.acp_client import ACPClient as _ACP
        return _ACP.from_rc_config(config)

    from researchclaw.llm.client import LLMClient as _LLM

    # Use from_rc_config to properly initialize adapters (e.g., Anthropic)
    return _LLM.from_rc_config(config)

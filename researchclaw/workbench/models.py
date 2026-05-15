"""Model configuration helpers for the workbench."""

from __future__ import annotations

from researchclaw.config import LlmConfig
from researchclaw.llm import PROVIDER_DETAILS


def build_model_config(
    *,
    mode: str,
    provider: str = "openai",
    model: str = "",
    base_url: str = "",
    api_key_env: str = "",
) -> LlmConfig:
    """Build an ``LlmConfig`` from workbench form values.

    ``mode="local"`` means an OpenAI-compatible local endpoint such as
    Ollama, vLLM, or LM Studio. Cloud providers use the shared provider table
    when possible.
    """
    mode_norm = (mode or "cloud").strip().lower()
    provider_norm = (provider or "openai").strip()
    if mode_norm == "local":
        return LlmConfig(
            provider="openai-compatible",
            base_url=base_url.strip() or "http://127.0.0.1:11434/v1",
            api_key_env=api_key_env.strip(),
            primary_model=model.strip(),
        )

    details = PROVIDER_DETAILS.get(provider_norm)
    return LlmConfig(
        provider=provider_norm,
        base_url=base_url.strip() or (details.base_url if details and details.base_url else ""),
        api_key_env=api_key_env.strip() or (details.api_key_env if details else ""),
        primary_model=model.strip() or (details.primary_model if details else ""),
    )

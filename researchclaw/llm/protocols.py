"""Shared structural protocols for LLM clients and provider adapters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMResponseProtocol(Protocol):
    """Minimum response surface consumed by ResearchClaw agents."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@runtime_checkable
class ChatLLMClientProtocol(Protocol):
    """Shared interface for high-level chat LLM clients."""

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponseProtocol: ...

    def chat_json(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]: ...


LLMClientProtocol = ChatLLMClientProtocol


@runtime_checkable
class ProviderAdapterProtocol(Protocol):
    """Interface implemented by native provider adapters."""

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        json_mode: bool = False,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...

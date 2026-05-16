"""Custom exception hierarchy for ResearchClaw.

The hierarchy gives callers stable, domain-specific catch points while keeping
compatibility with historical ``ValueError`` and ``RuntimeError`` handlers.
"""

from __future__ import annotations

from urllib.error import URLError


class ResearchClawError(Exception):
    """Base class for all ResearchClaw-specific errors."""


class PipelineError(ResearchClawError, RuntimeError):
    """Base class for pipeline orchestration and stage execution errors."""


class StageFailedError(PipelineError):
    """Raised when a pipeline stage fails in a caller-visible way."""


class PipelineTransitionError(PipelineError, ValueError):
    """Raised when a stage-status transition is invalid."""


class LLMError(ResearchClawError, RuntimeError):
    """Base class for LLM provider, transport, and response errors."""


class LLMRateLimitError(LLMError):
    """Raised when an LLM provider reports rate limiting."""


class MalformedLLMResponseError(LLMError, ValueError):
    """Raised when an LLM provider returns an unexpected payload shape."""


class SandboxError(ResearchClawError, RuntimeError):
    """Base class for experiment sandbox setup and execution errors."""


class SandboxConfigurationError(SandboxError):
    """Raised when sandbox configuration cannot create a valid backend."""


class ConfigValidationError(ResearchClawError, ValueError):
    """Raised when configuration validation fails."""


class WebError(ResearchClawError, RuntimeError):
    """Base class for web fetching, crawling, and retrieval errors."""


class SSRFBlockedError(WebError, URLError):
    """Raised when SSRF protection blocks a URL or connected peer."""


__all__ = [
    "ConfigValidationError",
    "LLMError",
    "LLMRateLimitError",
    "MalformedLLMResponseError",
    "PipelineError",
    "PipelineTransitionError",
    "ResearchClawError",
    "SSRFBlockedError",
    "SandboxConfigurationError",
    "SandboxError",
    "StageFailedError",
    "WebError",
]

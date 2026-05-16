"""Architecture checks for ResearchClaw's custom exception hierarchy."""

from __future__ import annotations

import pytest


def test_custom_exception_hierarchy_is_public() -> None:
    from researchclaw.exceptions import (
        ConfigValidationError,
        LLMError,
        LLMRateLimitError,
        PipelineError,
        ResearchClawError,
        SSRFBlockedError,
        SandboxError,
        StageFailedError,
        WebError,
    )

    assert issubclass(PipelineError, ResearchClawError)
    assert issubclass(StageFailedError, PipelineError)
    assert issubclass(LLMError, ResearchClawError)
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(SandboxError, ResearchClawError)
    assert issubclass(ConfigValidationError, ResearchClawError)
    assert issubclass(WebError, ResearchClawError)
    assert issubclass(SSRFBlockedError, WebError)


def test_config_validation_raises_specific_exception(tmp_path) -> None:
    from researchclaw.config import RCConfig
    from researchclaw.exceptions import ConfigValidationError
    from tests.test_rc_config import _valid_config_data

    data = _valid_config_data()
    data["llm"]["api_key"] = "sk-inline-secret"

    with pytest.raises(ConfigValidationError, match="llm.api_key"):
        RCConfig.from_dict(data, project_root=tmp_path, check_paths=False)


def test_sandbox_factory_raises_specific_exception(tmp_path) -> None:
    from researchclaw.config import ExperimentConfig, SshRemoteConfig
    from researchclaw.exceptions import SandboxError
    from researchclaw.experiment.factory import create_sandbox

    cfg = ExperimentConfig(
        mode="ssh_remote",
        ssh_remote=SshRemoteConfig(host=""),
    )

    with pytest.raises(SandboxError, match="host"):
        create_sandbox(cfg, tmp_path)


def test_pipeline_transition_raises_specific_exception() -> None:
    from researchclaw.exceptions import PipelineError
    from researchclaw.pipeline.stages import Stage, StageStatus, TransitionEvent, advance

    with pytest.raises(PipelineError, match="Unsupported transition"):
        advance(Stage.TOPIC_INIT, StageStatus.DONE, TransitionEvent.START)


def test_llm_malformed_response_raises_specific_exception() -> None:
    from researchclaw.exceptions import LLMError
    from researchclaw.llm.client import LLMClient, LLMConfig

    client = LLMClient(
        LLMConfig(
            base_url="https://example.invalid/v1",
            api_key="test",
        )
    )

    with pytest.raises(LLMError, match="Malformed API response"):
        client._parse_chat_completions_response({"choices": []}, "model")


def test_llm_retry_exhaustion_preserves_rate_limit_type(monkeypatch) -> None:
    import urllib.error
    from email.message import Message

    from researchclaw.exceptions import LLMRateLimitError
    from researchclaw.llm.client import LLMClient, LLMConfig

    client = LLMClient(
        LLMConfig(
            base_url="https://example.invalid/v1",
            api_key="test",
            max_retries=1,
        )
    )

    def raise_429(*_args: object, **_kwargs: object):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", Message(), None)

    monkeypatch.setattr(client, "_raw_call", raise_429)
    monkeypatch.setattr("researchclaw.llm.client.time.sleep", lambda *_args: None)

    with pytest.raises(LLMRateLimitError, match="LLM call failed"):
        client._call_with_retry("model", [{"role": "user", "content": "hi"}], 10, 0, False)


def test_llm_chat_preserves_rate_limit_type(monkeypatch) -> None:
    from researchclaw.exceptions import LLMRateLimitError
    from researchclaw.llm.client import LLMClient, LLMConfig

    client = LLMClient(
        LLMConfig(
            base_url="https://example.invalid/v1",
            api_key="test",
            primary_model="primary",
            fallback_models=[],
        )
    )

    def raise_rate_limit(*_args: object, **_kwargs: object):
        raise LLMRateLimitError("rate limited")

    monkeypatch.setattr(client, "_call_with_retry", raise_rate_limit)

    with pytest.raises(LLMRateLimitError, match="All models failed"):
        client.chat([{"role": "user", "content": "hi"}])

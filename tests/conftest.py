"""Shared pytest fixtures for ResearchClaw tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMResponse


class MockLLMClient:
    """Configurable sync/async LLM fake shared by tests."""

    def __init__(self, responses: str | list[str] | None = None, *, model: str = "fake-model"):
        if responses is None:
            responses = "mock response"
        self._responses = [responses] if isinstance(responses, str) else list(responses)
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self.async_calls: list[str] = []
        self._call_idx = 0

    def _next_response(self) -> str:
        if not self._responses:
            return ""
        idx = min(self._call_idx, len(self._responses) - 1)
        self._call_idx += 1
        return self._responses[idx]

    def set_responses(self, responses: str | list[str]) -> None:
        self._responses = [responses] if isinstance(responses, str) else list(responses)
        self._call_idx = 0

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.calls.append({"messages": messages, **kwargs})
        return LLMResponse(content=self._next_response(), model=self.model)

    def chat_json(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs, "json_mode": True})
        return {"content": self._next_response()}

    async def chat_async(self, prompt: str) -> str:
        self.async_calls.append(prompt)
        return self._next_response()


@dataclass(frozen=True)
class TestPipelineContext:
    """Small test-only bundle for pipeline-oriented fixtures."""

    config: RCConfig
    llm: MockLLMClient
    adapters: AdapterBundle
    run_dir: Path
    project_root: Path


@pytest.fixture()
def mock_llm_client() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture()
def tmp_config(tmp_path: Path) -> RCConfig:
    data = {
        "project": {"name": "rc-test", "mode": "docs-first"},
        "research": {
            "topic": "test-driven science",
            "domains": ["ml", "systems"],
            "daily_paper_count": 2,
            "quality_threshold": 8.2,
        },
        "runtime": {"timezone": "UTC"},
        "notifications": {
            "channel": "local",
            "on_stage_start": True,
            "on_stage_fail": False,
            "on_gate_required": True,
        },
        "knowledge_base": {"backend": "markdown", "root": str(tmp_path / "kb")},
        "openclaw_bridge": {"use_memory": True, "use_message": True},
        "llm": {
            "provider": "openai-compatible",
            "base_url": "http://localhost:1234/v1",
            "api_key_env": "RC_TEST_KEY",
            "primary_model": "fake-model",
            "fallback_models": [],
        },
        "security": {"hitl_required_stages": [5, 9, 20]},
        "experiment": {"mode": "sandbox"},
        "web_search": {"enabled": False},
    }
    return RCConfig.from_dict(data, project_root=tmp_path, check_paths=False)


@pytest.fixture()
def adapter_bundle() -> AdapterBundle:
    return AdapterBundle()


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run"
    path.mkdir()
    return path


@pytest.fixture()
def pipeline_context(
    tmp_config: RCConfig,
    mock_llm_client: MockLLMClient,
    adapter_bundle: AdapterBundle,
    run_dir: Path,
    tmp_path: Path,
) -> TestPipelineContext:
    return TestPipelineContext(
        config=tmp_config,
        llm=mock_llm_client,
        adapters=adapter_bundle,
        run_dir=run_dir,
        project_root=tmp_path,
    )

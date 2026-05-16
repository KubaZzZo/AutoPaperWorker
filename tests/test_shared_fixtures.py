from __future__ import annotations

from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMResponse


def test_mock_llm_client_records_sync_and_async_calls(mock_llm_client):
    response = mock_llm_client.chat(
        [{"role": "user", "content": "hello"}], temperature=0.1
    )

    assert isinstance(response, LLMResponse)
    assert response.content == "mock response"
    assert mock_llm_client.calls == [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.1,
        }
    ]


def test_tmp_config_uses_local_test_defaults(tmp_config):
    assert isinstance(tmp_config, RCConfig)
    assert tmp_config.project.name == "rc-test"
    assert tmp_config.llm.provider == "openai-compatible"
    assert tmp_config.llm.primary_model == "fake-model"
    assert tmp_config.web_search.enabled is False


def test_pipeline_context_bundles_common_pipeline_dependencies(pipeline_context):
    assert isinstance(pipeline_context.config, RCConfig)
    assert isinstance(pipeline_context.adapters, AdapterBundle)
    assert isinstance(pipeline_context.run_dir, Path)
    assert pipeline_context.run_dir.exists()
    assert pipeline_context.project_root.exists()
    assert pipeline_context.llm.chat([{"role": "user", "content": "x"}]).model == "fake-model"


def test_mock_llm_client_can_reset_response_sequence(mock_llm_client):
    mock_llm_client.set_responses(["first", "second"])

    assert mock_llm_client.chat([{"role": "user", "content": "x"}]).content == "first"
    assert mock_llm_client.chat([{"role": "user", "content": "y"}]).content == "second"
    assert mock_llm_client.chat([{"role": "user", "content": "z"}]).content == "second"

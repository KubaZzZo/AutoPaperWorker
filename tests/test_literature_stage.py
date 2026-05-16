"""Focused tests for literature stage implementations."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from researchclaw.adapters import AdapterBundle, FetchResponse, RecordingWebFetchAdapter
from researchclaw.config import WebSearchConfig
from researchclaw.llm.client import LLMResponse
from researchclaw.pipeline.stage_impls import _literature
from researchclaw.pipeline.stages import StageStatus
from researchclaw.workbench.run import default_workbench_config


class RecordingLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return LLMResponse(content=self.response, model="fake-model")


class SimplePrompts:
    def __init__(self, *, json_mode: bool = True) -> None:
        self.json_mode = json_mode
        self.calls: list[dict[str, Any]] = []

    def for_stage(self, stage: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"stage": stage, "kwargs": kwargs})
        return SimpleNamespace(
            system=f"system:{stage}",
            user=f"user:{stage}",
            json_mode=self.json_mode,
            max_tokens=500,
        )


class StatusWebFetchAdapter(RecordingWebFetchAdapter):
    def __init__(self, statuses: list[int]) -> None:
        super().__init__()
        self.statuses = statuses

    def fetch(self, url: str) -> FetchResponse:
        self.calls.append(url)
        status = self.statuses[min(len(self.calls) - 1, len(self.statuses) - 1)]
        return FetchResponse(url=url, status_code=status, text="ok")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_search_strategy_sanitizes_queries_and_verifies_sources(tmp_path: Path) -> None:
    config = replace(
        default_workbench_config(
            "Robust graph neural network calibration under distribution shift for medical imaging"
        ),
        openclaw_bridge=replace(default_workbench_config("x").openclaw_bridge, use_web_fetch=True),
    )
    stage_dir = tmp_path / "run" / "stage-03"
    run_dir = tmp_path / "run"
    stage_dir.mkdir(parents=True)
    payload = json.dumps(
        {
            "search_plan_yaml": "\n".join(
                [
                    "search_strategies:",
                    "  - name: broad",
                    "    queries:",
                    "      - Robust graph neural network calibration under distribution shift for medical imaging with extensive uncertainty analysis benchmark",
                    "      - graph neural calibration",
                    "      - graph neural calibration",
                    "filters:",
                    "  min_year: 2019",
                ]
            ),
            "sources": [
                {"id": "ok", "url": "https://example.test/ok"},
                {"id": "bad", "url": "https://example.test/bad"},
            ],
        }
    )
    web_fetch = StatusWebFetchAdapter([200, 500])

    result = _literature._execute_search_strategy(
        stage_dir,
        run_dir,
        config,
        AdapterBundle(web_fetch=web_fetch),
        llm=RecordingLLM(payload),
        prompts=SimplePrompts(),
    )

    assert result.status is StageStatus.DONE
    assert web_fetch.calls == ["https://example.test/ok", "https://example.test/bad"]
    sources = _read_json(stage_dir / "sources.json")["sources"]
    assert sources[0]["status"] == "verified"
    assert sources[1]["status"] == "unreachable"
    queries = _read_json(stage_dir / "queries.json")
    assert queries["year_min"] == 2019
    assert len(queries["queries"]) >= 5
    assert all(len(q) <= 80 for q in queries["queries"])
    assert len({q.lower() for q in queries["queries"]}) == len(queries["queries"])


def test_literature_collect_uses_llm_candidates_and_generates_bibtex(tmp_path: Path, monkeypatch) -> None:
    config = replace(default_workbench_config("neural retrieval benchmark"), web_search=WebSearchConfig(enabled=False))
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-04"
    stage_dir.mkdir(parents=True)
    _write_json(run_dir / "stage-03" / "queries.json", {"queries": ["neural retrieval"], "year_min": 2021})
    _write_text(run_dir / "stage-03" / "search_plan.yaml", "queries: [neural retrieval]")
    monkeypatch.setattr("researchclaw.literature.search.search_papers_multi_query", lambda *a, **k: [])
    monkeypatch.setattr("researchclaw.data.load_seminal_papers", lambda topic: [])
    llm = RecordingLLM(
        json.dumps(
            {
                "candidates": [
                    {
                        "title": "Neural Retrieval Benchmarks",
                        "year": 2022,
                        "url": "https://example.test/paper",
                        "authors": [{"name": "Ada Lovelace"}],
                    }
                ]
            }
        )
    )

    result = _literature._execute_literature_collect(
        stage_dir,
        run_dir,
        config,
        AdapterBundle(),
        llm=llm,
        prompts=SimplePrompts(),
    )

    assert result.status is StageStatus.DONE
    rows = _jsonl_rows(stage_dir / "candidates.jsonl")
    assert rows[0]["title"] == "Neural Retrieval Benchmarks"
    bib = (stage_dir / "references.bib").read_text(encoding="utf-8")
    assert "lovelace2022nrb" in bib
    meta = _read_json(stage_dir / "search_meta.json")
    assert meta["real_search"] is False
    assert meta["bibtex_entries"] == 1


def test_literature_screen_filters_truncates_and_supplements_shortlist(tmp_path: Path) -> None:
    config = default_workbench_config("graph calibration benchmark")
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-05"
    stage_dir.mkdir(parents=True)
    candidates = [
        {
            "title": f"Graph calibration benchmark paper {idx}",
            "abstract": "graph calibration benchmark " + ("x" * 900),
            "authors": [{"name": "Author"}],
        }
        for idx in range(18)
    ]
    candidates.append({"title": "Unrelated biology paper", "abstract": "cell assay only"})
    _write_text(
        run_dir / "stage-04" / "candidates.jsonl",
        "\n".join(json.dumps(c) for c in candidates),
    )
    llm = RecordingLLM(json.dumps({"shortlist": [{"title": "Graph calibration benchmark paper 0"}]}))

    result = _literature._execute_literature_screen(
        stage_dir,
        run_dir,
        config,
        AdapterBundle(),
        llm=llm,
        prompts=SimplePrompts(),
    )

    assert result.status is StageStatus.DONE
    rows = _jsonl_rows(stage_dir / "shortlist.jsonl")
    assert len(rows) == 15
    assert rows[0]["title"] == "Graph calibration benchmark paper 0"
    assert any(row.get("keep_reason") == "Supplemented to meet minimum shortlist" for row in rows[1:])
    assert all("authors" not in row for row in rows[1:])
    assert all(len(str(row.get("abstract", ""))) <= 803 for row in rows if "abstract" in row)
    assert all("biology" not in row["title"].lower() for row in rows)


def test_knowledge_extract_uses_web_context_and_sanitizes_card_filenames(tmp_path: Path) -> None:
    config = default_workbench_config("graph calibration benchmark")
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-06"
    stage_dir.mkdir(parents=True)
    _write_text(
        run_dir / "stage-05" / "shortlist.jsonl",
        json.dumps({"title": "Graph Calibration", "url": "https://example.test/gc"}),
    )
    _write_text(run_dir / "stage-04" / "web_context.md", "WEB CONTEXT " + "z" * 100)
    llm = RecordingLLM(
        json.dumps(
            {
                "cards": [
                    {
                        "card_id": "Card: One/Two",
                        "title": "Graph Calibration Card",
                        "problem": "Problem",
                        "method": "Method",
                        "data": "Data",
                        "metrics": "Metrics",
                        "findings": "Findings",
                        "limitations": "Limitations",
                        "citation": "Citation",
                        "cite_key": "gc2024",
                    }
                ]
            }
        )
    )
    prompts = SimplePrompts()

    result = _literature._execute_knowledge_extract(
        stage_dir,
        run_dir,
        config,
        AdapterBundle(),
        llm=llm,
        prompts=prompts,
    )

    assert result.status is StageStatus.DONE
    assert "Web Search Context" in prompts.calls[0]["kwargs"]["shortlist"]
    card_files = list((stage_dir / "cards").glob("*.md"))
    assert len(card_files) == 1
    assert ":" not in card_files[0].name and "/" not in card_files[0].name
    content = card_files[0].read_text(encoding="utf-8")
    assert "# Graph Calibration Card" in content
    assert "## Cite_Key" in content
    assert "gc2024" in content

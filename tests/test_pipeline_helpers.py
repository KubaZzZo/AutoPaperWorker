# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchclaw.pipeline import _helpers
from researchclaw.pipeline.code_blocks import (
    extract_code_block,
    extract_multi_file_blocks,
)


def test_extract_code_block_prefers_fenced_python() -> None:
    content = """
Intro text.

```python
print("metric: 1.0")
```

Outro text.
"""

    assert _helpers._extract_code_block(content) == 'print("metric: 1.0")'


def test_extract_multi_file_blocks_rejects_path_traversal_and_keeps_main() -> None:
    content = """
```filename:../evil.py
print("bad")
```

```python
# FILE: src/model.py
class Model:
    pass
```
"""

    files = _helpers._extract_multi_file_blocks(content)

    assert files == {"main.py": "class Model:\n    pass"}


def test_code_blocks_module_matches_legacy_helper_exports() -> None:
    content = """
```filename:../evil.py
print("bad")
```

```python
# FILE: src/model.py
class Model:
    pass
```
"""

    assert extract_code_block(content) == _helpers._extract_code_block(content)
    assert extract_multi_file_blocks(content) == _helpers._extract_multi_file_blocks(
        content
    )


def test_parse_metrics_from_stdout_filters_status_lines() -> None:
    stdout = """
epoch: 1
primary_metric: 0.92
condition=baseline/accuracy metric=0.81
INFO: training complete
loss: not-a-number
"""

    metrics = _helpers._parse_metrics_from_stdout(stdout)

    assert metrics["primary_metric"] == pytest.approx(0.92)
    assert metrics["baseline/accuracy"] == pytest.approx(0.81)
    assert "INFO" not in metrics
    assert "loss" not in metrics


def test_parse_metrics_from_stdout_logs_non_numeric_metric_values(caplog, monkeypatch) -> None:
    class FakeConditionPattern:
        def match(self, line: str):
            if line.startswith("condition="):
                class FakeMatch:
                    def group(self, idx: int):
                        return "baseline/accuracy" if idx == 1 else "not-a-number"

                return FakeMatch()
            return None

    monkeypatch.setattr(_helpers, "_CONDITION_RE", FakeConditionPattern())
    stdout = """
condition=baseline/accuracy metric=not-a-number
loss: not-a-number
"""

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        metrics = _helpers._parse_metrics_from_stdout(stdout)

    assert metrics == {}
    assert "Skipping non-numeric condition metric from stdout" in caplog.text
    assert "Skipping non-numeric metric from stdout" in caplog.text


def test_safe_json_loads_logs_failed_parse_candidates(caplog) -> None:
    text = """
not json
```json
{bad}
```
trailing [bad]
"""

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        result = _helpers._safe_json_loads(text, default={"fallback": True})

    assert result == {"fallback": True}
    assert "Failed to parse JSON directly from LLM text" in caplog.text
    assert "Failed to parse fenced JSON candidate" in caplog.text
    assert "Failed to parse bracketed JSON candidate" in caplog.text


def test_get_evolution_overlay_logs_store_failures(tmp_path: Path, caplog, monkeypatch) -> None:
    import researchclaw.evolution as evolution

    class BrokenEvolutionStore:
        def __init__(self, _path: Path) -> None:
            pass

        def build_overlay(self, *_args, **_kwargs) -> str:
            raise RuntimeError("overlay failed")

    monkeypatch.setattr(evolution, "EvolutionStore", BrokenEvolutionStore)

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        overlay = _helpers._get_evolution_overlay(tmp_path, "stage-test")

    assert overlay == ""
    assert "Failed to build evolution lesson overlay" in caplog.text


def test_get_evolution_overlay_logs_skill_registry_failures(caplog, monkeypatch) -> None:
    def broken_registry(_config=None):
        raise RuntimeError("registry failed")

    monkeypatch.setattr(_helpers, "_get_skill_registry", broken_registry)

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        overlay = _helpers._get_evolution_overlay(None, "stage-test", topic="topic")

    assert overlay == ""
    assert "Failed to build matched skill overlay" in caplog.text


def test_build_context_preamble_logs_unreadable_hitl_guidance(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    rc_config = SimpleNamespace(
        research=SimpleNamespace(topic="test-driven science", domains=[]),
    )
    run_dir = tmp_path
    guidance = run_dir / "stage-99" / "hitl_guidance.md"
    guidance.parent.mkdir(parents=True)
    guidance.write_text("human guidance", encoding="utf-8")

    original_read_text = Path.read_text

    def fail_guidance_read(self: Path, *args, **kwargs):
        if self == guidance:
            raise OSError("guidance unavailable")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_guidance_read)

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        text = _helpers._build_context_preamble(rc_config, run_dir)

    assert "## Research Context" in text
    assert "Failed to read HITL guidance" in caplog.text


def test_collect_experiment_results_summarizes_and_selects_best(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "stage-14" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "a.json").write_text(
        json.dumps({"metrics": {"accuracy": 0.8}, "stdout": "accuracy: 0.8"}),
        encoding="utf-8",
    )
    (runs_dir / "b.json").write_text(
        json.dumps({"metrics": {"accuracy": 0.9}, "stdout": "accuracy: 0.9"}),
        encoding="utf-8",
    )

    result = _helpers._collect_experiment_results(
        tmp_path,
        metric_key="accuracy",
        metric_direction="maximize",
    )

    assert result["metrics_summary"]["accuracy"]["mean"] == pytest.approx(0.85)
    assert result["metrics_summary"]["accuracy"]["count"] == 2
    assert result["best_run"]["metrics"]["accuracy"] == pytest.approx(0.9)
    assert "accuracy & 0.8000 & 0.9000 & 0.8500 & 2" in result["latex_table"]


def test_collect_experiment_results_logs_malformed_structured_results(
    tmp_path: Path, caplog
) -> None:
    runs_dir = tmp_path / "stage-14" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "results.json").write_text("{bad json", encoding="utf-8")

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        result = _helpers._collect_experiment_results(tmp_path)

    assert result["runs"] == []
    assert "Failed to load structured experiment results" in caplog.text


def test_collect_experiment_results_logs_non_numeric_metric_values(
    tmp_path: Path, caplog
) -> None:
    runs_dir = tmp_path / "stage-14" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "a.json").write_text(
        json.dumps({"metrics": {"accuracy": "n/a", "loss": "bad"}}),
        encoding="utf-8",
    )

    with caplog.at_level("DEBUG", logger="researchclaw.pipeline._helpers"):
        result = _helpers._collect_experiment_results(
            tmp_path,
            metric_key="accuracy",
        )

    assert result["metrics_summary"] == {}
    assert result["best_run"]["metrics"]["accuracy"] == "n/a"
    assert "Skipping non-numeric experiment metric" in caplog.text
    assert "Skipping non-numeric primary experiment metric" in caplog.text


def test_extract_paper_title_strips_outer_fence_and_title_prefix() -> None:
    markdown = """```markdown
## Title GraphSignal: Sparse Spectral Filters for Robust Forecasting

## Abstract
This paper studies forecasting.
```"""

    title = _helpers._extract_paper_title(markdown)

    assert title == "GraphSignal: Sparse Spectral Filters for Robust Forecasting"

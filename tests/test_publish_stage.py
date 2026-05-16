"""Focused tests for publish-stage helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.literature.verify import CitationResult, VerificationReport, VerifyStatus
from researchclaw.pipeline.stage_impls import _publish
from researchclaw.pipeline.stages import StageStatus
from researchclaw.workbench.run import default_workbench_config


def _bib_entry(key: str, title: str | None = None) -> str:
    return (
        f"@article{{{key},\n"
        f"  title = {{{title or key}}},\n"
        "  author = {Smith, Ada},\n"
        "  year = {2024},\n"
        "}\n"
    )


def _verified_report(keys: list[str]) -> VerificationReport:
    return VerificationReport(
        total=len(keys),
        verified=len(keys),
        results=[
            CitationResult(
                cite_key=key,
                title=f"Title {key}",
                status=VerifyStatus.VERIFIED,
                confidence=0.99,
                method="test",
            )
            for key in keys
        ],
    )


def test_citation_verify_empty_bib_writes_placeholder_and_verified_paper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-23"
    stage_dir.mkdir(parents=True)

    monkeypatch.setattr(
        _publish,
        "_read_prior_artifact",
        lambda _run_dir, name: "Final paper body" if name == "paper_final.md" else "",
    )

    result = _publish._execute_citation_verify(
        stage_dir,
        run_dir,
        default_workbench_config("empty bibliography"),
        AdapterBundle(),
    )

    assert result.status is StageStatus.DONE
    assert "paper_final_verified.md" not in result.artifacts
    report = json.loads((stage_dir / "verification_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["total"] == 0
    assert report["summary"]["integrity_score"] == 1.0
    assert (stage_dir / "references_verified.bib").read_text(encoding="utf-8").startswith("% No references")
    assert (stage_dir / "paper_final_verified.md").read_text(encoding="utf-8") == "Final paper body"


def test_citation_verify_filters_low_relevance_and_uncited_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-23"
    stage22 = run_dir / "stage-22"
    stage_dir.mkdir(parents=True)
    stage22.mkdir()
    bib_text = "\n".join(
        [
            _bib_entry("good2024", "Relevant cited paper"),
            _bib_entry("low2024", "Off topic paper"),
            _bib_entry("tex2024", "Cited only in LaTeX"),
            _bib_entry("uncited2024", "Unused but verified"),
        ]
    )
    paper_text = r"We cite \cite{good2024,low2024} in markdown."
    (stage22 / "paper.tex").write_text(r"Additional citation \citep{tex2024}.", encoding="utf-8")

    monkeypatch.setattr(
        _publish,
        "_read_prior_artifact",
        lambda _run_dir, name: {"references.bib": bib_text, "paper_final.md": paper_text}.get(name, ""),
    )
    monkeypatch.setattr(
        "researchclaw.literature.verify.verify_citations",
        lambda _bib_text, s2_api_key="": _verified_report(["good2024", "low2024", "tex2024", "uncited2024"]),
    )

    class FakeLLM:
        def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
            return type(
                "Response",
                (),
                {"content": json.dumps({"good2024": 0.95, "low2024": 0.1, "tex2024": 0.8, "uncited2024": 0.9})},
            )()

    result = _publish._execute_citation_verify(
        stage_dir,
        run_dir,
        default_workbench_config("citation filtering"),
        AdapterBundle(),
        llm=FakeLLM(),
    )

    assert result.status is StageStatus.DONE
    verified_bib = (stage_dir / "references_verified.bib").read_text(encoding="utf-8")
    assert "good2024" in verified_bib
    assert "tex2024" in verified_bib
    assert "low2024" not in verified_bib
    assert "uncited2024" not in verified_bib
    verified_paper = (stage_dir / "paper_final_verified.md").read_text(encoding="utf-8")
    assert r"\cite{good2024}" in verified_paper
    assert "low2024" not in verified_paper
    report = json.loads((stage_dir / "verification_report.json").read_text(encoding="utf-8"))
    scores = {row["cite_key"]: row["relevance_score"] for row in report["results"]}
    assert scores["low2024"] == 0.1


def test_citation_verify_applies_hard_cap_with_default_relevance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-23"
    stage_dir.mkdir(parents=True)
    keys = [f"paper{i:02d}2024" for i in range(62)]
    bib_text = "\n".join(_bib_entry(key) for key in keys)

    monkeypatch.setattr(
        _publish,
        "_read_prior_artifact",
        lambda _run_dir, name: bib_text if name == "references.bib" else "",
    )
    monkeypatch.setattr(
        "researchclaw.literature.verify.verify_citations",
        lambda _bib_text, s2_api_key="": _verified_report(keys),
    )

    result = _publish._execute_citation_verify(
        stage_dir,
        run_dir,
        default_workbench_config("large bibliography"),
        AdapterBundle(),
    )

    assert result.status is StageStatus.DONE
    verified_bib = (stage_dir / "references_verified.bib").read_text(encoding="utf-8")
    kept_keys = re.findall(r"@\w+\{([^,]+),", verified_bib)
    assert len(kept_keys) == 60
    assert keys[0] not in kept_keys
    assert keys[1] not in kept_keys
    assert keys[-1] in kept_keys


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_sanitize_fabricated_data_returns_reason_without_verified_values(tmp_path: Path) -> None:
    paper = """# Results

| Method | Accuracy |
|---|---:|
| Proposed | 91.2 |
"""

    sanitized, report = _publish._sanitize_fabricated_data(paper, tmp_path / "run")

    assert sanitized == paper
    assert report["sanitized"] is False
    assert report["reason"] == "no verified values found in experiment_summary.json"
    assert report["tables_processed"] == 0


def test_sanitize_fabricated_data_uses_best_summary_and_sanitizes_markdown_tables(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_summary(
        run_dir / "experiment_summary_best.json",
        {
            "condition_summaries": {
                "proposed": {"metrics": {"accuracy_mean": 0.912, "loss_mean": 0.123}},
                "baseline": {"metrics": {"accuracy_mean": 0.75}},
            },
            "metrics_summary": {"accuracy": {"mean": 0.912}},
        },
    )
    _write_summary(
        run_dir / "stage-14-regressed" / "experiment_summary.json",
        {"condition_summaries": {"bad": {"metrics": {"accuracy_mean": 0.444}}}},
    )
    paper = """# Results

| Method | LR | Accuracy | Loss | Note |
|---|---:|---:|---:|---|
| Cos-200 | 0.0003 | 91.2 | 0.123 | 999 |
| Regressed | 0.0003 | 44.4 | 0.222 | ResNet-34 |

# Hyperparameters

| Parameter | Value |
|---|---:|
| Batch size | 4096 |

# Statistics

| Test | p-value | t-statistic |
|---|---:|---:|
| Paired | 0.031 | 5.4 |
"""

    sanitized, report = _publish._sanitize_fabricated_data(paper, run_dir)

    assert report["sanitized"] is True
    assert report["tables_processed"] == 1
    assert report["numbers_replaced"] == 3
    assert "| Cos-200 | 0.0003 | 91.2 | 0.123 | --- |" in sanitized
    assert "| Regressed | 0.0003 | --- | --- | ResNet-34 |" in sanitized
    assert "| Batch size | 4096 |" in sanitized
    assert "| Paired | 0.031 | 5.4 |" in sanitized
    assert "44.4" in report["replaced_samples"]


def test_sanitize_fabricated_data_sanitizes_latex_tables_and_results_prose(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_summary(
        run_dir / "stage-14" / "experiment_summary.json",
        {
            "condition_summaries": {
                "proposed": {"metrics": {"accuracy_mean": 0.912}},
            },
            "best_run": {"metrics": {"accuracy": 0.912}},
        },
    )
    paper = r"""# Results

The model achieved 91.2% accuracy and reported 88.8% on the hidden set.

\begin{table}
\caption{Result accuracy table}
\begin{tabular}{lrr}
\toprule
Method & Accuracy & Fake \\
\midrule
Proposed & 91.2 & 88.8 \\
Baseline & 75.0 & 20 \\
\bottomrule
\end{tabular}
\end{table}

# Discussion

The narrative reported 88.8% again outside results.
"""

    sanitized, report = _publish._sanitize_fabricated_data(paper, run_dir)

    assert report["sanitized"] is True
    assert report["tables_processed"] == 1
    assert report["numbers_replaced"] == 1
    assert report["prose_numbers_replaced"] == 1
    assert "achieved 91.2% accuracy" in sanitized
    assert "reported [value removed] on the hidden set" in sanitized
    assert "Proposed & 91.2 & 88.8" in sanitized
    assert "Baseline & --- & 20" in sanitized
    assert "outside results" in sanitized

class _FakeTemplate:
    name = "plain_test"
    display_name = "Plain Test Template"

    def get_style_files(self) -> list[Path]:
        return []


def _patch_export_publish_heavy_deps(monkeypatch) -> None:
    monkeypatch.setattr("researchclaw.templates.get_template", lambda _name: _FakeTemplate())
    monkeypatch.setattr(
        "researchclaw.templates.markdown_to_latex",
        lambda text, _tpl, title="", authors="", bib_file="", bib_entries=None: text,
    )
    monkeypatch.setattr(
        "researchclaw.experiment.visualize.generate_all_charts",
        lambda _run_dir, _chart_dir, metric_key="accuracy", metric_direction="maximize": [],
    )
    monkeypatch.setattr(
        "researchclaw.templates.compiler.compile_latex",
        lambda _tex_path, max_attempts=2: type("CompileResult", (), {"success": False, "errors": ["skipped"]})(),
    )
    monkeypatch.setattr(_publish, "reconcile_figure_refs", lambda _tex, _charts: None)
    monkeypatch.setattr(_publish, "_generate_framework_diagram_prompt", lambda *args, **kwargs: "")


def test_export_publish_converts_citations_prunes_bib_and_packages_single_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-22"
    stage_dir.mkdir(parents=True)
    (run_dir / "degradation_signal.json").write_text(
        json.dumps({"score": 0.41, "threshold": 0.8}),
        encoding="utf-8",
    )
    bib_text = "\n".join(
        [
            _bib_entry("smith2024main", "Main cited paper"),
            _bib_entry("jones2024extra", "Extra cited paper"),
            _bib_entry("unused2024paper", "Unused paper"),
        ]
    )
    paper = """# Useful Paper

## Abstract
This is concise.

## Results
We cite [smith2024main, jones2024extra] and mention [missing2024ghost].
"""
    code = """import numpy as np
from sklearn.metrics import accuracy_score

print(np.array([1]))
"""

    _patch_export_publish_heavy_deps(monkeypatch)
    monkeypatch.setattr(
        _publish,
        "_read_prior_artifact",
        lambda _run_dir, name: {
            "paper_revised.md": paper,
            "references.bib": bib_text,
            "experiment_final.py": code,
        }.get(name, ""),
    )
    monkeypatch.setattr(
        _publish,
        "_resolve_missing_citations",
        lambda missing, existing: (set(), []),
    )

    result = _publish._execute_export_publish(
        stage_dir,
        run_dir,
        default_workbench_config("export publish citations"),
        AdapterBundle(),
    )

    assert result.status is StageStatus.DONE
    assert "paper_final_latex.md" in result.artifacts
    assert "references.bib" in result.artifacts
    assert "invalid_citations.json" in result.artifacts
    final_md = (stage_dir / "paper_final.md").read_text(encoding="utf-8")
    assert "degraded mode" in final_md
    assert "missing2024ghost" not in final_md
    final_latex = (stage_dir / "paper_final_latex.md").read_text(encoding="utf-8")
    assert "\\cite{smith2024main, jones2024extra}" in final_latex
    exported_bib = (stage_dir / "references.bib").read_text(encoding="utf-8")
    assert "smith2024main" in exported_bib
    assert "jones2024extra" in exported_bib
    assert "unused2024paper" not in exported_bib
    requirements = (stage_dir / "code" / "requirements.txt").read_text(encoding="utf-8")
    assert requirements.splitlines() == ["numpy", "scikit-learn"]
    assert "Useful Paper" in (stage_dir / "code" / "README.md").read_text(encoding="utf-8")


def test_export_publish_uses_revised_when_llm_truncates_and_packages_multifile_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stage-22"
    code_src = run_dir / "experiment_final"
    chart_src = run_dir / "stage-14" / "charts"
    stage_dir.mkdir(parents=True)
    code_src.mkdir(parents=True)
    chart_src.mkdir(parents=True)
    (code_src / "train.py").write_text("import torch\nprint(torch.__version__)\n", encoding="utf-8")
    (code_src / "plot.py").write_text("import pandas as pd\n", encoding="utf-8")
    (chart_src / "fig_results.png").write_bytes(b"fake png")
    revised = """# Durable Paper

## Method
The full method text is intentionally long enough to reject a short LLM response.

## Results
See the result chart below.

## Conclusion
Done.
"""
    bib_text = _bib_entry("smith2024main")

    class ShortLLM:
        def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
            return type("Response", (), {"content": "tiny"})()

    _patch_export_publish_heavy_deps(monkeypatch)
    monkeypatch.setattr(
        _publish,
        "_read_prior_artifact",
        lambda _run_dir, name: {
            "paper_revised.md": revised,
            "references.bib": bib_text,
            "experiment_final/": str(code_src),
        }.get(name, ""),
    )

    result = _publish._execute_export_publish(
        stage_dir,
        run_dir,
        default_workbench_config("export publish multifile"),
        AdapterBundle(),
        llm=ShortLLM(),
    )

    assert result.status is StageStatus.DONE
    final_md = (stage_dir / "paper_final.md").read_text(encoding="utf-8")
    assert "tiny" not in final_md
    assert "Durable Paper" in final_md
    assert "![Figure 1: Fig Results](charts/fig_results.png)" in final_md
    assert (stage_dir / "charts" / "fig_results.png").exists()
    assert (stage_dir / "code" / "train.py").read_text(encoding="utf-8").startswith("import torch")
    assert (stage_dir / "code" / "plot.py").exists()
    requirements = (stage_dir / "code" / "requirements.txt").read_text(encoding="utf-8")
    assert requirements.splitlines() == ["pandas", "torch"]
    readme = (stage_dir / "code" / "README.md").read_text(encoding="utf-8")
    assert "train.py" in readme
    assert "plot.py" in readme

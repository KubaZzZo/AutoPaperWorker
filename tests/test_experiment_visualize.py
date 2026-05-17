"""Tests for experiment visualization chart generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchclaw.experiment import visualize

pytestmark = pytest.mark.skipif(
    not visualize.HAS_MATPLOTLIB,
    reason="matplotlib is required for visualization tests",
)


def _assert_png(path: Path) -> None:
    assert path.exists()
    assert path.stat().st_size > 0
    assert path.read_bytes().startswith(b"\x89PNG")


def _condition_summaries() -> dict[str, dict[str, object]]:
    return {
        "baseline": {
            "metrics": {
                "val_loss_mean": 0.42,
                "accuracy_mean": 0.73,
                "runtime_sec": 12.5,
            },
            "ci95_low": 0.38,
            "ci95_high": 0.46,
        },
        "improved_method": {
            "metrics": {
                "val_loss_mean": 0.31,
                "accuracy_mean": 0.82,
                "runtime_sec": 14.0,
            },
            "ci95_low": 0.28,
            "ci95_high": 0.34,
        },
        "unstable_variant": {
            "metrics": {
                "val_loss_mean": 0.55,
                "accuracy_mean": 0.61,
                "runtime_sec": 13.0,
            },
            "ci95_low": 0.49,
            "ci95_high": 0.62,
        },
    }


def test_label_helpers_format_for_readable_charts() -> None:
    assert visualize._format_cond_name("improved_method") == "Improved Method"
    assert visualize._shorten_label("short") == "short"
    shortened = visualize._shorten_label("x" * 30, max_len=8)
    assert shortened.startswith("x" * 7)
    assert len(shortened) == 8
    assert shortened[-1] != "x"
    assert visualize._is_excluded_metric("runtime_sec")
    assert visualize._is_excluded_metric("elapsed_custom")
    assert not visualize._is_excluded_metric("accuracy_mean")


def test_plot_functions_return_none_for_missing_or_insufficient_data(tmp_path: Path) -> None:
    assert visualize.plot_condition_comparison({}, tmp_path / "empty.png") is None
    assert visualize.plot_metric_heatmap(
        {"only": {"metrics": {"accuracy_mean": 0.9}}},
        tmp_path / "heatmap.png",
    ) is None
    assert visualize.plot_ablation_deltas(
        {"baseline": {"metrics": {"val_loss_mean": 0}}},
        tmp_path / "ablation.png",
    ) is None
    assert visualize.plot_metric_trajectory(
        [{"metrics": {"other": 1.0}}],
        "val_loss",
        tmp_path / "trajectory.png",
    ) is None
    assert visualize.plot_iteration_scores([None, None], tmp_path / "scores.png") is None


@pytest.mark.parametrize(
    ("plotter", "filename"),
    [
        (
            lambda data, path: visualize.plot_condition_comparison(
                data,
                path,
                metric_key="val_loss",
            ),
            "condition.png",
        ),
        (
            lambda data, path: visualize.plot_metric_heatmap(data, path),
            "heatmap.png",
        ),
        (
            lambda data, path: visualize.plot_ablation_deltas(
                data,
                path,
                metric_key="val_loss",
                higher_is_better=False,
            ),
            "ablation.png",
        ),
    ],
)
def test_condition_based_plotters_create_png_files(
    tmp_path: Path,
    plotter,
    filename: str,
) -> None:
    output = tmp_path / filename

    result = plotter(_condition_summaries(), output)

    assert result == output
    _assert_png(output)


def test_metric_trajectory_experiment_comparison_and_timeline_create_pngs(
    tmp_path: Path,
) -> None:
    trajectory = visualize.plot_metric_trajectory(
        [
            {"run_id": "seed-1", "metrics": {"val_loss": "0.52"}},
            {"run_id": "seed-2", "key_metrics": {"val_loss": 0.41}},
            {"run_id": "bad", "metrics": {"val_loss": "not-a-number"}},
        ],
        "val_loss",
        tmp_path / "trajectory.png",
    )
    comparison = visualize.plot_experiment_comparison(
        {
            "val_loss": {"mean": 0.42, "min": 0.31, "max": 0.55},
            "accuracy": {"mean": 0.75, "min": 0.61, "max": 0.82},
            "runtime_sec": {"mean": 12.0, "min": 10.0, "max": 14.0},
        },
        tmp_path / "comparison.png",
    )
    timeline = visualize.plot_pipeline_timeline(
        [
            {"stage_name": "Design", "elapsed_sec": 3.5, "status": "done"},
            {"stage": "Execute", "elapsed_sec": 0, "status": "failed"},
        ],
        tmp_path / "timeline.png",
    )
    scores = visualize.plot_iteration_scores(
        [6.2, None, 7.8],
        tmp_path / "scores.png",
        threshold=7.0,
    )

    for path in [trajectory, comparison, timeline, scores]:
        assert path is not None
        _assert_png(path)


def test_generate_all_charts_uses_run_data_versioned_stage14_and_iteration_summary(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "stage-13" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "run-1.json").write_text(
        json.dumps({"run_id": "run-1", "metrics": {"val_loss": 0.52}}),
        encoding="utf-8",
    )
    (runs_dir / "run-2.json").write_text(
        json.dumps({"run_id": "run-2", "metrics": {"val_loss": 0.31}}),
        encoding="utf-8",
    )
    (runs_dir / "broken.json").write_text("{bad json", encoding="utf-8")

    summary_dir = tmp_path / "stage-14-retry"
    summary_dir.mkdir()
    (summary_dir / "experiment_summary.json").write_text(
        json.dumps(
            {
                "condition_summaries": _condition_summaries(),
                "metrics_summary": {
                    "val_loss": {"mean": 0.42, "min": 0.31, "max": 0.55},
                    "accuracy": {"mean": 0.75, "min": 0.61, "max": 0.82},
                    "runtime_sec": {"mean": 13.0, "min": 12.0, "max": 14.0},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "iteration_summary.json").write_text(
        json.dumps({"iteration_scores": [5.5, None, 7.25], "quality_threshold": 7.0}),
        encoding="utf-8",
    )

    generated = visualize.generate_all_charts(
        tmp_path,
        metric_key="val_loss",
        metric_direction="minimize",
    )

    generated_names = {path.name for path in generated}
    assert generated_names == {
        "metric_trajectory.png",
        "method_comparison.png",
        "ablation_analysis.png",
        "metric_heatmap.png",
        "experiment_comparison.png",
        "iteration_scores.png",
    }
    for path in generated:
        _assert_png(path)

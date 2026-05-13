"""Experiment specification helpers used by pipeline integration hooks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricDef:
    """A metric that experiment results should report."""

    name: str
    direction: str = "maximize"


@dataclass
class ExperimentSpec:
    """Minimal structured experiment contract."""

    topic: str = ""
    metrics: list[MetricDef] = field(default_factory=list)
    baselines: list[str] = field(default_factory=list)


def generate_spec(topic: str, design_text: str = "") -> str:
    """Generate a human-readable experiment spec skeleton."""

    return (
        "# Experiment Spec\n\n"
        f"- topic: {topic}\n"
        "- metric: primary_metric (maximize)\n"
        "- baseline: baseline_method\n\n"
        "## Design Notes\n"
        f"{design_text.strip()}\n"
    )


def parse_spec(text: str) -> ExperimentSpec:
    """Parse metric and baseline declarations from a markdown spec."""

    spec = ExperimentSpec()
    topic_match = re.search(r"^-\s*topic:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if topic_match:
        spec.topic = topic_match.group(1).strip()

    for match in re.finditer(
        r"^-\s*metric:\s*([A-Za-z0-9_.\-/ ]+)(?:\s*\((maximize|minimize)\))?",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        name = match.group(1).strip()
        direction = (match.group(2) or "maximize").lower()
        if name:
            spec.metrics.append(MetricDef(name=name, direction=direction))

    for match in re.finditer(
        r"^-\s*baseline:\s*(.+)$",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        baseline = match.group(1).strip()
        if baseline:
            spec.baselines.append(baseline)

    return spec


def validate_results_against_spec(
    spec: ExperimentSpec, results: dict[str, Any]
) -> list[str]:
    """Return human-readable violations for missing metrics/baselines."""

    violations: list[str] = []
    reported_metrics = _collect_metric_names(results)
    for metric in spec.metrics:
        if metric.name not in reported_metrics:
            violations.append(f"Missing metric: {metric.name}")

    expected_baselines = {item.lower() for item in spec.baselines}
    reported_baselines = {
        str(item).lower() for item in results.get("baselines", [])
    }
    condition_summaries = results.get("condition_summaries", {})
    if isinstance(condition_summaries, dict):
        reported_baselines.update(str(key).lower() for key in condition_summaries)

    for baseline in sorted(expected_baselines):
        if baseline and baseline not in reported_baselines:
            violations.append(f"Missing baseline: {baseline}")

    return violations


def _collect_metric_names(results: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    metrics = results.get("metrics", {})
    if isinstance(metrics, dict):
        names.update(str(key) for key in metrics)
    summaries = results.get("condition_summaries", {})
    if isinstance(summaries, dict):
        for summary in summaries.values():
            if isinstance(summary, dict) and isinstance(summary.get("metrics"), dict):
                names.update(str(key) for key in summary["metrics"])
    return names

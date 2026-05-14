"""Schema normalization helpers for FigureAgent LLM outputs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from researchclaw.utils.sanitize import sanitize_figure_id

_DEFAULT_CHART_TYPE = "bar_comparison"
_DEFAULT_WIDTH = "single_column"


def _coerce_text(value: Any, default: str = "") -> str:
    """Return a stable string for scalar LLM fields."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def normalize_figure_spec(raw: Any, *, index: int = 0) -> dict[str, Any] | None:
    """Normalize one figure spec from an LLM response.

    Returns ``None`` for non-object items so callers can drop malformed list
    entries before later agents assume dictionary semantics.
    """
    if not isinstance(raw, dict):
        return None

    spec = dict(raw)
    spec["figure_id"] = sanitize_figure_id(
        _coerce_text(spec.get("figure_id"), f"fig_{index + 1}"),
        fallback=f"fig_{index + 1}",
    )
    spec["chart_type"] = _coerce_text(spec.get("chart_type"), _DEFAULT_CHART_TYPE)
    spec["title"] = _coerce_text(spec.get("title"))
    spec["caption"] = _coerce_text(spec.get("caption"))
    spec["x_label"] = _coerce_text(spec.get("x_label"))
    spec["y_label"] = _coerce_text(spec.get("y_label"))
    spec["width"] = _coerce_text(spec.get("width"), _DEFAULT_WIDTH)
    spec["section"] = _coerce_text(spec.get("section"), "results")

    data_source = spec.get("data_source", {})
    if isinstance(data_source, str):
        spec["data_source"] = {"type": data_source}
    elif isinstance(data_source, dict):
        spec["data_source"] = data_source
    else:
        spec["data_source"] = {}

    priority = spec.get("priority", 2)
    try:
        spec["priority"] = int(priority)
    except (TypeError, ValueError):
        spec["priority"] = 2

    return spec


def normalize_figure_specs(raw_items: Any) -> list[dict[str, Any]]:
    """Normalize a list of figure specs, dropping malformed entries."""
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (str, bytes, dict)):
        return []
    specs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        spec = normalize_figure_spec(raw, index=index)
        if spec is not None:
            specs.append(spec)
    return specs

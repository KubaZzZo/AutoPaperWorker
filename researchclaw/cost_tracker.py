"""Lightweight process-wide LLM cost tracker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

DEFAULT_PRICE_TABLE_PATH = Path(__file__).with_name("data") / "provider_prices.json"

PRICE_TABLE_USD_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    # input, output prices per 1M tokens
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4.1": (2.00, 8.00),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "anthropic/claude-3-5-sonnet": (3.00, 15.00),
    "anthropic/claude-3-7-sonnet": (3.00, 15.00),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "anthropic/claude-opus-4": (15.00, 75.00),
}


def load_price_table(path: str | Path = DEFAULT_PRICE_TABLE_PATH) -> dict[str, tuple[float, float]]:
    """Load provider/model token prices from a JSON data file.

    The JSON file is intentionally data-only so provider price maintenance can
    happen without changing cost aggregation code.
    """

    price_path = Path(path)
    payload = json.loads(price_path.read_text(encoding="utf-8"))
    models = payload.get("models", []) if isinstance(payload, dict) else []
    table: dict[str, tuple[float, float]] = {}
    if not isinstance(models, list):
        return table
    for item in models:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "")
        model = str(item.get("model") or "")
        if not provider or not model:
            continue
        try:
            input_price = float(item["input_usd_per_1m_tokens"])
            output_price = float(item["output_usd_per_1m_tokens"])
        except (KeyError, TypeError, ValueError):
            continue
        table[_price_key(provider, model)] = (input_price, output_price)
    return table


def get_price_table(path: str | Path = DEFAULT_PRICE_TABLE_PATH) -> dict[str, tuple[float, float]]:
    """Return configured prices, falling back to built-in defaults."""

    try:
        loaded = load_price_table(path)
    except (OSError, json.JSONDecodeError):
        loaded = {}
    return loaded or PRICE_TABLE_USD_PER_MILLION_TOKENS


@dataclass(frozen=True)
class CostEntry:
    """One recorded API cost event."""

    timestamp: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] | None = None


class CostTracker:
    """Track accumulated LLM cost and optionally persist JSONL entries."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path is not None else None
        self._entries: list[CostEntry] = []
        self._lock = Lock()
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return sum(entry.cost_usd for entry in self._entries)

    def record(
        self,
        provider: str,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        **metadata: Any,
    ) -> CostEntry:
        """Record an API usage cost event."""

        entry = CostEntry(
            timestamp=datetime.now(UTC).isoformat(),
            provider=provider,
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cost_usd=float(cost_usd or 0.0),
            metadata=metadata or None,
        )
        with self._lock:
            self._entries.append(entry)
            if self.log_path is not None:
                payload = asdict(entry)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return entry

    def check_budget(self, max_budget_usd: float) -> bool:
        """Return True if current spend is within ``max_budget_usd``."""

        if max_budget_usd <= 0:
            return True
        return self.total_cost_usd <= float(max_budget_usd)


def _empty_cost_bucket() -> dict[str, int | float]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "estimated_cost_usd": 0.0,
        "cost_variance_usd": 0.0,
        "cost_variance_ratio": 0.0,
    }


def _add_cost_to_bucket(
    bucket: dict[str, int | float],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    estimated_cost_usd: float,
) -> None:
    bucket["calls"] = int(bucket["calls"]) + 1
    bucket["prompt_tokens"] = int(bucket["prompt_tokens"]) + prompt_tokens
    bucket["completion_tokens"] = (
        int(bucket["completion_tokens"]) + completion_tokens
    )
    bucket["total_tokens"] = int(bucket["total_tokens"]) + prompt_tokens + completion_tokens
    bucket["cost_usd"] = round(float(bucket["cost_usd"]) + cost_usd, 6)
    bucket["estimated_cost_usd"] = round(
        float(bucket["estimated_cost_usd"]) + estimated_cost_usd,
        6,
    )
    _update_variance(bucket)


def estimate_entry_cost_usd(
    provider: str,
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    price_table_path: str | Path = DEFAULT_PRICE_TABLE_PATH,
) -> float:
    """Estimate cost from the provider/model price table."""

    key = _price_key(provider, model)
    prices = get_price_table(price_table_path).get(key)
    if prices is None:
        return 0.0
    input_price, output_price = prices
    return round(
        (prompt_tokens / 1_000_000) * input_price
        + (completion_tokens / 1_000_000) * output_price,
        6,
    )


def _price_key(provider: str, model: str) -> str:
    provider_norm = (provider or "unknown").strip().lower()
    model_norm = (model or "unknown").strip().lower()
    return f"{provider_norm}/{model_norm}"


def _update_variance(bucket: dict[str, int | float]) -> None:
    actual_key = "cost_usd" if "cost_usd" in bucket else "total_cost_usd"
    actual = float(bucket[actual_key])
    estimated = float(bucket["estimated_cost_usd"])
    variance = round(actual - estimated, 6)
    bucket["cost_variance_usd"] = variance
    if estimated:
        bucket["cost_variance_ratio"] = round(variance / estimated, 6)
    else:
        bucket["cost_variance_ratio"] = 0.0


def summarize_cost_log(log_path: str | Path) -> dict[str, Any]:
    """Aggregate a ``cost_log.jsonl`` by stage and model."""
    path = Path(log_path)
    summary: dict[str, Any] = {
        "calls": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "estimated_cost_usd": 0.0,
        "cost_variance_usd": 0.0,
        "cost_variance_ratio": 0.0,
        "by_stage": {},
        "by_model": {},
    }
    if not path.exists():
        return summary

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        prompt_tokens = int(entry.get("prompt_tokens") or 0)
        completion_tokens = int(entry.get("completion_tokens") or 0)
        cost_usd = float(entry.get("cost_usd") or 0.0)
        provider = str(entry.get("provider") or "unknown")
        model = str(entry.get("model") or "unknown")
        estimated_cost_usd = estimate_entry_cost_usd(
            provider,
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        metadata = entry.get("metadata")
        stage = "unknown"
        if isinstance(metadata, dict):
            stage = str(metadata.get("stage") or metadata.get("stage_name") or stage)

        summary["calls"] += 1
        summary["total_prompt_tokens"] += prompt_tokens
        summary["total_completion_tokens"] += completion_tokens
        summary["total_tokens"] += prompt_tokens + completion_tokens
        summary["total_cost_usd"] = round(
            float(summary["total_cost_usd"]) + cost_usd,
            6,
        )
        summary["estimated_cost_usd"] = round(
            float(summary["estimated_cost_usd"]) + estimated_cost_usd,
            6,
        )
        _update_variance(summary)

        by_stage = summary["by_stage"]
        stage_bucket = by_stage.setdefault(stage, _empty_cost_bucket())
        _add_cost_to_bucket(
            stage_bucket,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            estimated_cost_usd=estimated_cost_usd,
        )

        by_model = summary["by_model"]
        model_key = _price_key(provider, model)
        model_bucket = by_model.setdefault(model_key, _empty_cost_bucket())
        _add_cost_to_bucket(
            model_bucket,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            estimated_cost_usd=estimated_cost_usd,
        )

    return summary


def write_cost_summary(run_dir: str | Path) -> Path:
    """Write ``cost_summary.json`` beside ``cost_log.jsonl``."""
    root = Path(run_dir)
    summary = summarize_cost_log(root / "cost_log.jsonl")
    out_path = root / "cost_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out_path


_GLOBAL_TRACKER = CostTracker()


def get_global_tracker() -> CostTracker:
    """Return the process-wide tracker used by pipeline/HITL budget checks."""

    return _GLOBAL_TRACKER


def reset_global_tracker(log_path: str | Path | None = None) -> CostTracker:
    """Reset the process-wide tracker, primarily for tests."""

    global _GLOBAL_TRACKER
    _GLOBAL_TRACKER = CostTracker(log_path=log_path)
    return _GLOBAL_TRACKER

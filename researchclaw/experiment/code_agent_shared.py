"""Shared dataclasses and helpers for experiment code agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from researchclaw.config import RCConfig


@dataclass(frozen=True)
class CodeAgentResult:
    """Output from a code agent invocation."""

    files: dict[str, str]
    provider_name: str
    elapsed_sec: float
    raw_output: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.files)


class CodeAgentProvider(Protocol):
    """Protocol for code generation backends."""

    @property
    def name(self) -> str: ...

    def generate(
        self,
        *,
        exp_plan: str,
        topic: str,
        metric_key: str,
        pkg_hint: str,
        compute_budget: str,
        extra_guidance: str,
        workdir: Path,
        timeout_sec: int = 600,
    ) -> CodeAgentResult:
        ...

    def refine(
        self,
        *,
        current_files: dict[str, str],
        run_summaries: list[str],
        metric_key: str,
        metric_direction: str,
        topic: str,
        extra_hints: str,
        workdir: Path,
        timeout_sec: int = 600,
    ) -> CodeAgentResult:
        ...

    def repair(
        self,
        *,
        files: dict[str, str],
        issues: str,
        workdir: Path,
        timeout_sec: int = 300,
    ) -> CodeAgentResult:
        ...


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _collect_py_files(workdir: Path) -> dict[str, str]:
    """Read all .py files from a directory (flat, no subdirs)."""
    files: dict[str, str] = {}
    for pyfile in sorted(workdir.glob("*.py")):
        if pyfile.name.startswith("_codex_") or pyfile.name.startswith("_agent_"):
            continue
        files[pyfile.name] = pyfile.read_text(encoding="utf-8")
    return files


def _filter_claude_extra_args(args: list[str]) -> list[str]:
    """Drop Claude CLI flags that bypass permissions or re-enable shell access."""
    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--dangerously-skip-permissions":
            continue
        if arg == "--allowed-tools":
            skip_next = True
            continue
        filtered.append(arg)
    return filtered


def _seed_workdir(workdir: Path, files: dict[str, str]) -> None:
    """Pre-populate workdir with files for refinement/repair."""
    workdir.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (workdir / fname).write_text(content, encoding="utf-8")


def format_feedback_for_agent(
    sandbox_result: Any,
    metric_key: str,
    metric_direction: str,
    best_metric: float | None,
) -> str:
    """Format sandbox run results as structured feedback for CLI agents."""
    parts = ["## Previous Run Results"]
    parts.append(f"Return code: {sandbox_result.returncode}")
    parts.append(f"Elapsed: {sandbox_result.elapsed_sec:.1f}s")
    parts.append(f"Timed out: {sandbox_result.timed_out}")
    if sandbox_result.metrics:
        parts.append("Metrics:")
        for k, v in sandbox_result.metrics.items():
            parts.append(f"  {k}: {v}")
    if sandbox_result.stderr:
        parts.append(f"Stderr (last 1000 chars):\n{sandbox_result.stderr[-1000:]}")
    parts.append(f"\nTarget: {metric_direction} '{metric_key}'")
    if best_metric is not None:
        parts.append(f"Best so far: {best_metric}")
    return "\n".join(parts)


__all__ = [
    "CodeAgentProvider",
    "CodeAgentResult",
    "_collect_py_files",
    "_filter_claude_extra_args",
    "_seed_workdir",
    "_to_text",
    "format_feedback_for_agent",
]

"""Data models and protocols for the pipeline CodeAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CodeAgentConfig:
    """Configuration for the advanced code generation agent.

    All phases are independently toggleable.  The default profile enables
    Phases 1 (blueprint), 2 (sequential generation + exec-fix), and 5
    (review), which gives a large quality boost at moderate extra cost.
    Phase 4 (tree search) is opt-in because it multiplies both LLM and
    sandbox usage.
    """

    enabled: bool = True

    # Phase 1: Blueprint planning (deep implementation blueprint)
    architecture_planning: bool = True

    # Phase 2: Sequential file generation (generate files one-by-one
    # following dependency order from blueprint, with CodeMem summaries)
    sequential_generation: bool = True

    # Phase 2.5: Hard validation gates (AST-based)
    hard_validation: bool = True
    hard_validation_max_repairs: int = 4

    # Phase 3: Execution-in-the-loop
    exec_fix_max_iterations: int = 3
    exec_fix_timeout_sec: int = 60

    # Phase 4: Solution tree search (off by default)
    tree_search_enabled: bool = False
    tree_search_candidates: int = 3
    tree_search_max_depth: int = 2
    tree_search_eval_timeout_sec: int = 120

    # Phase 5: Multi-agent review dialog
    review_max_rounds: int = 2


@dataclass
class SolutionNode:
    """One candidate solution in the search tree."""

    node_id: str
    files: dict[str, str]
    parent_id: str | None = None
    depth: int = 0
    # Evaluation
    runs_ok: bool = False
    returncode: int = -1
    evaluated: bool = False
    stdout: str = ""
    stderr: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    generation_method: str = "initial"


@dataclass
class CodeAgentResult:
    """Final output from the code agent."""

    files: dict[str, str]
    architecture_spec: str = ""
    validation_log: list[str] = field(default_factory=list)
    total_llm_calls: int = 0
    total_sandbox_runs: int = 0
    best_score: float = 0.0
    tree_nodes_explored: int = 0
    review_rounds: int = 0


class _SandboxResult(Protocol):  # pragma: no cover
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float
    metrics: dict[str, object]
    timed_out: bool


class _SandboxLike(Protocol):  # pragma: no cover
    def run_project(
        self,
        project_dir: Path,
        *,
        entry_point: str = "main.py",
        timeout_sec: int = 300,
    ) -> Any: ...


@dataclass
class _SimpleResult:
    """Minimal sandbox result for internal error plumbing."""

    returncode: int = 1
    stdout: str = ""
    stderr: str = ""
    elapsed_sec: float = 0.0
    metrics: dict[str, object] = field(default_factory=dict)
    timed_out: bool = False

"""Stage I/O contracts for the 23-stage ResearchClaw pipeline.

Each StageContract declares:
  - input_files: artifacts this stage reads (produced by prior stages)
  - output_files: artifacts this stage must produce
  - dod: Definition of Done — human-readable acceptance criterion
  - error_code: unique error identifier for diagnostics
  - max_retries: how many times the stage may be retried on failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from researchclaw.pipeline.stages import Stage


@dataclass(frozen=True)
class StageContract:
    stage: Stage
    input_files: tuple[str, ...]
    output_files: tuple[str, ...]
    dod: str
    error_code: str
    max_retries: int = 1
    alternative_input_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ExperimentSummaryContract:
    """Structured payload written by Stage 14 as experiment_summary.json."""

    metrics_summary: dict[str, Any]
    total_runs: int
    best_run: dict[str, Any] | None
    latex_table: str
    generated: str
    seed_insufficiency_warnings: tuple[str, ...] = ()
    ablation_warnings: tuple[str, ...] = ()
    paired_comparisons: tuple[dict[str, Any], ...] = ()
    condition_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_conditions: int | None = None
    total_metric_keys: int | None = None

    def __post_init__(self) -> None:
        if self.total_runs < 0:
            raise ValueError("total_runs must be non-negative")
        if self.total_conditions is not None and self.total_conditions < 0:
            raise ValueError("total_conditions must be non-negative")
        if self.total_metric_keys is not None and self.total_metric_keys < 0:
            raise ValueError("total_metric_keys must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible Stage 14 summary payload."""
        payload: dict[str, Any] = {
            "metrics_summary": self.metrics_summary,
            "total_runs": self.total_runs,
            "best_run": self.best_run,
            "latex_table": self.latex_table,
            "generated": self.generated,
        }
        if self.seed_insufficiency_warnings:
            payload["seed_insufficiency_warnings"] = list(
                self.seed_insufficiency_warnings
            )
        if self.ablation_warnings:
            payload["ablation_warnings"] = list(self.ablation_warnings)
        if self.paired_comparisons:
            payload["paired_comparisons"] = list(self.paired_comparisons)
        if self.condition_summaries:
            payload["condition_summaries"] = self.condition_summaries
            payload["condition_metrics"] = self.condition_summaries
            payload["total_conditions"] = self.total_conditions
        if self.total_metric_keys:
            payload["total_metric_keys"] = self.total_metric_keys
        return payload


@dataclass(frozen=True)
class ExperimentRunContract:
    """Structured payload written by Stage 12 as runs/run-*.json."""

    run_id: str
    task_id: str
    status: str
    metrics: dict[str, Any]
    elapsed_sec: float | None
    stdout: str
    stderr: str
    stdout_log: str
    stderr_log: str
    timed_out: bool
    completed_at: str
    environment: dict[str, Any] = field(default_factory=dict)
    structured_results: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        if not self.status:
            raise ValueError("status must be non-empty")
        if self.elapsed_sec is not None and self.elapsed_sec < 0:
            raise ValueError("elapsed_sec must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible Stage 12 run payload."""
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "status": self.status,
            "metrics": self.metrics,
            "elapsed_sec": self.elapsed_sec,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_log": self.stdout_log,
            "stderr_log": self.stderr_log,
            "timed_out": self.timed_out,
            "completed_at": self.completed_at,
            "environment": self.environment,
        }
        if self.structured_results is not None:
            payload["structured_results"] = self.structured_results
        return payload


@dataclass(frozen=True)
class ResourceScheduleTaskContract:
    """One executable task in the Stage 11 resource schedule."""

    id: str
    name: str
    depends_on: tuple[str, ...] = ()
    gpu_count: int | float = 1
    estimated_minutes: int | float = 0
    priority: str = "normal"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("task id must be non-empty")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("task name must be non-empty")
        if self.gpu_count < 0:
            raise ValueError("gpu_count must be non-negative")
        if self.estimated_minutes < 0:
            raise ValueError("estimated_minutes must be non-negative")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ResourceScheduleTaskContract:
        depends_on = payload.get("depends_on", ())
        if depends_on is None:
            depends_on_values: tuple[str, ...] = ()
        elif isinstance(depends_on, list | tuple):
            depends_on_values = tuple(str(item) for item in depends_on)
        else:
            depends_on_values = (str(depends_on),)

        return cls(
            id=str(payload.get("id", "")).strip(),
            name=str(payload.get("name", "")).strip(),
            depends_on=depends_on_values,
            gpu_count=_coerce_number(payload.get("gpu_count", 1), "gpu_count"),
            estimated_minutes=_coerce_number(
                payload.get("estimated_minutes", 0),
                "estimated_minutes",
            ),
            priority=str(payload.get("priority", "normal")).strip() or "normal",
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible Stage 11 task payload."""
        return {
            "id": self.id,
            "name": self.name,
            "depends_on": list(self.depends_on),
            "gpu_count": self.gpu_count,
            "estimated_minutes": self.estimated_minutes,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ResourceScheduleContract:
    """Structured payload written by Stage 11 as schedule.json."""

    tasks: tuple[ResourceScheduleTaskContract, ...]
    total_gpu_budget: int | float
    generated: str

    def __post_init__(self) -> None:
        if self.total_gpu_budget < 0:
            raise ValueError("total_gpu_budget must be non-negative")
        if not self.generated:
            raise ValueError("generated must be non-empty")

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        generated: str,
    ) -> ResourceScheduleContract:
        raw_tasks = payload.get("tasks", ())
        if raw_tasks is None:
            raw_tasks = ()
        if not isinstance(raw_tasks, list | tuple):
            raise ValueError("tasks must be a list")

        tasks = []
        for raw_task in raw_tasks:
            if not isinstance(raw_task, dict):
                raise ValueError("tasks must contain mappings")
            tasks.append(ResourceScheduleTaskContract.from_payload(raw_task))

        default_budget = max((task.gpu_count for task in tasks), default=0)
        total_gpu_budget = _coerce_number(
            payload.get("total_gpu_budget", default_budget),
            "total_gpu_budget",
        )
        return cls(
            tasks=tuple(tasks),
            total_gpu_budget=total_gpu_budget,
            generated=str(payload.get("generated") or generated),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible Stage 11 schedule payload."""
        return {
            "tasks": [task.to_payload() for task in self.tasks],
            "total_gpu_budget": self.total_gpu_budget,
            "generated": self.generated,
        }


@dataclass(frozen=True)
class RefinementLogContract:
    """Structured payload written by Stage 13 as refinement_log.json."""

    generated: str
    mode: str
    metric_key: str
    iterations: tuple[dict[str, Any], ...] = ()
    metric_direction: str | None = None
    max_iterations_requested: int | None = None
    max_iterations_executed: int | None = None
    baseline_metric: int | float | None = None
    project_files: tuple[str, ...] = ()
    converged: bool | None = None
    stop_reason: str | None = None
    best_metric: int | float | None = None
    best_version: str | None = None
    final_version: str | None = None
    skipped: bool | None = None
    skip_reason: str | None = None
    paused: bool | None = None
    pause_reason: str | None = None
    pause_iteration: int | None = None
    iterations_completed: int | None = None
    ablation_identical_warning: bool | None = None

    def __post_init__(self) -> None:
        if not self.generated:
            raise ValueError("generated must be non-empty")
        if not self.mode:
            raise ValueError("mode must be non-empty")
        if not self.metric_key:
            raise ValueError("metric_key must be non-empty")
        _validate_optional_count(
            self.max_iterations_requested,
            "max_iterations_requested",
        )
        _validate_optional_count(
            self.max_iterations_executed,
            "max_iterations_executed",
        )
        _validate_optional_count(self.pause_iteration, "pause_iteration")
        _validate_optional_count(self.iterations_completed, "iterations_completed")

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        generated: str,
    ) -> RefinementLogContract:
        raw_iterations = payload.get("iterations", ())
        if raw_iterations is None:
            raw_iterations = ()
        if not isinstance(raw_iterations, list | tuple):
            raise ValueError("iterations must be a list")
        iterations = []
        for raw_iteration in raw_iterations:
            if not isinstance(raw_iteration, dict):
                raise ValueError("iterations must contain mappings")
            iterations.append(dict(raw_iteration))

        project_files = payload.get("project_files", ())
        if project_files is None:
            project_files_values: tuple[str, ...] = ()
        elif isinstance(project_files, list | tuple):
            project_files_values = tuple(str(path) for path in project_files)
        else:
            project_files_values = (str(project_files),)

        return cls(
            generated=str(payload.get("generated") or generated),
            mode=str(payload.get("mode", "")).strip(),
            metric_key=str(payload.get("metric_key", "")).strip(),
            metric_direction=_optional_string(payload.get("metric_direction")),
            max_iterations_requested=_optional_count(
                payload.get("max_iterations_requested"),
                "max_iterations_requested",
            ),
            max_iterations_executed=_optional_count(
                payload.get("max_iterations_executed"),
                "max_iterations_executed",
            ),
            baseline_metric=payload.get("baseline_metric"),
            project_files=project_files_values,
            iterations=tuple(iterations),
            converged=_optional_bool(payload.get("converged")),
            stop_reason=_optional_string(payload.get("stop_reason")),
            best_metric=payload.get("best_metric"),
            best_version=_optional_string(payload.get("best_version")),
            final_version=_optional_string(payload.get("final_version")),
            skipped=_optional_bool(payload.get("skipped")),
            skip_reason=_optional_string(payload.get("skip_reason")),
            paused=_optional_bool(payload.get("paused")),
            pause_reason=_optional_string(payload.get("pause_reason")),
            pause_iteration=_optional_count(
                payload.get("pause_iteration"),
                "pause_iteration",
            ),
            iterations_completed=_optional_count(
                payload.get("iterations_completed"),
                "iterations_completed",
            ),
            ablation_identical_warning=_optional_bool(
                payload.get("ablation_identical_warning")
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible Stage 13 refinement log payload."""
        payload: dict[str, Any] = {
            "generated": self.generated,
            "mode": self.mode,
            "metric_key": self.metric_key,
        }
        _put_optional(payload, "metric_direction", self.metric_direction)
        _put_optional(
            payload,
            "max_iterations_requested",
            self.max_iterations_requested,
        )
        _put_optional(payload, "max_iterations_executed", self.max_iterations_executed)
        _put_optional(payload, "baseline_metric", self.baseline_metric)
        if self.project_files:
            payload["project_files"] = list(self.project_files)
        payload["iterations"] = [dict(iteration) for iteration in self.iterations]
        _put_optional(payload, "converged", self.converged)
        _put_optional(payload, "stop_reason", self.stop_reason)
        _put_optional(payload, "best_metric", self.best_metric)
        _put_optional(payload, "best_version", self.best_version)
        _put_optional(payload, "final_version", self.final_version)
        _put_optional(payload, "skipped", self.skipped)
        _put_optional(payload, "skip_reason", self.skip_reason)
        _put_optional(payload, "paused", self.paused)
        _put_optional(payload, "pause_reason", self.pause_reason)
        _put_optional(payload, "pause_iteration", self.pause_iteration)
        _put_optional(payload, "iterations_completed", self.iterations_completed)
        _put_optional(
            payload,
            "ablation_identical_warning",
            self.ablation_identical_warning,
        )
        return payload


def _coerce_number(value: Any, field_name: str) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if number < 0:
        raise ValueError(f"{field_name} must be non-negative")
    if number.is_integer():
        return int(number)
    return number


def _optional_count(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    number = _coerce_number(value, field_name)
    return int(number)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _validate_optional_count(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _put_optional(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


CONTRACTS: dict[Stage, StageContract] = {
    # Phase A: Research Scoping
    Stage.TOPIC_INIT: StageContract(
        stage=Stage.TOPIC_INIT,
        input_files=(),
        output_files=("goal.md", "hardware_profile.json"),
        dod="SMART goal statement with topic, scope, and constraints",
        error_code="E01_INVALID_GOAL",
        max_retries=0,
    ),
    Stage.PROBLEM_DECOMPOSE: StageContract(
        stage=Stage.PROBLEM_DECOMPOSE,
        input_files=("goal.md",),
        output_files=("problem_tree.md",),
        dod=">=3 prioritized sub-questions identified",
        error_code="E02_DECOMP_FAIL",
    ),
    # Phase B: Literature Discovery
    Stage.SEARCH_STRATEGY: StageContract(
        stage=Stage.SEARCH_STRATEGY,
        input_files=("problem_tree.md",),
        output_files=("search_plan.yaml", "sources.json", "queries.json"),
        dod=">=2 search strategies defined with verified data sources",
        error_code="E03_STRATEGY_BAD",
    ),
    Stage.LITERATURE_COLLECT: StageContract(
        stage=Stage.LITERATURE_COLLECT,
        input_files=("search_plan.yaml",),
        output_files=("candidates.jsonl",),
        dod=">=N candidate papers collected from specified sources",
        error_code="E04_COLLECT_EMPTY",
        max_retries=2,
    ),
    Stage.LITERATURE_SCREEN: StageContract(
        stage=Stage.LITERATURE_SCREEN,
        input_files=("candidates.jsonl",),
        output_files=("shortlist.jsonl",),
        dod="Relevance + quality dual screening completed and approved",
        error_code="E05_GATE_REJECT",
        max_retries=0,
    ),
    Stage.KNOWLEDGE_EXTRACT: StageContract(
        stage=Stage.KNOWLEDGE_EXTRACT,
        input_files=("shortlist.jsonl",),
        output_files=("cards/",),
        dod="Structured knowledge card per shortlisted paper",
        error_code="E06_EXTRACT_FAIL",
    ),
    # Phase C: Knowledge Synthesis
    Stage.SYNTHESIS: StageContract(
        stage=Stage.SYNTHESIS,
        input_files=("cards/",),
        output_files=("synthesis.md",),
        dod="Topic clusters + >=2 research gaps identified",
        error_code="E07_SYNTHESIS_WEAK",
    ),
    Stage.HYPOTHESIS_GEN: StageContract(
        stage=Stage.HYPOTHESIS_GEN,
        input_files=("synthesis.md",),
        output_files=("hypotheses.md",),
        dod=">=2 falsifiable research hypotheses; optional branch plan when parallel_hypotheses is enabled",
        error_code="E08_HYP_INVALID",
    ),
    # Phase D: Experiment Design
    Stage.EXPERIMENT_DESIGN: StageContract(
        stage=Stage.EXPERIMENT_DESIGN,
        input_files=("hypotheses.md",),
        output_files=("exp_plan.yaml",),
        dod="Experiment plan with baselines, ablations, metrics approved",
        error_code="E09_GATE_REJECT",
        max_retries=0,
    ),
    Stage.CODE_GENERATION: StageContract(
        stage=Stage.CODE_GENERATION,
        input_files=("exp_plan.yaml",),
        output_files=("experiment/", "experiment_spec.md"),
        dod="Multi-file experiment project + spec document",
        error_code="E10_CODEGEN_FAIL",
        max_retries=2,
    ),
    Stage.RESOURCE_PLANNING: StageContract(
        stage=Stage.RESOURCE_PLANNING,
        input_files=("exp_plan.yaml",),
        output_files=("schedule.json",),
        dod="Resource schedule with GPU/time estimates",
        error_code="E11_SCHED_CONFLICT",
    ),
    # Phase E: Experiment Execution
    Stage.EXPERIMENT_RUN: StageContract(
        stage=Stage.EXPERIMENT_RUN,
        input_files=("schedule.json", "experiment/"),
        output_files=("runs/",),
        dod="All scheduled experiment runs completed with artifacts",
        error_code="E12_RUN_FAIL",
        max_retries=2,
    ),
    Stage.ITERATIVE_REFINE: StageContract(
        stage=Stage.ITERATIVE_REFINE,
        input_files=("runs/",),
        output_files=("refinement_log.json", "experiment_final/"),
        dod="Edit-run-eval loop converged or max iterations reached",
        error_code="E13_REFINE_FAIL",
        max_retries=2,
    ),
    # Phase F: Analysis & Decision
    Stage.RESULT_ANALYSIS: StageContract(
        stage=Stage.RESULT_ANALYSIS,
        input_files=("experiment_final/", "runs/"),
        output_files=("analysis.md",),
        dod="Metrics analyzed with statistical tests and conclusions",
        error_code="E14_ANALYSIS_ERR",
        alternative_input_groups=(("experiment_final/",), ("runs/",)),
    ),
    Stage.RESEARCH_DECISION: StageContract(
        stage=Stage.RESEARCH_DECISION,
        input_files=("analysis.md",),
        output_files=("decision.md",),
        dod="PROCEED/PIVOT decision with evidence-based justification",
        error_code="E15_DECISION_FAIL",
    ),
    # Phase G: Paper Writing
    Stage.PAPER_OUTLINE: StageContract(
        stage=Stage.PAPER_OUTLINE,
        input_files=("analysis.md", "decision.md"),
        output_files=("outline.md",),
        dod="Complete paper outline with section-level detail",
        error_code="E16_OUTLINE_FAIL",
    ),
    Stage.PAPER_DRAFT: StageContract(
        stage=Stage.PAPER_DRAFT,
        input_files=("outline.md",),
        output_files=("paper_draft.md",),
        dod="Full paper draft with all sections written",
        error_code="E17_DRAFT_FAIL",
    ),
    Stage.PEER_REVIEW: StageContract(
        stage=Stage.PEER_REVIEW,
        input_files=("paper_draft.md",),
        output_files=("reviews.md",),
        dod=">=2 simulated review perspectives with actionable feedback",
        error_code="E18_REVIEW_FAIL",
    ),
    Stage.PAPER_REVISION: StageContract(
        stage=Stage.PAPER_REVISION,
        input_files=("paper_draft.md", "reviews.md"),
        output_files=("paper_revised.md",),
        dod="All review comments addressed with tracked changes",
        error_code="E19_REVISION_FAIL",
    ),
    # Phase H: Finalization
    Stage.QUALITY_GATE: StageContract(
        stage=Stage.QUALITY_GATE,
        input_files=("paper_revised.md",),
        output_files=("quality_report.json",),
        dod="Quality score meets threshold and approved",
        error_code="E20_GATE_REJECT",
        max_retries=0,
    ),
    Stage.KNOWLEDGE_ARCHIVE: StageContract(
        stage=Stage.KNOWLEDGE_ARCHIVE,
        input_files=(),
        output_files=("archive.md", "bundle_index.json"),
        dod="Retrospective + reproducibility bundle archived",
        error_code="E21_ARCHIVE_FAIL",
    ),
    Stage.EXPORT_PUBLISH: StageContract(
        stage=Stage.EXPORT_PUBLISH,
        input_files=("paper_revised.md",),
        output_files=("paper_final.md", "code/"),
        dod="Final paper exported in target format",
        error_code="E22_EXPORT_FAIL",
    ),
    Stage.CITATION_VERIFY: StageContract(
        stage=Stage.CITATION_VERIFY,
        input_files=("paper_final.md",),  # references.bib is optional (BUG-50)
        output_files=("verification_report.json", "references_verified.bib"),
        dod="All citations verified against real APIs; hallucinated refs flagged",
        error_code="E23_VERIFY_FAIL",
    ),
}

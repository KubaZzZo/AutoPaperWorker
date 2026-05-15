# Stage Implementations Refactor Plan

> Created: 2026-05-15
> Status: Phase 3.15 implemented; Phase 3.16 planned
> Scope: three large-module reduction rounds for selected `researchclaw/pipeline/stage_impls/` modules.

## Goal

Reduce the review cost of the largest stage implementation modules without
changing pipeline behavior or breaking legacy imports. Each round must leave a
small, testable module behind a narrow interface and must be marked in this
document and in `docs/current_optimization_plan.md`.

## Ground Rules

- Use TDD for each round: add or extend a focused compatibility test first,
  verify the expected red failure, then move production code.
- Keep existing public and private stage imports compatible unless a stage
  caller is updated in the same round.
- Extract cohesive workflows, not isolated pass-through helpers.
- Do not mix stages in one commit. Each round gets its own commit and push.
- After every round, update the status table below and the Phase marker in
  `docs/current_optimization_plan.md`.
- Required verification for every round:
  - Targeted pytest for the moved workflow and its legacy wrapper/callers.
  - `python -m py_compile` for changed modules and tests.
  - `git diff --check`.
  - Post-push `git status --short --branch`, latest commit, and GitHub repo
    metadata check.

## Progress

| Phase | Target | Status | Marker |
| --- | --- | --- | --- |
| 3.15 | Execution repair workflow extraction | Implemented | 2026-05-15: `execution_run.py` owns Stage 12 sandbox run persistence, status classification, result artifact writing, time-budget warnings, and low-seed warnings. |
| 3.16 | Review/publish quality or citation workflow extraction | Planned | Not yet implemented |
| 3.17 | Paper writing or code-generation workflow extraction | Planned | Not yet implemented |

## Round 1: Phase 3.15 - Execution Repair Workflow

**Primary file:** `researchclaw/pipeline/stage_impls/_execution.py`

**Candidate extraction:** Move the cohesive experiment execution repair loop
support code into a new module, likely
`researchclaw/pipeline/stage_impls/execution_repair.py`.

**Why first:** `_execution.py` is large but has useful test coverage around
sandbox execution, metric parsing, fallback logging, no-improvement streaks,
empty metrics, and refinement behavior. This gives the first large-module
round a safer test surface than starting with the largest review/publish file.

**Expected shape:**

- New module owns a named repair-loop or repair-support workflow with a small
  interface.
- `_execution.py` keeps stage orchestration and imports the extracted workflow.
- Existing monkeypatch points used by tests remain available or are updated
  with focused compatibility tests.

**Minimum verification candidates:**

- `python -m pytest tests/test_rc_executor.py::TestNoImproveStreakFix -q`
- `python -m pytest tests/test_rc_executor.py::TestStdoutFailureDetection -q`
- `python -m pytest tests/test_rc_executor.py::TestConsecutiveEmptyMetrics -q`
- Add a focused new test for the extracted module matching the old caller
  behavior.

## Round 2: Phase 3.16 - Review/Publish Workflow

**Primary file:** `researchclaw/pipeline/stage_impls/_review_publish.py`

**Candidate extraction:** Move one cohesive review/publish workflow into a new
module. Preferred candidates are citation verification, draft-quality handling,
or final artifact packaging, depending on which has the clearest test coverage
at implementation time.

**Why second:** `_review_publish.py` is the largest stage module. It should be
split only after the first stage_impls extraction confirms the pattern for
tests, wrappers, and documentation markers.

**Expected shape:**

- New module owns one review/publish workflow and exposes a small function or
  object used by `_review_publish.py`.
- `_review_publish.py` remains the stage-facing orchestration module.
- Tests pin the behavior through the extracted module and through the legacy
  stage caller when practical.

**Minimum verification candidates:**

- Search first for tests touching citation verification, draft quality,
  publication packaging, or final artifacts.
- Run the exact targeted tests for the selected workflow.
- Add a focused new compatibility test before moving code.

## Round 3: Phase 3.17 - Writing Or Code Generation Workflow

**Primary files:** one of:

- `researchclaw/pipeline/stage_impls/_paper_writing.py`
- `researchclaw/pipeline/stage_impls/_code_generation.py`

**Candidate extraction:** Choose the better-covered workflow after Phases 3.15
and 3.16. Prefer paper drafting/revision support if writing tests are clear;
otherwise extract code-generation scaffold or validation support.

**Why third:** The third round should use what was learned from the first two
rounds rather than forcing a preselected target. Both files are large enough to
benefit, but the safer target is the one with the clearer test surface after
the first two refactors.

**Expected shape:**

- New module owns one coherent workflow.
- Existing stage module keeps orchestration and compatibility imports.
- Tests prove both the extracted workflow behavior and the stage caller path.

**Minimum verification candidates:**

- Search first for tests covering the selected workflow.
- Add a focused new compatibility test before moving code.
- Run targeted tests plus py_compile and diff checks.

## Completion Definition

The three-round stage_impls refactor is complete when:

- Phases 3.15, 3.16, and 3.17 are all marked implemented in this document.
- `docs/current_optimization_plan.md` contains matching implemented markers.
- Each phase has its own commit pushed to `origin/main`.
- The final local worktree is clean and tracks `origin/main`.

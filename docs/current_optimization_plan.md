# Current Optimization Plan

> Created: 2026-05-14
> Status: Active
> Supersedes: completed issue tracker, roadmap, Figure/BenchmarkAgent plan, and MetaClaw integration plan.

## Cleanup Decision

The completed tracker and implementation plans have been removed from `docs/`
because they no longer describe active work:

- Issue tracker v9 reported 37 fixed issues, 0 partial, and 0 open.
- The roadmap listed no tracked high-value gaps.
- The FigureAgent/BenchmarkAgent plan marked both agents implemented.
- The MetaClaw integration plan marked the integration implemented and merged.

The remaining docs are user-facing guides, localized READMEs, tester guides,
and this active optimization plan.

## Current Priorities

1. **Experiment throughput observability**
   - Stage 12 already writes live stdout/stderr log files.
   - `progress.json` should expose the latest experiment run log paths and
     run status so dashboards and WebSocket consumers can surface long-running
     experiment progress without scanning every artifact.

2. **Runtime noise and logging cleanup**
   - Library `print()` calls remain in pipeline and experiment modules.
   - Keep intentional CLI output, but move background pipeline status and
     repair messages toward structured logging or explicit reporter hooks.
   - 2026-05-14 slice: experiment repair progress no longer writes directly to
     stdout by default; callers can opt in with an injected progress reporter.
   - 2026-05-14 slice: pipeline runner stage progress no longer writes directly
     to stdout by default; CLI explicitly passes `print` as the progress
     reporter to preserve terminal feedback.
   - 2026-05-14 slice: Context7 MCP client usage docs no longer include a
     direct `print()` example, keeping client guidance side-effect free.
   - 2026-05-14 slice: generated Colab worker templates now support
     `RC_COLAB_WORKER_VERBOSE=0` for quiet polling logs while preserving
     visible status output by default.
   - 2026-05-14 slice: collaboration-loop stage I/O can now be injected for
     cleaner library usage, while interactive edit/fallback prompts remain
     terminal-visible on purpose.
   - 2026-05-14 slice: collaboration-loop artifact update notices now route
     through injected output hooks instead of direct `print()`.
   - 2026-05-14 slice: experiment harness metric/warning output can now be
     injected for quieter library tests while preserving default stdout/stderr
     sandbox behavior.

3. **Large-module reduction**
   - `researchclaw/pipeline/runner.py` and
     `researchclaw/templates/converter.py` remain large enough to slow review
     and targeted agent edits; `researchclaw/prompts.py` is now a small
     public manager module backed by `researchclaw/prompt_defaults/`.
   - Extract narrow helpers only where tests already define behavior.
   - 2026-05-14 slice: progress snapshot writing moved out of
     `researchclaw/pipeline/runner.py` into `researchclaw/pipeline/progress.py`
     so pipeline orchestration no longer owns dashboard snapshot details.
   - 2026-05-14 slice: checkpoint and heartbeat persistence moved into
     `researchclaw/pipeline/checkpoint.py`, leaving runner-level compatibility
     wrappers for existing callers.
   - 2026-05-14 slice: experiment diagnosis/repair orchestration moved into
     `researchclaw/pipeline/experiment_workflow.py`, further narrowing
     `researchclaw/pipeline/runner.py` to pipeline control flow.
   - 2026-05-14 slice: parallel hypothesis branch orchestration moved into
     `researchclaw/pipeline/parallel_branches.py`, and final deliverable
     packaging moved into `researchclaw/pipeline/deliverables.py`; runner now
     keeps only compatibility wrappers around those workflows.
   - 2026-05-14 slice: pipeline summary and content authenticity metrics moved
     into `researchclaw/pipeline/summary.py`, leaving runner focused on stage
     scheduling and rollback control.
   - 2026-05-15 slice: prompt defaults moved out of
     `researchclaw/prompts.py` into focused modules under
     `researchclaw/prompt_defaults/`, while `PromptManager` and legacy
     constants remain import-compatible.

4. **Long-run performance backends**
   - `progress.json` and artifact scanning are enough for local runs, but not
     ideal for high-throughput multi-run monitoring.
   - A later slice should introduce a pluggable run-state backend such as
     SQLite first, with Redis left optional.

5. **Coverage expansion**
   - Add tests for every optimization slice.
   - Prioritize dashboard/run-state behavior, LLM adapter resilience, MCP
     transport/registry paths, and experiment artifact handling.

## First Implementation Slice

Expose Stage 12 experiment run telemetry in `progress.json`.

Acceptance criteria:

- When `stage-12/runs/run-*.json` exists, the progress snapshot includes an
  `experiment_runs` array.
- Each run entry includes `run_id`, `status`, `elapsed_sec`, `stdout_log`,
  `stderr_log`, `metrics`, and `updated_at` when available.
- Paths are relative to the run directory so the snapshot can move with the
  artifact directory.
- Malformed run JSON does not break progress writing; it is skipped with debug
  logging.
- Dashboard collection preserves the experiment telemetry in its snapshot
  dictionary output for API/WebSocket consumers.

## Verification

Run targeted tests for pipeline integration and executor behavior, then run
syntax and whitespace checks:

- `python -m pytest tests/test_pipeline_integrations.py tests/test_rc_executor.py`
- `python -m py_compile researchclaw/pipeline/runner.py researchclaw/dashboard/collector.py`
- `git diff --check`

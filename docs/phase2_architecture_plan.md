# Phase 2 Architecture Deepening Plan

> Created: 2026-05-15
> Status: Active
> Scope: second-round architecture optimization after the Phase 1 throughput,
> logging, and runner extraction work.

## Goal

Make the highest-friction modules easier to review, test, and edit without
changing public behavior. Each phase must keep existing interfaces stable unless
the phase explicitly says otherwise.

## Priority Order

1. **Prompt defaults decomposition** - implemented 2026-05-15
   - Target: `researchclaw/prompts.py`
   - Why first: it is the largest file in the package and mixes the
     `PromptManager` interface with large default prompt data.
   - Strategy: keep `PromptManager`, `RenderedPrompt`, and `_render` in
     `researchclaw/prompts.py`; move default data into focused modules under
     `researchclaw/prompt_defaults/`.

2. **Template converter decomposition** - implemented 2026-05-15
   - Target: `researchclaw/templates/converter.py`
   - Why second: it remains large and format-sensitive, but it needs narrower
     behavior tests before structural movement.
   - Strategy: split conversion helpers by responsibility after adding tests
     around the public converter behavior.

3. **Run-state backend interface** - in progress
   - Target: progress and dashboard run-state paths.
   - Why third: this is feature-level architecture work, not a pure refactor.
   - Strategy: introduce a small backend interface with the existing JSON
     progress path as the first adapter, then add SQLite as a second adapter.

4. **Coverage expansion**
   - Target: LLM adapter resilience, MCP transport/registry paths, and
     experiment artifact handling.
   - Strategy: add focused tests at the public interface of each module before
     changing implementation.

## Phase 2.1: Prompt Defaults Decomposition

Status: Implemented 2026-05-15.

### Files

- Keep:
  - `researchclaw/prompts.py`
- Create:
  - `researchclaw/prompt_defaults/__init__.py`
  - `researchclaw/prompt_defaults/blocks.py`
  - `researchclaw/prompt_defaults/debate_roles.py`
  - `researchclaw/prompt_defaults/sub_prompts.py`
  - `researchclaw/prompt_defaults/stage_prompts.py`
- Test:
  - `tests/test_prompts_architecture.py`

### Design

`researchclaw/prompts.py` remains the public prompt interface. Existing imports
such as `from researchclaw.prompts import PromptManager`,
`DEBATE_ROLES_HYPOTHESIS`, and `DEBATE_ROLES_ANALYSIS` must keep working.

Default prompt dictionaries move behind the same names:

- `SECTION_WORD_TARGETS` and `_DEFAULT_BLOCKS` move to
  `researchclaw.prompt_defaults.blocks`.
- `DEBATE_ROLES_HYPOTHESIS` and `DEBATE_ROLES_ANALYSIS` move to
  `researchclaw.prompt_defaults.debate_roles`.
- `_DEFAULT_SUB_PROMPTS` moves to
  `researchclaw.prompt_defaults.sub_prompts`.
- `_DEFAULT_STAGES` moves to
  `researchclaw.prompt_defaults.stage_prompts`.

`researchclaw/prompts.py` imports and re-exports those names so callers do not
need to change.

### Acceptance Criteria

- [x] `PromptManager()` still loads all default stages.
- [x] User override YAML behavior is unchanged.
- [x] Sub-prompt rendering behavior is unchanged.
- [x] Debate role constants remain importable from `researchclaw.prompts`.
- [x] `researchclaw/prompts.py` is materially smaller after the split.
- [x] No prompt text is intentionally changed.

### Verification

Run these commands after implementation:

- `python -m pytest tests/test_prompts_architecture.py tests/test_code_agent.py tests/test_rc_evolution.py`
- `python -m py_compile researchclaw/prompts.py researchclaw/prompt_defaults/__init__.py researchclaw/prompt_defaults/blocks.py researchclaw/prompt_defaults/debate_roles.py researchclaw/prompt_defaults/sub_prompts.py researchclaw/prompt_defaults/stage_prompts.py`
- `git diff --check`

## Phase 2.2: Template Converter Decomposition

Status: Implemented 2026-05-15.

### Planned Direction

Add focused tests around the current converter public behavior, then move
format-specific helpers out of `researchclaw/templates/converter.py` into a
small package. This phase should not start until Phase 2.1 is committed.

### Completed Slices

- 2026-05-15: moved inline Markdown/LaTeX conversion helpers from
  `researchclaw/templates/converter.py` into `researchclaw/templates/inline.py`.
  Legacy imports from `researchclaw.templates.converter` are preserved by
  re-exporting `_convert_inline`, `_escape_latex`, and Unicode replacement data.
- 2026-05-15: moved table parsing/rendering helpers into
  `researchclaw/templates/tables.py`, with converter compatibility wrappers for
  `_render_table`, `_parse_table_row`, and `_parse_alignments`.
- 2026-05-15: moved code block and algorithm rendering helpers into
  `researchclaw/templates/codeblocks.py`, with converter compatibility wrappers
  for `_render_code_block` and `_escape_algo_line`.
- 2026-05-15: moved paper completeness checks into
  `researchclaw/templates/completeness.py`, while preserving
  `check_paper_completeness` from `researchclaw.templates.converter`.

### Acceptance Criteria

- [x] Existing `markdown_to_latex` behavior remains compatible.
- [x] Legacy converter helper imports used by tests remain available.
- [x] Table rendering, code block rendering, inline conversion, figure
  rendering, and completeness checks have targeted regression coverage.
- [x] `researchclaw/templates/converter.py` is materially smaller after the
  split.

## Phase 2.3: Run-State Backend Interface

Status: In progress. First JSON backend slice implemented 2026-05-15.

### Planned Direction

Create a run-state interface that preserves the current JSON progress behavior
as the default adapter. SQLite should be added only after the JSON adapter has
tests proving compatibility with dashboard collection.

### Completed Slices

- 2026-05-15: introduced `researchclaw/run_state.py` with
  `RunStateBackend` and `JsonRunStateBackend`.
- 2026-05-15: `write_progress_snapshot()` writes through the run-state backend
  while preserving the existing `progress.json` file contract.
- 2026-05-15: `DashboardCollector` reads progress through the run-state backend,
  so malformed progress snapshot logging is now owned by `researchclaw.run_state`.

### Remaining Slices

- Add an injectable SQLite backend that implements the same `RunStateBackend`
  interface.
- Add migration/compatibility tests proving JSON progress files remain readable
  while SQLite state can serve dashboard collection.

## Phase 2.4: Coverage Expansion

### Planned Direction

Add tests for the highest-risk interfaces that still have thin coverage:

- LLM adapter retry/error behavior.
- MCP transport and registry behavior.
- Experiment artifact discovery and malformed artifact handling.

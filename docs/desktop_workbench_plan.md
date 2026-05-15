# AutoPaperWorker Workbench Implementation Plan

## Summary

This document tracks the lightweight AutoPaperWorker workbench rollout. The
workbench is backend-first: CLI and GUI surfaces call focused backend modules
under `researchclaw/workbench`, while GUI code remains a thin shell.

## Phase Progress

- [x] P0: Core CLI skeleton
  - Added `researchclaw workbench search --topic "..."`
  - Added `researchclaw workbench run --topic "..."`
  - Added OpenAlex/arXiv preview search adapter.
  - Added workbench config builder for cloud API pipeline runs.
- [x] P1: GUI shell and import foundations
  - Added `researchclaw gui`.
  - Added a `customtkinter`-based thin GUI shell.
  - Added CNKI semi-automatic import helpers for RIS, simple metadata text, and PDF paths.
  - Added local OpenAI-compatible model config support.
- [x] P2: Computer-science graduation project foundations
  - Added topic classification for algorithm/system/tool project styles.
  - Added read-only code project analysis.
  - Added a minimal project planning helper.
- [x] P3: Remote SSH compute foundations
  - Added AutoDL/GPUHome/custom SSH command parsing.
  - Added key-first/password-fallback auth metadata.
  - Added redacted profile serialization so passwords do not persist.

## Final Polish

- [x] Wire a real multi-tab GUI shell to the workbench controller.
- [x] Add pipeline progress reporting into the GUI log pane and CLI.
- [x] Make remote download recursive so result directories can be pulled back.
- [x] Clean up Chinese user-facing strings and backend labels.
- [x] Add controller-level integration tests for search, CNKI, projects, and remote compute.

## Implementation Notes

- CNKI remains semi-automatic and compliant: the GUI should open CNKI and help
  import user-exported records/PDFs. It must not automate login, CAPTCHA,
  permission bypass, or bulk full-text download.
- SSH passwords are session-only data. Persisted profiles must be redacted.
- GUI tests are intentionally light. Backend modules carry behavior coverage.
- Remote execution is represented by profile and parsing helpers in this slice;
  actual upload/run/download orchestration can build on these helpers.

## Verification

- `pytest tests/test_workbench.py -q`

# Contributing to AutoResearchClaw

Thanks for helping improve AutoResearchClaw. This project combines a 23-stage research pipeline, LLM adapters, sandboxed experiment execution, paper generation, HITL workflows, and publication tooling, so small, well-scoped changes are much easier to review than broad rewrites.

## Development Setup

1. Fork and clone the repo.
2. Create a virtual environment and install dev dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
   On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.
3. Generate your local config:
   ```bash
   researchclaw init
   ```
4. Edit `config.arc.yaml` with your local LLM settings. Do not commit secrets.

## Config Convention

- `config.researchclaw.example.yaml` is the tracked template.
- `config.arc.yaml` is your local config and must stay gitignored.
- `config.yaml` is also gitignored and supported as a fallback.

## Quality Checks

Run the smallest relevant checks before opening a PR:

```bash
ruff check changed_file.py
mypy --config-file pyproject.toml changed_file.py
pytest tests/test_relevant_module.py -q
```

For broader changes, run the non-integration test layer:

```bash
pytest -m "not integration"
```

Live API and environment-specific checks are marked separately. Run them only when you have the right credentials and local tools:

```bash
pytest -m integration -rs
pytest -m live_api -rs
```

## CI Expectations

Pull requests run lint on changed Python/config files and run the non-integration test layer. The main branch also uploads coverage and lint reports. Security scanning runs through Bandit and uploads a report for review.

The repository still has historical lint debt, so new PRs should avoid adding new ruff violations even while the project ratchets toward full-repo lint enforcement.

## PR Guidelines

- Branch from `main` unless you are continuing an approved feature branch.
- Keep one concern per PR.
- Include focused tests for new behavior or bug fixes.
- Update docs when changing commands, config, workflow, or user-facing behavior.
- Add a note to `CHANGELOG.md` under `[Unreleased]` for user-visible changes.
- Do not commit generated artifacts, local configs, caches, logs, uploads, or credentials.

## Changelog

This project follows a Keep a Changelog-style `CHANGELOG.md`. Add concise entries under `Added`, `Changed`, `Fixed`, `Security`, or another existing heading. Link to issues or PRs when available.

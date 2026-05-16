# Test Infrastructure Audit

Last reviewed: 2026-05-16

## Coverage

Pytest is configured in `pyproject.toml` to run coverage by default:

```powershell
python -m pytest
```

This emits a terminal missing-lines report and writes an HTML report to `htmlcov/`.
Use this faster command when you only need pass/fail or skip auditing:

```powershell
python -m pytest --no-cov
```

## Skipped Test Review

Latest local audit command:

```powershell
python -m pytest -q -rs --no-cov
```

Latest local result:

```text
3158 passed, 7 skipped, 29 warnings in 188.68s
```

Skip categories:

| Category | Count | Current assessment |
| --- | ---: | --- |
| Missing HITL ablation intervention fixtures | 0 | Resolved by committing deterministic fixtures under `experiments/hitl_ablation/interventions/`. |
| Missing historical artifact fixtures | 0 | Resolved by moving artifact-regression tests to committed minimal fixtures under `tests/fixtures/artifacts/`. |
| Missing external API credentials | 4 | Expected in local/CI runs without `ANTHROPIC_API_KEY` or `MINIMAX_API_KEY`. Keep skipped unless a credentialed integration job is configured. |
| Platform/tooling unavailable | 3 | Expected on this Windows environment: symlink privilege test and two Bash sentinel tests. |
| Optional/generated profile or harness fixture missing | 0 | No longer present in the latest local audit. |

Remaining skips are external-service or platform/tooling dependent. They should stay skipped in ordinary local runs and be covered by credentialed or POSIX-capable jobs when needed.

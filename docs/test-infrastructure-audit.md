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
3113 passed, 52 skipped, 29 warnings in 194.38s
```

Skip categories:

| Category | Count | Current assessment |
| --- | ---: | --- |
| Missing HITL ablation intervention fixtures | 30 | Expected for a clean checkout unless `experiments/hitl_ablation/interventions/` fixture files are restored or generated. |
| Missing historical artifact fixtures | 12 | Expected for artifact-regression tests that depend on local run IDs under `artifacts/`. These should either get committed minimal fixtures or be converted to generated fixtures. |
| Missing external API credentials | 4 | Expected in local/CI runs without `ANTHROPIC_API_KEY` or `MINIMAX_API_KEY`. Keep skipped unless a credentialed integration job is configured. |
| Platform/tooling unavailable | 3 | Expected on this Windows environment: symlink privilege test and two Bash sentinel tests. |
| Optional/generated profile or harness fixture missing | 3 | Review whether these should use generated temporary fixtures instead of runtime skips. |

Priority follow-up: reduce artifact and HITL fixture skips first. They account for most skipped tests and can likely be made deterministic without external services.

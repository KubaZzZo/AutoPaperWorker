from __future__ import annotations

import json
from pathlib import Path

from researchclaw.config import ParallelHypothesesConfig
from researchclaw.pipeline.parallel_hypotheses import (
    build_hypothesis_branch_plan,
    extract_hypothesis_candidates,
    write_hypothesis_branch_plan,
)


def test_extract_hypothesis_candidates_from_numbered_markdown():
    text = """
# Candidate Hypotheses

1. Contrastive pretraining improves low-label accuracy on graph benchmarks.
2. A smaller adapter model matches the full fine-tuned baseline.
3. Curriculum sampling reduces validation loss variance.
"""

    candidates = extract_hypothesis_candidates(text)

    assert candidates == [
        "Contrastive pretraining improves low-label accuracy on graph benchmarks.",
        "A smaller adapter model matches the full fine-tuned baseline.",
        "Curriculum sampling reduces validation loss variance.",
    ]


def test_build_hypothesis_branch_plan_limits_and_stabilizes_ids():
    cfg = ParallelHypothesesConfig(
        enabled=True,
        max_branches=2,
        selection_metric="accuracy",
    )

    plan = build_hypothesis_branch_plan(
        """
## H1
Contrastive pretraining improves low-label accuracy.

## H2
Curriculum sampling improves convergence.

## H3
Adapter tuning improves parameter efficiency.
""",
        cfg,
    )

    assert plan["enabled"] is True
    assert plan["selection_metric"] == "accuracy"
    assert [branch["rank"] for branch in plan["branches"]] == [1, 2]
    assert [branch["branch_id"] for branch in plan["branches"]] == [
        "hypothesis-01",
        "hypothesis-02",
    ]
    assert plan["branches"][0]["status"] == "planned"
    assert "Contrastive pretraining" in plan["branches"][0]["hypothesis"]


def test_build_hypothesis_branch_plan_disabled_returns_no_branches():
    cfg = ParallelHypothesesConfig(enabled=False, max_branches=3)

    plan = build_hypothesis_branch_plan("1. Test hypothesis", cfg)

    assert plan["enabled"] is False
    assert plan["branches"] == []


def test_write_hypothesis_branch_plan_persists_json(tmp_path: Path):
    cfg = ParallelHypothesesConfig(enabled=True, max_branches=1)

    output = write_hypothesis_branch_plan(
        tmp_path,
        "1. Contrastive pretraining improves low-label accuracy.",
        cfg,
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert output.name == "hypothesis_branches.json"
    assert data["branches"][0]["branch_id"] == "hypothesis-01"

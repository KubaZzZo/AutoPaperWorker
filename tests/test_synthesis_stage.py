"""Focused tests for synthesis and hypothesis generation stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.hitl.workshops.idea import IdeaCandidate
from researchclaw.pipeline.stage_impls import _synthesis
from researchclaw.pipeline.stages import StageStatus
from researchclaw.workbench.run import default_workbench_config


def test_hypothesis_gen_persists_real_idea_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    saved_candidates: list[object] = []

    class CapturingIdeaWorkshop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.candidates: list[object] = []

        def save(self) -> None:
            saved_candidates.extend(self.candidates)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stage_dir = run_dir / "stage-08"
    stage_dir.mkdir()
    monkeypatch.setattr(_synthesis, "_read_prior_artifact", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        "researchclaw.hitl.workshops.idea.IdeaWorkshop",
        CapturingIdeaWorkshop,
    )
    monkeypatch.setattr(
        "researchclaw.literature.novelty.check_novelty",
        lambda **_kwargs: {
            "novelty_score": 1.0,
            "assessment": "high",
            "recommendation": "proceed",
        },
        raising=False,
    )

    result = _synthesis._execute_hypothesis_gen(
        stage_dir,
        run_dir,
        default_workbench_config("typed hypothesis candidate"),
        AdapterBundle(),
        llm=None,
    )

    assert result.status is StageStatus.DONE
    assert len(saved_candidates) == 1
    candidate = saved_candidates[0]
    assert isinstance(candidate, IdeaCandidate)
    assert candidate.title == "Generated Hypothesis"
    assert candidate.description

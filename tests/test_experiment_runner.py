"""Tests for the experiment runner loop and history serialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from researchclaw.config import ExperimentConfig
from researchclaw.experiment.runner import (
    ExperimentHistory,
    ExperimentResult,
    ExperimentRunner,
    _result_from_dict,
)
from researchclaw.experiment.sandbox import SandboxResult


class FakeSandbox:
    def __init__(self, results: list[SandboxResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def run(self, code: str, *, timeout_sec: int = 300, cancel_event=None) -> SandboxResult:
        self.calls.append((code, timeout_sec))
        return self.results.pop(0)


@dataclass
class FakeResponse:
    content: str


class FakeLlm:
    def __init__(self, responses: list[str] | None = None, *, fail: bool = False) -> None:
        self.responses = responses or []
        self.fail = fail
        self.messages: list[list[dict[str, str]]] = []
        self.systems: list[str | None] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
    ) -> FakeResponse:
        self.messages.append(messages)
        self.systems.append(system)
        if self.fail:
            raise RuntimeError("llm unavailable")
        return FakeResponse(self.responses.pop(0))


class FakeGit:
    def __init__(self) -> None:
        self.actions: list[tuple[str, object]] = []

    def is_git_repo(self) -> bool:
        return True

    def create_experiment_branch(self, tag: str) -> str:
        self.actions.append(("branch", tag))
        return f"exp/{tag}"

    def commit_experiment(
        self,
        run_id: str,
        metrics: dict[str, object],
        description: str,
    ) -> str:
        self.actions.append(("commit", run_id, metrics, description))
        return f"commit-{run_id}"

    def discard_experiment(self, run_id: str, reason: str) -> bool:
        self.actions.append(("discard", run_id, reason))
        return True

    def return_to_original_branch(self) -> bool:
        self.actions.append(("return", None))
        return True


def _sandbox_result(
    metric: object | None,
    *,
    returncode: int = 0,
    stderr: str = "",
    timed_out: bool = False,
) -> SandboxResult:
    metrics = {"score": metric} if metric is not None else {}
    return SandboxResult(
        returncode=returncode,
        stdout="ok",
        stderr=stderr,
        elapsed_sec=1.25,
        metrics=metrics,
        timed_out=timed_out,
    )


def _runner(
    tmp_path: Path,
    monkeypatch,
    sandbox: FakeSandbox,
    *,
    direction: str = "minimize",
    keep_threshold: float = 0.0,
    max_iterations: int = 3,
) -> ExperimentRunner:
    monkeypatch.setattr(
        "researchclaw.experiment.runner.create_sandbox",
        lambda config, path: sandbox,
    )
    monkeypatch.setattr(
        "researchclaw.experiment.environment.capture_environment",
        lambda sandbox: {"python": "test", "backend": type(sandbox).__name__},
    )
    config = ExperimentConfig(
        time_budget_sec=12,
        max_iterations=max_iterations,
        metric_key="score",
        metric_direction=direction,
        keep_threshold=keep_threshold,
    )
    return ExperimentRunner(config, tmp_path / "workspace")


def test_run_experiment_records_baseline_and_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sandbox = FakeSandbox([_sandbox_result("0.5")])
    runner = _runner(tmp_path, monkeypatch, sandbox)

    result = runner.run_experiment("print('hello')", run_id="r1")

    assert sandbox.calls == [("print('hello')", 12)]
    assert result.primary_metric == 0.5
    assert result.improved is True
    assert result.kept is True
    assert result.error is None
    assert result.environment == {"python": "test", "backend": "FakeSandbox"}
    assert runner.history.baseline_metric == 0.5
    assert runner.history.best_result == result


def test_run_experiment_tracks_errors_and_thresholded_improvements(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sandbox = FakeSandbox(
        [
            _sandbox_result(1.0),
            _sandbox_result(0.98),
            _sandbox_result(0.7, returncode=2, stderr="boom"),
            _sandbox_result(0.6, timed_out=True),
        ]
    )
    runner = _runner(tmp_path, monkeypatch, sandbox, keep_threshold=0.05)

    baseline = runner.run_experiment("base", run_id="r", iteration=0)
    small_delta = runner.run_experiment("small", run_id="r", iteration=1)
    failed = runner.run_experiment("failed", run_id="r", iteration=2)
    timed_out = runner.run_experiment("slow", run_id="r", iteration=3)

    assert baseline.kept is True
    assert small_delta.improved is True
    assert small_delta.kept is False
    assert failed.error == "boom"
    assert failed.kept is True
    assert timed_out.error == "Timed out after 12s"
    assert runner.history.best_result == timed_out
    assert [r.iteration for r in runner.history.results] == [0, 1, 2, 3]


def test_run_loop_uses_llm_code_blocks_and_git_keep_or_discard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sandbox = FakeSandbox(
        [
            _sandbox_result(1.0),
            _sandbox_result(0.8),
            _sandbox_result(0.9),
            _sandbox_result(0.7),
        ]
    )
    runner = _runner(tmp_path, monkeypatch, sandbox, max_iterations=3)
    git = FakeGit()
    runner._git = git
    llm = FakeLlm(
        [
            "```python\nprint('better')\n```",
            "print('worse')",
            "```python\nprint('best')\n```",
        ]
    )

    history = runner.run_loop("print('base')", run_id="exp1", llm=llm)

    assert [call[0] for call in sandbox.calls] == [
        "print('base')",
        "print('better')",
        "print('worse')",
        "print('best')",
    ]
    assert [result.primary_metric for result in history.results] == [1.0, 0.8, 0.9, 0.7]
    assert history.best_result is not None
    assert history.best_result.code == "print('best')"
    assert ("branch", "exp1") in git.actions
    assert any(action[0] == "commit" and action[1] == "exp1-iter1" for action in git.actions)
    assert any(action[0] == "discard" and action[1] == "exp1-iter2" for action in git.actions)
    assert git.actions[-1] == ("return", None)
    assert "Last primary metric" in llm.messages[0][0]["content"]


def test_improve_code_falls_back_when_llm_fails_or_returns_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = _runner(tmp_path, monkeypatch, FakeSandbox([]))
    runner.history.add(
        ExperimentResult(
            run_id="r",
            iteration=0,
            code="current",
            metrics={"score": 1.0},
            primary_metric=1.0,
            improved=True,
            kept=True,
            elapsed_sec=1.0,
            stdout="",
            stderr="",
        )
    )

    assert runner._improve_code(FakeLlm(fail=True), "current", runner.history) == "current"
    assert runner._improve_code(FakeLlm(["   "]), "current", runner.history) == "current"


def test_history_round_trip_skips_invalid_entries(tmp_path: Path) -> None:
    result = ExperimentResult(
        run_id="r1",
        iteration=2,
        code="print(1)",
        metrics={"score": 0.4, 2: "coerced"},
        primary_metric=0.4,
        improved=True,
        kept=True,
        elapsed_sec=3,
        stdout="out",
        stderr="err",
        environment={1: "numeric-key"},
    )
    history = ExperimentHistory(results=[result], best_result=result, baseline_metric=1.2)
    restored = ExperimentHistory.from_dict(
        {
            **history.to_dict(),
            "results": [
                history.to_dict()["results"][0],
                {"run_id": 123, "iteration": "bad"},
            ],
        }
    )

    assert len(restored.results) == 1
    assert restored.results[0].metrics == {"score": 0.4, "2": "coerced"}
    assert restored.results[0].environment == {"1": "numeric-key"}
    assert restored.best_result is not None
    assert restored.best_result.primary_metric == 0.4
    assert restored.baseline_metric == 1.2

    runner = ExperimentRunner.__new__(ExperimentRunner)
    runner.history = restored
    output = tmp_path / "nested" / "history.json"
    runner.save_history(output)
    assert output.exists()


def test_result_from_dict_rejects_wrong_shapes() -> None:
    valid = {
        "run_id": "r",
        "iteration": 0,
        "code": "code",
        "metrics": {"score": 1},
        "primary_metric": None,
        "improved": False,
        "kept": False,
        "elapsed_sec": 0.5,
        "stdout": "",
        "stderr": "",
        "error": None,
        "environment": {},
    }

    assert _result_from_dict(valid) is not None
    assert _result_from_dict({**valid, "metrics": []}) is None
    assert _result_from_dict({**valid, "primary_metric": "bad"}) is None
    assert _result_from_dict({**valid, "error": 404}) is None
    assert ExperimentRunner._to_float(True) is None
    assert ExperimentRunner._to_float("bad") is None
    assert ExperimentRunner._to_float(3) == 3.0
    maximize = ExperimentRunner.__new__(ExperimentRunner)
    maximize.config = ExperimentConfig(metric_direction="maximize")
    minimize = ExperimentRunner.__new__(ExperimentRunner)
    minimize.config = ExperimentConfig(metric_direction="minimize")
    assert maximize._is_improvement(2.0, 1.0)
    assert minimize._is_improvement(0.5, 1.0)

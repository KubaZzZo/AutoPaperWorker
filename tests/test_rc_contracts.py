import re

import pytest

from researchclaw.pipeline.contracts import (
    CONTRACTS,
    ExperimentRunContract,
    ExperimentSummaryContract,
    RefinementLogContract,
    ResourceScheduleContract,
    ResourceScheduleTaskContract,
    StageContract,
)
from researchclaw.pipeline.stages import GATE_STAGES, STAGE_SEQUENCE, Stage


def test_contracts_dict_has_exactly_23_entries():
    assert len(CONTRACTS) == 23


def test_every_stage_has_matching_contract_entry():
    assert set(CONTRACTS.keys()) == set(Stage)


@pytest.mark.parametrize("stage", STAGE_SEQUENCE)
def test_each_stage_member_resolves_to_stage_contract(stage: Stage):
    assert isinstance(CONTRACTS[stage], StageContract)


@pytest.mark.parametrize("stage,contract", tuple(CONTRACTS.items()))
def test_contract_stage_field_matches_dict_key(stage: Stage, contract: StageContract):
    assert contract.stage is stage


@pytest.mark.parametrize("contract", tuple(CONTRACTS.values()))
def test_output_files_is_non_empty_for_all_contracts(contract: StageContract):
    assert contract.output_files


@pytest.mark.parametrize("stage,contract", tuple(CONTRACTS.items()))
def test_error_code_starts_with_e_and_contains_stage_number(
    stage: Stage, contract: StageContract
):
    assert contract.error_code.startswith("E")
    assert f"{int(stage):02d}" in contract.error_code
    assert re.match(r"^E\d{2}_[A-Z0-9_]+$", contract.error_code)


@pytest.mark.parametrize("contract", tuple(CONTRACTS.values()))
def test_max_retries_is_non_negative_for_all_contracts(contract: StageContract):
    assert contract.max_retries >= 0


def test_gate_stages_have_expected_max_retries():
    assert CONTRACTS[Stage.LITERATURE_SCREEN].max_retries == 0
    assert CONTRACTS[Stage.EXPERIMENT_DESIGN].max_retries == 0
    assert CONTRACTS[Stage.QUALITY_GATE].max_retries == 0


@pytest.mark.parametrize("stage", tuple(GATE_STAGES))
def test_gate_stage_contracts_are_never_retried(stage: Stage):
    assert CONTRACTS[stage].max_retries == 0


def test_topic_init_contract_has_expected_input_output_files():
    contract = CONTRACTS[Stage.TOPIC_INIT]

    assert contract.input_files == ()
    assert contract.output_files == ("goal.md", "hardware_profile.json")


def test_export_publish_contract_has_expected_outputs():
    contract = CONTRACTS[Stage.EXPORT_PUBLISH]

    assert contract.output_files == ("paper_final.md", "code/")


@pytest.mark.parametrize("contract", tuple(CONTRACTS.values()))
def test_dod_is_non_empty_string_for_all_contracts(contract: StageContract):
    assert isinstance(contract.dod, str)
    assert contract.dod.strip()


@pytest.mark.parametrize("contract", tuple(CONTRACTS.values()))
def test_input_files_is_tuple_of_strings(contract: StageContract):
    assert isinstance(contract.input_files, tuple)
    assert all(isinstance(path, str) and path for path in contract.input_files)


@pytest.mark.parametrize("contract", tuple(CONTRACTS.values()))
def test_output_files_is_tuple_of_strings(contract: StageContract):
    assert isinstance(contract.output_files, tuple)
    assert all(isinstance(path, str) and path for path in contract.output_files)


def test_error_codes_are_unique_across_contracts():
    all_codes = [contract.error_code for contract in CONTRACTS.values()]
    assert len(all_codes) == len(set(all_codes))


def test_contracts_follow_stage_sequence_order():
    assert tuple(CONTRACTS.keys()) == STAGE_SEQUENCE


@pytest.mark.parametrize("stage", STAGE_SEQUENCE)
def test_contract_stage_int_matches_stage_enum_value(stage: Stage):
    assert int(CONTRACTS[stage].stage) == int(stage)


def test_experiment_summary_contract_serializes_stage_14_payload():
    summary = ExperimentSummaryContract(
        metrics_summary={"accuracy": {"mean": 0.91}},
        total_runs=3,
        best_run={"run_id": "best", "metrics": {"accuracy": 0.91}},
        latex_table="\\begin{tabular}{}",
        generated="2026-05-17T12:00:00+00:00",
        seed_insufficiency_warnings=("baseline has only 2 seeds",),
        ablation_warnings=("ABLATION FAILURE: conditions are identical",),
        paired_comparisons=({"method": "proposed", "baseline": "control"},),
        condition_summaries={
            "control": {"metrics": {"accuracy": 0.82}},
            "proposed": {"metrics": {"accuracy": 0.91}},
        },
        total_conditions=2,
        total_metric_keys=5,
    )

    payload = summary.to_payload()

    assert payload["metrics_summary"] == {"accuracy": {"mean": 0.91}}
    assert payload["total_runs"] == 3
    assert payload["best_run"]["run_id"] == "best"
    assert payload["seed_insufficiency_warnings"] == ["baseline has only 2 seeds"]
    assert payload["ablation_warnings"] == ["ABLATION FAILURE: conditions are identical"]
    assert payload["paired_comparisons"] == [
        {"method": "proposed", "baseline": "control"}
    ]
    assert payload["condition_summaries"] == summary.condition_summaries
    assert payload["condition_metrics"] == summary.condition_summaries
    assert payload["total_conditions"] == 2
    assert payload["total_metric_keys"] == 5


def test_experiment_summary_contract_omits_empty_optional_sections():
    summary = ExperimentSummaryContract(
        metrics_summary={},
        total_runs=0,
        best_run=None,
        latex_table="",
        generated="2026-05-17T12:00:00+00:00",
    )

    payload = summary.to_payload()

    assert "seed_insufficiency_warnings" not in payload
    assert "ablation_warnings" not in payload
    assert "paired_comparisons" not in payload
    assert "condition_summaries" not in payload
    assert "condition_metrics" not in payload
    assert "total_conditions" not in payload
    assert "total_metric_keys" not in payload


def test_experiment_summary_contract_rejects_negative_counts():
    with pytest.raises(ValueError, match="total_runs"):
        ExperimentSummaryContract(
            metrics_summary={},
            total_runs=-1,
            best_run=None,
            latex_table="",
            generated="2026-05-17T12:00:00+00:00",
        )


def test_experiment_run_contract_serializes_stage_12_sandbox_payload():
    run = ExperimentRunContract(
        run_id="run-1",
        task_id="sandbox-main",
        status="completed",
        metrics={"accuracy": 0.91},
        elapsed_sec=12.5,
        stdout="ok",
        stderr="",
        stdout_log="runs/run-1.stdout.log",
        stderr_log="runs/run-1.stderr.log",
        timed_out=False,
        completed_at="2026-05-17T12:00:00+00:00",
        environment={"python": "3.12"},
        structured_results={"conditions": {"baseline": {"n_seeds": 3}}},
    )

    payload = run.to_payload()

    assert payload == {
        "run_id": "run-1",
        "task_id": "sandbox-main",
        "status": "completed",
        "metrics": {"accuracy": 0.91},
        "elapsed_sec": 12.5,
        "stdout": "ok",
        "stderr": "",
        "stdout_log": "runs/run-1.stdout.log",
        "stderr_log": "runs/run-1.stderr.log",
        "timed_out": False,
        "completed_at": "2026-05-17T12:00:00+00:00",
        "environment": {"python": "3.12"},
        "structured_results": {"conditions": {"baseline": {"n_seeds": 3}}},
    }


def test_experiment_run_contract_omits_missing_structured_results():
    run = ExperimentRunContract(
        run_id="run-1",
        task_id="sandbox-main",
        status="failed",
        metrics={},
        elapsed_sec=1.0,
        stdout="FAIL",
        stderr="",
        stdout_log="runs/run-1.stdout.log",
        stderr_log="runs/run-1.stderr.log",
        timed_out=False,
        completed_at="2026-05-17T12:00:00+00:00",
        environment={},
    )

    assert "structured_results" not in run.to_payload()


def test_experiment_run_contract_rejects_negative_elapsed_seconds():
    with pytest.raises(ValueError, match="elapsed_sec"):
        ExperimentRunContract(
            run_id="run-1",
            task_id="sandbox-main",
            status="failed",
            metrics={},
            elapsed_sec=-0.1,
            stdout="",
            stderr="",
            stdout_log="runs/run-1.stdout.log",
            stderr_log="runs/run-1.stderr.log",
            timed_out=False,
            completed_at="2026-05-17T12:00:00+00:00",
            environment={},
        )


def test_resource_schedule_contract_serializes_stage_11_payload():
    schedule = ResourceScheduleContract(
        tasks=(
            ResourceScheduleTaskContract(
                id="baseline",
                name="Run baseline",
                depends_on=(),
                gpu_count=1,
                estimated_minutes=20,
                priority="high",
            ),
            ResourceScheduleTaskContract(
                id="proposed",
                name="Run proposed method",
                depends_on=("baseline",),
                gpu_count=2,
                estimated_minutes=30.5,
                priority="normal",
            ),
        ),
        total_gpu_budget=2,
        generated="2026-05-17T12:00:00+00:00",
    )

    assert schedule.to_payload() == {
        "tasks": [
            {
                "id": "baseline",
                "name": "Run baseline",
                "depends_on": [],
                "gpu_count": 1,
                "estimated_minutes": 20,
                "priority": "high",
            },
            {
                "id": "proposed",
                "name": "Run proposed method",
                "depends_on": ["baseline"],
                "gpu_count": 2,
                "estimated_minutes": 30.5,
                "priority": "normal",
            },
        ],
        "total_gpu_budget": 2,
        "generated": "2026-05-17T12:00:00+00:00",
    }


def test_resource_schedule_contract_normalizes_llm_payload():
    schedule = ResourceScheduleContract.from_payload(
        {
            "tasks": [
                {
                    "id": 7,
                    "name": "Run generated task",
                    "depends_on": ["baseline", 8],
                    "gpu_count": "2",
                    "estimated_minutes": "15.5",
                }
            ],
            "total_gpu_budget": "4",
        },
        generated="2026-05-17T12:00:00+00:00",
    )

    assert schedule.to_payload() == {
        "tasks": [
            {
                "id": "7",
                "name": "Run generated task",
                "depends_on": ["baseline", "8"],
                "gpu_count": 2,
                "estimated_minutes": 15.5,
                "priority": "normal",
            }
        ],
        "total_gpu_budget": 4,
        "generated": "2026-05-17T12:00:00+00:00",
    }


@pytest.mark.parametrize(
    "task_kwargs",
    (
        {"id": "", "name": "Run baseline", "gpu_count": 1, "estimated_minutes": 20},
        {"id": "baseline", "name": "", "gpu_count": 1, "estimated_minutes": 20},
        {"id": "baseline", "name": "Run baseline", "gpu_count": -1},
        {"id": "baseline", "name": "Run baseline", "estimated_minutes": -1},
    ),
)
def test_resource_schedule_task_contract_rejects_invalid_fields(task_kwargs):
    with pytest.raises(ValueError):
        ResourceScheduleTaskContract(depends_on=(), priority="high", **task_kwargs)


def test_resource_schedule_contract_rejects_non_mapping_tasks():
    with pytest.raises(ValueError, match="tasks"):
        ResourceScheduleContract.from_payload(
            {"tasks": ["baseline"]},
            generated="2026-05-17T12:00:00+00:00",
        )


def test_refinement_log_contract_serializes_stage_13_payload():
    log = RefinementLogContract(
        generated="2026-05-17T12:00:00+00:00",
        mode="sandbox",
        metric_key="accuracy",
        metric_direction="maximize",
        max_iterations_requested=5,
        max_iterations_executed=3,
        baseline_metric=0.72,
        project_files=("main.py", "requirements.txt"),
        iterations=(
            {
                "iteration": 1,
                "version_dir": "experiment_v1/",
                "metric": 0.81,
                "improved": True,
            },
        ),
        converged=True,
        stop_reason="no_improvement_for_2_iterations",
        best_metric=0.81,
        best_version="experiment_v1/",
        final_version="experiment_final/",
    )

    assert log.to_payload() == {
        "generated": "2026-05-17T12:00:00+00:00",
        "mode": "sandbox",
        "metric_key": "accuracy",
        "metric_direction": "maximize",
        "max_iterations_requested": 5,
        "max_iterations_executed": 3,
        "baseline_metric": 0.72,
        "project_files": ["main.py", "requirements.txt"],
        "iterations": [
            {
                "iteration": 1,
                "version_dir": "experiment_v1/",
                "metric": 0.81,
                "improved": True,
            }
        ],
        "converged": True,
        "stop_reason": "no_improvement_for_2_iterations",
        "best_metric": 0.81,
        "best_version": "experiment_v1/",
        "final_version": "experiment_final/",
    }


def test_refinement_log_contract_normalizes_simulated_payload():
    log = RefinementLogContract.from_payload(
        {
            "mode": "simulated",
            "metric_key": "accuracy",
            "skipped": True,
            "skip_reason": "not meaningful",
        },
        generated="2026-05-17T12:00:00+00:00",
    )

    assert log.to_payload() == {
        "generated": "2026-05-17T12:00:00+00:00",
        "mode": "simulated",
        "metric_key": "accuracy",
        "iterations": [],
        "skipped": True,
        "skip_reason": "not meaningful",
    }


def test_refinement_log_contract_rejects_invalid_iterations():
    with pytest.raises(ValueError, match="iterations"):
        RefinementLogContract.from_payload(
            {
                "mode": "sandbox",
                "metric_key": "accuracy",
                "iterations": ["bad"],
            },
            generated="2026-05-17T12:00:00+00:00",
        )


def test_refinement_log_contract_rejects_negative_counts():
    with pytest.raises(ValueError, match="max_iterations_executed"):
        RefinementLogContract(
            generated="2026-05-17T12:00:00+00:00",
            mode="sandbox",
            metric_key="accuracy",
            max_iterations_executed=-1,
        )

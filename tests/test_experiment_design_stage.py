"""Tests for Stage 9 experiment design behavior."""

from __future__ import annotations

import yaml
from dataclasses import replace
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.config import ExperimentConfig, RCConfig
from researchclaw.pipeline.stage_impls._experiment_design import (
    _execute_experiment_design,
    _normalize_plan_field,
    _plan_field_names,
)
from researchclaw.pipeline.stages import Stage, StageStatus


def _write_hypotheses(run_dir: Path, text: str) -> None:
    stage_dir = run_dir / "stage-08"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "hypotheses.md").write_text(text, encoding="utf-8")


class TestPlanFieldNormalization:
    def test_none_and_blank_strings_normalize_to_empty_list(self) -> None:
        assert _normalize_plan_field(None) == []
        assert _normalize_plan_field("   ") == []

    def test_string_normalizes_to_single_item_list(self) -> None:
        assert _normalize_plan_field("baseline") == ["baseline"]

    def test_dict_values_preserve_nested_structure_with_names(self) -> None:
        normalized = _normalize_plan_field(
            {
                "baseline_a": {"description": "A strong baseline", "params": {"k": 3}},
                "baseline_b": "simple baseline",
                "baseline_c": None,
            }
        )

        assert normalized == [
            {"description": "A strong baseline", "params": {"k": 3}, "name": "baseline_a"},
            {"name": "baseline_b", "description": "simple baseline"},
            {"name": "baseline_c", "description": ""},
        ]

    def test_plan_field_names_extract_dict_names_and_stringify_scalars(self) -> None:
        assert _plan_field_names([
            {"name": "named"},
            {"description": "missing name"},
            "raw",
            7,
        ]) == ["named", "{'description': 'missing name'}", "raw", "7"]


class TestExecuteExperimentDesignFallback:
    def test_extracts_method_and_baseline_names_from_hypotheses(
        self,
        tmp_config: RCConfig,
        run_dir: Path,
        tmp_path: Path,
    ) -> None:
        stage_dir = tmp_path / "stage-09"
        stage_dir.mkdir()
        _write_hypotheses(
            run_dir,
            "Our novel method: SpiralNet should improve accuracy.\n"
            "Compare baseline method: GridSearch as the standard approach.\n",
        )

        result = _execute_experiment_design(
            stage_dir,
            run_dir,
            tmp_config,
            AdapterBundle(),
            llm=None,
        )

        assert result.stage == Stage.EXPERIMENT_DESIGN
        assert result.status == StageStatus.DONE
        assert result.artifacts == ("exp_plan.yaml",)
        plan = yaml.safe_load((stage_dir / "exp_plan.yaml").read_text(encoding="utf-8"))
        assert plan["topic"] == tmp_config.research.topic
        assert "SpiralNet" in plan["proposed_methods"]
        assert "GridSearch" in plan["baselines"]
        assert tmp_config.experiment.metric_key in plan["metrics"]

    def test_topic_derived_fallback_uses_topic_prefix_without_hypotheses(
        self,
        tmp_config: RCConfig,
        run_dir: Path,
        tmp_path: Path,
    ) -> None:
        stage_dir = tmp_path / "stage-09"
        stage_dir.mkdir()
        config = tmp_config.with_research_overrides(topic="graph transformers")

        _execute_experiment_design(stage_dir, run_dir, config, AdapterBundle(), llm=None)

        plan = yaml.safe_load((stage_dir / "exp_plan.yaml").read_text(encoding="utf-8"))
        assert plan["baselines"] == ["graph_baseline_1", "graph_baseline_2"]
        assert plan["proposed_methods"] == ["graph_proposed", "graph_variant"]
        assert plan["datasets"] == ["primary_dataset", "secondary_dataset"]

    def test_short_time_budget_trims_fallback_conditions_to_eight(
        self,
        tmp_config: RCConfig,
        run_dir: Path,
        tmp_path: Path,
    ) -> None:
        stage_dir = tmp_path / "stage-09"
        stage_dir.mkdir()
        config = replace(
            tmp_config,
            experiment=ExperimentConfig(time_budget_sec=300, metric_key="accuracy"),
        )
        _write_hypotheses(
            run_dir,
            "\n".join(
                [f"Our novel method: Method{i}" for i in range(1, 8)]
                + [f"baseline method: Base{i}" for i in range(1, 8)]
            ),
        )

        _execute_experiment_design(stage_dir, run_dir, config, AdapterBundle(), llm=None)

        plan = yaml.safe_load((stage_dir / "exp_plan.yaml").read_text(encoding="utf-8"))
        total_conditions = (
            len(plan["proposed_methods"])
            + len(plan["baselines"])
            + len(plan["ablations"])
        )
        assert total_conditions <= 8
        assert plan["proposed_methods"] == ["Method1", "Method2", "Method3"]
        assert plan["baselines"] == ["Base1", "Base2", "Base3"]
        assert plan["ablations"] == ["without_key_component", "simplified_version"]

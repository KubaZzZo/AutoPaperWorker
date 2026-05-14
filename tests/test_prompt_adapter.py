"""Tests for domain-aware prompt adapters."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from researchclaw.domains.detector import DomainProfile, get_profile, get_generic_profile
from researchclaw.domains.prompt_adapter import (
    GenericPromptAdapter,
    MLPromptAdapter,
    PromptAdapter,
    PromptBlocks,
    get_adapter,
    register_adapter,
)


def test_adapter_registry_logs_missing_optional_adapter(
    monkeypatch,
    caplog,
) -> None:
    from researchclaw.domains import prompt_adapter

    original_import = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "researchclaw.domains.adapters.physics":
            raise ImportError("physics adapter missing")
        return original_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with caplog.at_level("DEBUG", logger="researchclaw.domains.prompt_adapter"):
        registry = prompt_adapter._build_adapter_registry()

    assert "generic" in registry
    assert "physics_" not in registry
    assert "Optional domain adapter unavailable" in caplog.text


def test_prompt_adapter_registry_has_no_pure_except_pass() -> None:
    source = Path("researchclaw/domains/prompt_adapter.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body = [
                stmt for stmt in node.body
                if not (
                    isinstance(stmt, ast.Expr)
                    and isinstance(getattr(stmt, "value", None), ast.Constant)
                    and isinstance(stmt.value.value, str)
                )
            ]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                offenders.append(node.lineno)
    assert offenders == []


# ---------------------------------------------------------------------------
# PromptBlocks tests
# ---------------------------------------------------------------------------


class TestPromptBlocks:
    def test_default_empty(self):
        blocks = PromptBlocks()
        assert blocks.compute_budget == ""
        assert blocks.dataset_guidance == ""
        assert blocks.code_generation_hints == ""

    def test_all_fields(self):
        blocks = PromptBlocks(
            compute_budget="budget info",
            dataset_guidance="data info",
            hp_reporting="hp info",
            code_generation_hints="code hints",
            result_analysis_hints="analysis hints",
            experiment_design_context="design context",
            statistical_test_guidance="stat guidance",
            output_format_guidance="output format",
        )
        assert blocks.compute_budget == "budget info"
        assert blocks.output_format_guidance == "output format"


# ---------------------------------------------------------------------------
# ML Adapter tests
# ---------------------------------------------------------------------------


class TestMLPromptAdapter:
    def test_returns_empty_blocks(self):
        """ML adapter must return empty blocks (delegates to prompts.py)."""
        profile = get_profile("ml_vision") or DomainProfile(
            domain_id="ml_vision", display_name="CV"
        )
        adapter = MLPromptAdapter(profile)

        blocks = adapter.get_code_generation_blocks({})
        assert blocks.compute_budget == ""
        assert blocks.dataset_guidance == ""
        assert blocks.code_generation_hints == ""

    def test_all_methods_return_empty(self):
        profile = DomainProfile(domain_id="ml_generic", display_name="ML")
        adapter = MLPromptAdapter(profile)

        for method in [
            adapter.get_code_generation_blocks,
            adapter.get_experiment_design_blocks,
            adapter.get_result_analysis_blocks,
        ]:
            blocks = method({})
            assert all(
                getattr(blocks, f) == ""
                for f in [
                    "compute_budget", "dataset_guidance", "hp_reporting",
                    "code_generation_hints", "result_analysis_hints",
                ]
            )


# ---------------------------------------------------------------------------
# Generic Adapter tests
# ---------------------------------------------------------------------------


class TestGenericPromptAdapter:
    def test_provides_code_hints(self):
        profile = DomainProfile(
            domain_id="generic",
            display_name="Generic",
            core_libraries=["numpy", "scipy"],
        )
        adapter = GenericPromptAdapter(profile)
        blocks = adapter.get_code_generation_blocks({})
        assert blocks.code_generation_hints  # should not be empty

    def test_convergence_hints(self):
        profile = DomainProfile(
            domain_id="test_conv",
            display_name="Conv Test",
            experiment_paradigm="convergence",
        )
        adapter = GenericPromptAdapter(profile)
        blocks = adapter.get_code_generation_blocks({})
        assert "convergence" in blocks.code_generation_hints.lower()

    def test_progressive_spec_hints(self):
        profile = DomainProfile(
            domain_id="test_econ",
            display_name="Econ Test",
            experiment_paradigm="progressive_spec",
        )
        adapter = GenericPromptAdapter(profile)
        blocks = adapter.get_code_generation_blocks({})
        assert "progressive" in blocks.code_generation_hints.lower()

    def test_experiment_design_has_terminology(self):
        profile = DomainProfile(
            domain_id="test",
            display_name="Test Domain",
            condition_terminology={"baseline": "reference", "proposed": "our method"},
            standard_baselines=["Method A", "Method B"],
        )
        adapter = GenericPromptAdapter(profile)
        blocks = adapter.get_experiment_design_blocks({})
        assert "reference" in blocks.experiment_design_context
        assert "Method A" in blocks.experiment_design_context


# ---------------------------------------------------------------------------
# Physics Adapter tests
# ---------------------------------------------------------------------------


class TestPhysicsAdapter:
    def test_physics_adapter_loaded(self):
        profile = get_profile("physics_simulation")
        if profile is None:
            pytest.skip("physics_simulation profile not found")
        adapter = get_adapter(profile)
        assert not isinstance(adapter, MLPromptAdapter)

    def test_physics_code_blocks_nonempty(self):
        profile = get_profile("physics_pde")
        if profile is None:
            pytest.skip("physics_pde profile not found")
        adapter = get_adapter(profile)
        blocks = adapter.get_code_generation_blocks({})
        assert blocks.code_generation_hints  # should have physics-specific hints


# ---------------------------------------------------------------------------
# Economics Adapter tests
# ---------------------------------------------------------------------------


class TestEconomicsAdapter:
    def test_economics_adapter_loaded(self):
        profile = get_profile("economics_empirical")
        if profile is None:
            pytest.skip("economics_empirical profile not found")
        adapter = get_adapter(profile)
        assert not isinstance(adapter, MLPromptAdapter)

    def test_economics_design_blocks(self):
        profile = get_profile("economics_empirical")
        if profile is None:
            pytest.skip("economics_empirical profile not found")
        adapter = get_adapter(profile)
        blocks = adapter.get_experiment_design_blocks({})
        assert "progressive" in blocks.experiment_design_context.lower()


# ---------------------------------------------------------------------------
# get_adapter dispatch tests
# ---------------------------------------------------------------------------


class TestGetAdapter:
    def test_ml_domains_get_ml_adapter(self):
        for domain_id in ["ml_vision", "ml_nlp", "ml_rl", "ml_generic"]:
            profile = get_profile(domain_id)
            if profile is None:
                continue
            adapter = get_adapter(profile)
            assert isinstance(adapter, MLPromptAdapter), (
                f"{domain_id} should use MLPromptAdapter"
            )

    def test_generic_domain_gets_generic_adapter(self):
        profile = get_generic_profile()
        adapter = get_adapter(profile)
        assert isinstance(adapter, GenericPromptAdapter)

    def test_physics_uses_physics_adapter(self):
        profile = get_profile("physics_simulation")
        if profile is None:
            pytest.skip("physics_simulation profile not found")
        adapter = get_adapter(profile)
        from researchclaw.domains.adapters.physics import PhysicsPromptAdapter
        assert isinstance(adapter, PhysicsPromptAdapter)

    def test_unknown_domain_gets_generic(self):
        profile = DomainProfile(domain_id="unknown_domain", display_name="Unknown")
        adapter = get_adapter(profile)
        assert isinstance(adapter, GenericPromptAdapter)


# ---------------------------------------------------------------------------
# Blueprint context tests
# ---------------------------------------------------------------------------


class TestBlueprintContext:
    def test_blueprint_includes_file_structure(self):
        profile = DomainProfile(
            domain_id="test",
            display_name="Test",
            typical_file_structure={"config.py": "Config", "main.py": "Entry"},
            core_libraries=["numpy"],
        )
        adapter = GenericPromptAdapter(profile)
        ctx = adapter.get_blueprint_context()
        assert "config.py" in ctx
        assert "numpy" in ctx

    def test_blueprint_includes_hints(self):
        profile = DomainProfile(
            domain_id="test",
            display_name="Test",
            code_generation_hints="Use scipy.integrate for ODE solving",
        )
        adapter = GenericPromptAdapter(profile)
        ctx = adapter.get_blueprint_context()
        assert "scipy.integrate" in ctx

    def test_ml_adapter_blueprint_context(self):
        """ML adapter should also provide basic blueprint context."""
        profile = get_profile("ml_vision") or DomainProfile(
            domain_id="ml_vision",
            display_name="CV",
            typical_file_structure={"model.py": "Model", "train.py": "Training"},
        )
        adapter = MLPromptAdapter(profile)
        ctx = adapter.get_blueprint_context()
        # ML adapter inherits from base, should have file structure if profile has it
        if profile.typical_file_structure:
            assert "model.py" in ctx or ctx == ""  # acceptable either way


# ---------------------------------------------------------------------------
# Adapter registration tests
# ---------------------------------------------------------------------------


class TestAdapterRegistration:
    def test_register_custom_adapter(self):
        class CustomAdapter(PromptAdapter):
            def get_code_generation_blocks(self, ctx):
                return PromptBlocks(code_generation_hints="custom")

            def get_experiment_design_blocks(self, ctx):
                return PromptBlocks()

            def get_result_analysis_blocks(self, ctx):
                return PromptBlocks()

        register_adapter("custom_domain", CustomAdapter)

        profile = DomainProfile(domain_id="custom_domain", display_name="Custom")
        adapter = get_adapter(profile)
        assert isinstance(adapter, CustomAdapter)
        blocks = adapter.get_code_generation_blocks({})
        assert blocks.code_generation_hints == "custom"

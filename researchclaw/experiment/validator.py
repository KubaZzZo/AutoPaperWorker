"""Experiment code validation: syntax, security, and quality checks.

This module preserves the historical import surface while focused validator
implementations live in smaller modules.
"""

from __future__ import annotations

from researchclaw.experiment.validator_api import (
    check_api_correctness,
    check_filename_collisions,
    check_undefined_calls,
)
from researchclaw.experiment.validator_classes import check_class_quality
from researchclaw.experiment.validator_core import (
    BANNED_MODULES,
    COMMON_SCIENCE,
    DANGEROUS_BUILTINS,
    DANGEROUS_CALLS,
    SAFE_STDLIB,
    CodeValidation,
    ValidationIssue,
    _SecurityVisitor,
    _resolve_call_name,
    check_code_complexity,
    extract_imports,
    format_issues_for_llm,
    validate_code,
    validate_imports,
    validate_security,
    validate_syntax,
)
from researchclaw.experiment.validator_data import (
    check_capacity_fairness,
    check_data_split_overlap,
    check_loss_direction,
)
from researchclaw.experiment.validator_scoping import (
    _collect_if_only_assignments,
    _extract_assign_targets,
    auto_fix_unbound_locals,
    check_variable_scoping,
)


def deep_validate_files(
    files: dict[str, str],
) -> list[str]:
    """Run all deep quality checks across all experiment files.

    Returns a list of warning strings. Empty = no concerns.
    """
    warnings: list[str] = []
    warnings.extend(check_class_quality(files))
    warnings.extend(check_filename_collisions(files))
    for fname, code in files.items():
        if not fname.endswith(".py"):
            continue
        warnings.extend(check_variable_scoping(code, fname))
        warnings.extend(check_api_correctness(code, fname))
        warnings.extend(check_undefined_calls(code, fname))
        warnings.extend(check_data_split_overlap(code, fname))
        warnings.extend(check_loss_direction(code, fname))
        warnings.extend(check_capacity_fairness(code, fname))
    return warnings


__all__ = [
    "BANNED_MODULES",
    "COMMON_SCIENCE",
    "DANGEROUS_BUILTINS",
    "DANGEROUS_CALLS",
    "SAFE_STDLIB",
    "CodeValidation",
    "ValidationIssue",
    "_SecurityVisitor",
    "_collect_if_only_assignments",
    "_extract_assign_targets",
    "_resolve_call_name",
    "auto_fix_unbound_locals",
    "check_api_correctness",
    "check_capacity_fairness",
    "check_class_quality",
    "check_code_complexity",
    "check_data_split_overlap",
    "check_filename_collisions",
    "check_loss_direction",
    "check_undefined_calls",
    "check_variable_scoping",
    "deep_validate_files",
    "extract_imports",
    "format_issues_for_llm",
    "validate_code",
    "validate_imports",
    "validate_security",
    "validate_syntax",
]

"""Class-level quality checks for generated experiment code."""

from __future__ import annotations

import ast
import logging
from typing import Any

from researchclaw.experiment.validator_core import _resolve_call_name

logger = logging.getLogger("researchclaw.experiment.validator")


def check_class_quality(all_files: dict[str, str]) -> list[str]:
    """Analyze class implementations across all experiment files.

    Detects:
    - Empty or trivial class inheritance (class B(A): pass)
    - Classes with too few methods (< 2 non-dunder)
    - Duplicate class bodies (identical forward/train logic across variants)
    - nn.Module created inside forward() instead of __init__()
    """
    warnings: list[str] = []

    class_info: dict[str, dict[str, Any]] = {}

    for fname, code in all_files.items():
        if not fname.endswith(".py"):
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            cls_name = node.name
            methods: list[str] = []
            method_sources: dict[str, str] = {}
            has_forward_new_module = False
            body_lines = 0

            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
                    # Approximate method body size
                    m_start = item.lineno
                    m_end = item.end_lineno or item.lineno
                    body_len = m_end - m_start
                    method_sources[item.name] = f"{fname}:{m_start}-{m_end}"

                    # Check for nn.Module creation inside forward()
                    if item.name in ("forward", "__call__"):
                        for sub in ast.walk(item):
                            if isinstance(sub, ast.Call):
                                call_name = _resolve_call_name(sub.func)
                                if call_name.startswith("nn.") and call_name != "nn.Module":
                                    has_forward_new_module = True

            # Count effective body lines
            code_lines = code.splitlines()
            if node.end_lineno and node.lineno:
                cls_body = code_lines[node.lineno - 1 : node.end_lineno]
                body_lines = sum(
                    1 for l in cls_body
                    if l.strip() and not l.strip().startswith("#")
                    and not l.strip().startswith(("import ", "from "))
                )

            non_dunder = [m for m in methods if not m.startswith("__")]

            has_explicit_bases = bool(node.bases)

            class_info[f"{fname}:{cls_name}"] = {
                "methods": methods,
                "non_dunder": non_dunder,
                "body_lines": body_lines,
                "file": fname,
                "has_forward_new_module": has_forward_new_module,
                "class_name": cls_name,
                "has_explicit_bases": has_explicit_bases,
            }

            # --- Check 1: Empty or trivial class ---
            if body_lines <= 2:
                warnings.append(
                    f"[{fname}] Class '{cls_name}' has only {body_lines} body lines "
                    f"— likely an empty or trivial subclass (class B(A): pass)"
                )

            # --- Check 2: Too few methods for an algorithm class ---
            if (
                body_lines > 5
                and len(non_dunder) < 2
                and not has_explicit_bases
            ):
                warnings.append(
                    f"[{fname}] Class '{cls_name}' has only {len(non_dunder)} "
                    f"non-dunder method(s) — algorithm classes should have at "
                    f"least __init__ + one core method (forward/train_step/predict)"
                )

            # --- Check 3: nn.Module created in forward() ---
            if has_forward_new_module:
                warnings.append(
                    f"[{fname}] Class '{cls_name}' creates nn.Module (nn.Linear etc.) "
                    f"inside forward() — these modules are unregistered and untrained. "
                    f"Move to __init__() and register as submodules."
                )

    # --- Check 4: Duplicate class names across files ---
    duplicated_class_names: set[str] = set()
    classes_by_name: dict[str, list[dict[str, Any]]] = {}
    for info in class_info.values():
        classes_by_name.setdefault(str(info["class_name"]), []).append(info)

    for cls_name, entries in classes_by_name.items():
        non_trivial = [entry for entry in entries if int(entry["body_lines"]) > 5]
        files = sorted({str(entry["file"]) for entry in non_trivial})
        if len(files) >= 2:
            duplicated_class_names.add(cls_name)
            warnings.append(
                f"Class '{cls_name}' is defined in multiple files "
                f"({', '.join(files)}). Keep each algorithm/helper class in one "
                f"canonical module and import it elsewhere instead of duplicating "
                f"the definition."
            )

    # --- Check 5: Duplicate class implementations ---
    # Compare class body hashes to find copy-paste variants
    class_names = list(class_info.keys())
    for i, name_a in enumerate(class_names):
        info_a = class_info[name_a]
        for name_b in class_names[i + 1:]:
            info_b = class_info[name_b]
            if (
                str(info_a["class_name"]) == str(info_b["class_name"])
                and str(info_a["class_name"]) in duplicated_class_names
            ):
                continue
            if (
                info_a["body_lines"] > 5
                and info_b["body_lines"] > 5
                and info_a["non_dunder"] == info_b["non_dunder"]
                and abs(info_a["body_lines"] - info_b["body_lines"]) <= 2
            ):
                # Same methods, same body size — likely duplicates
                warnings.append(
                    f"Classes '{name_a.split(':')[1]}' and '{name_b.split(':')[1]}' "
                    f"have identical method signatures and similar body sizes "
                    f"({info_a['body_lines']} vs {info_b['body_lines']} lines) — "
                    f"may be copy-paste variants with no real algorithmic difference"
                )

    # --- Check 6: Ablation subclasses must override with different logic ---
    # Parse inheritance relationships and compare method ASTs
    for fname_code, code in all_files.items():
        if not fname_code.endswith(".py"):
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue

        # Build {class_name: ClassDef} map for this file
        file_classes: dict[str, ast.ClassDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                file_classes[node.name] = node

        for cls_name, cls_node in file_classes.items():
            # Check if this class inherits from another class in the same file
            for base in cls_node.bases:
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if not base_name or base_name not in file_classes:
                    continue

                parent_node = file_classes[base_name]
                # Get method bodies as AST dumps for comparison
                child_methods = {
                    m.name: ast.dump(m)
                    for m in cls_node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not m.name.startswith("__")
                }
                parent_methods = {
                    m.name: ast.dump(m)
                    for m in parent_node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not m.name.startswith("__")
                }

                if not child_methods:
                    # Already caught by Check 1 (empty class)
                    continue

                # Check if all overridden methods have identical AST to parent
                identical_count = 0
                override_count = 0
                for method_name, method_dump in child_methods.items():
                    if method_name in parent_methods:
                        override_count += 1
                        if method_dump == parent_methods[method_name]:
                            identical_count += 1

                if override_count > 0 and identical_count == override_count:
                    warnings.append(
                        f"[{fname_code}] Class '{cls_name}' inherits from "
                        f"'{base_name}' and overrides {override_count} method(s), "
                        f"but ALL overridden methods have identical AST to parent "
                        f"— this is NOT a real ablation. Methods must differ."
                    )
                elif override_count == 0 and len(child_methods) > 0:
                    # Has methods but none override parent — might be fine
                    # (new methods that parent doesn't have)
                    pass

                # --- Check 7: Ablation subclass must override >=1 parent method ---
                _lname = cls_name.lower()
                if ("ablation" in _lname or "no_" in _lname or "without" in _lname):
                    parent_non_dunder = {
                        m.name
                        for m in parent_node.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not m.name.startswith("__")
                    }
                    child_overrides = set(child_methods.keys()) & parent_non_dunder
                    if not child_overrides and parent_non_dunder:
                        warnings.append(
                            f"[{fname_code}] Ablation class '{cls_name}' inherits "
                            f"from '{base_name}' but does NOT override any of its "
                            f"methods ({', '.join(sorted(parent_non_dunder))}). "
                            f"An ablation MUST override the method that removes "
                            f"the ablated component."
                        )

    return warnings

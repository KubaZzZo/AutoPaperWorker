"""Variable-scoping checks and repairs for generated experiment code."""

from __future__ import annotations

import ast


def check_variable_scoping(code: str, fname: str = "main.py") -> list[str]:
    """Detect common variable scoping bugs in experiment code.

    Catches the pattern where a variable is defined inside an if-branch
    but used outside that branch (UnboundLocalError at runtime).
    """
    warnings: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Collect variables assigned only inside if/elif/else branches
        if_only_vars: dict[str, int] = {}
        top_level_vars: set[str] = set()

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                _collect_if_only_assignments(child, if_only_vars)
            elif isinstance(child, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                for target in _extract_assign_targets(child):
                    top_level_vars.add(target)

        # Check for variables used after the if block but only defined inside it
        for var_name, var_line in if_only_vars.items():
            if var_name not in top_level_vars:
                # Check if this variable is used later in the function
                for later_node in ast.walk(node):
                    if (
                        isinstance(later_node, ast.Name)
                        and later_node.id == var_name
                        and isinstance(later_node.ctx, ast.Load)
                        and later_node.lineno > var_line
                    ):
                        warnings.append(
                            f"[{fname}:{var_line}] Variable '{var_name}' is assigned "
                            f"only inside an if-branch but used at line "
                            f"{later_node.lineno} — will cause UnboundLocalError "
                            f"if the branch is not taken"
                        )
                        break

    return warnings


def _collect_if_only_assignments(
    if_node: ast.If, result: dict[str, int]
) -> None:
    """Collect variables assigned only inside if/elif branches."""
    for child in ast.iter_child_nodes(if_node):
        if isinstance(child, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for target in _extract_assign_targets(child):
                result[target] = child.lineno
        elif isinstance(child, ast.If):
            _collect_if_only_assignments(child, result)


def _extract_assign_targets(node: ast.AST) -> list[str]:
    """Extract variable names from assignment targets."""
    names: list[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    elif isinstance(node, ast.AugAssign) or isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def auto_fix_unbound_locals(code: str) -> tuple[str, int]:
    """Programmatically fix UnboundLocalError patterns.

    For each variable assigned only inside an if-branch but used later,
    insert ``var = None`` before the if-statement.

    Returns (fixed_code, num_fixes).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, 0

    lines = code.splitlines(keepends=True)
    insertions: dict[int, list[str]] = {}  # lineno -> lines to insert before

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if_only_vars: dict[str, int] = {}
        top_level_vars: set[str] = set()
        if_line_map: dict[str, int] = {}  # var -> if-statement lineno

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                before: dict[str, int] = {}
                _collect_if_only_assignments(child, before)
                for var_name, var_line in before.items():
                    if_only_vars[var_name] = var_line
                    if_line_map[var_name] = child.lineno
            elif isinstance(child, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                for target in _extract_assign_targets(child):
                    top_level_vars.add(target)

        for var_name, var_line in if_only_vars.items():
            if var_name in top_level_vars:
                continue
            # Confirm it's actually used later
            used_later = False
            for later_node in ast.walk(node):
                if (
                    isinstance(later_node, ast.Name)
                    and later_node.id == var_name
                    and isinstance(later_node.ctx, ast.Load)
                    and later_node.lineno > var_line
                ):
                    used_later = True
                    break
            if not used_later:
                continue

            if_lineno = if_line_map.get(var_name)
            if if_lineno is None:
                continue
            # Determine indentation of the if-statement
            if if_lineno <= len(lines):
                if_line = lines[if_lineno - 1]
                indent = if_line[: len(if_line) - len(if_line.lstrip())]
            else:
                indent = "    "
            insertions.setdefault(if_lineno, [])
            fix_line = f"{indent}{var_name} = None\n"
            if fix_line not in insertions[if_lineno]:
                insertions[if_lineno].append(fix_line)

    if not insertions:
        return code, 0

    # Apply insertions in reverse line order to keep line numbers stable
    num_fixes = sum(len(v) for v in insertions.values())
    for lineno in sorted(insertions, reverse=True):
        idx = lineno - 1
        for fix_line in reversed(insertions[lineno]):
            lines.insert(idx, fix_line)

    return "".join(lines), num_fixes

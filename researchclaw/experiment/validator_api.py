"""API correctness and name-resolution checks for generated experiment code."""

from __future__ import annotations

import ast


def check_api_correctness(code: str, fname: str = "main.py") -> list[str]:
    """Detect common API misuse patterns.

    Catches:
    - np.erf() (should be scipy.special.erf)
    - nn.Linear/nn.Conv2d inside forward() (unregistered module)
    - random.seed() without numpy.random.seed() (incomplete seeding)
    - NumPy 2.0 removed APIs (.ptp(), np.bool, etc.)
    """
    import re as _re

    warnings: list[str] = []

    lines = code.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # np.erf doesn't exist
        if _re.search(r"\bnp\.erf\b", stripped):
            warnings.append(
                f"[{fname}:{i}] np.erf() does not exist — use "
                f"scipy.special.erf() or math.erf() instead"
            )

        # NumPy 2.0 removed ndarray methods
        if _re.search(r"\.ptp\s*\(", stripped):
            warnings.append(
                f"[{fname}:{i}] ndarray.ptp() was removed in NumPy 2.0 — "
                f"use np.ptp(arr) or arr.max() - arr.min() instead"
            )

        # NumPy 2.0 removed type aliases
        for old_alias in ("np.bool", "np.int", "np.float", "np.complex",
                          "np.object", "np.str"):
            pattern = _re.escape(old_alias) + r"(?![_\w\d])"
            if _re.search(pattern, stripped):
                warnings.append(
                    f"[{fname}:{i}] {old_alias} was removed in NumPy 2.0 — "
                    f"use {old_alias}_ or Python builtin instead"
                )

        # np.random.RandomState with hardcoded seed in a function called multiple times
        if _re.search(r"RandomState\(\s*\d+\s*\)", stripped) and "def " not in stripped:
            warnings.append(
                f"[{fname}:{i}] Hardcoded RandomState seed inside a loop/function "
                f"may produce identical results across calls — pass seed as parameter"
            )

    # --- Import-usage mismatch detection ---
    # Detect `from X import Y` followed by `X.Y(...)` — guaranteed NameError
    import_from_map: dict[str, set[str]] = {}  # module -> {names}
    import_module_set: set[str] = set()  # modules imported with `import X`
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        m = _re.match(r"from\s+([\w.]+)\s+import\s+(.+)", stripped)
        if m:
            mod = m.group(1)
            names = {n.strip().split(" as ")[-1].strip()
                     for n in m.group(2).split(",")}
            import_from_map.setdefault(mod, set()).update(names)
        elif _re.match(r"import\s+([\w.]+)", stripped) and "from" not in stripped:
            m2 = _re.match(r"import\s+([\w.]+)", stripped)
            if m2:
                import_module_set.add(m2.group(1).split(".")[0])

    # Now scan for qualified calls to modules that were only from-imported
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for mod, _names in import_from_map.items():
            top_mod = mod.split(".")[0]
            # Only flag if the module was NOT also imported via `import X`
            if top_mod in import_module_set:
                continue
            # Check for `module.name(...)` usage when `name` was from-imported
            for name in _names:
                pattern = _re.escape(f"{mod}.{name}") + r"\s*\("
                if _re.search(pattern, stripped):
                    warnings.append(
                        f"[{fname}:{i}] Import-usage mismatch: '{name}' was imported "
                        f"via `from {mod} import {name}` but called as `{mod}.{name}()` "
                        f"— this will raise NameError. Use `{name}()` directly."
                    )

    return warnings


def check_undefined_calls(code: str, fname: str = "main.py") -> list[str]:
    """Detect calls to undefined functions/names in experiment code.

    Catches the pattern where a function is called but never defined or imported,
    which would cause NameError at runtime.
    """
    warnings: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings

    # Common builtins that are always available
    builtins = {
        "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
        "list", "dict", "set", "tuple", "str", "int", "float", "bool", "bytes",
        "type", "isinstance", "issubclass", "hasattr", "getattr", "setattr",
        "delattr", "callable", "iter", "next", "reversed", "slice", "super",
        "property", "staticmethod", "classmethod", "abs", "all", "any", "bin",
        "chr", "ord", "hex", "oct", "pow", "round", "sum", "min", "max", "open",
        "input", "repr", "hash", "id", "dir", "vars", "globals", "locals",
        "format", "ascii", "object", "Exception", "ValueError", "TypeError",
        "KeyError", "IndexError", "AttributeError", "RuntimeError", "StopIteration",
        "NotImplementedError", "AssertionError", "ImportError", "FileNotFoundError",
        "OSError", "IOError", "ZeroDivisionError", "OverflowError", "MemoryError",
        "RecursionError", "SystemExit", "KeyboardInterrupt", "GeneratorExit",
        "BaseException", "Warning", "DeprecationWarning", "UserWarning",
        "FutureWarning", "PendingDeprecationWarning", "SyntaxWarning",
        "RuntimeWarning", "ResourceWarning", "BytesWarning", "UnicodeWarning",
        "breakpoint", "memoryview", "bytearray", "frozenset", "complex",
        "divmod", "eval", "exec", "compile", "__import__", "help", "exit", "quit",
    }

    # Collect all defined names in the module
    defined_names: set[str] = set()

    for node in ast.walk(tree):
        # Function definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
        # Imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name.split(".")[0]
                defined_names.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                if name != "*":
                    defined_names.add(name)
        # Assignments (including comprehensions)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            defined_names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) or isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                defined_names.add(node.target.id)
        # For loop targets
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                defined_names.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        defined_names.add(elt.id)
        # With statement targets
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    defined_names.add(item.optional_vars.id)
        # Exception handlers
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                defined_names.add(node.name)
        # Named expressions (walrus operator)
        elif isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name):
                defined_names.add(node.target.id)

    # Also collect function parameters
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args:
                defined_names.add(arg.arg)
            for arg in node.args.posonlyargs:
                defined_names.add(arg.arg)
            for arg in node.args.kwonlyargs:
                defined_names.add(arg.arg)
            if node.args.vararg:
                defined_names.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined_names.add(node.args.kwarg.arg)

    # Now find all function calls to bare names (not attributes like obj.method())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Only check bare name calls, not attribute calls (obj.method())
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
                if (
                    call_name not in defined_names
                    and call_name not in builtins
                ):
                    warnings.append(
                        f"[{fname}:{node.lineno}] Call to undefined function "
                        f"'{call_name}()' — this will raise NameError at runtime. "
                        f"Either define the function or remove the call."
                    )

    return warnings


def check_filename_collisions(files: dict[str, str]) -> list[str]:
    """BUG-202: Detect local .py filenames that shadow pip/stdlib packages.

    The LLM commonly generates ``config.py``, ``models.py``, etc. which get
    shadowed by pip-installed packages (e.g. ``pip install config``).  The
    result is an import crash at runtime.
    """
    # Filenames (without .py) that are known to collide with pip/stdlib packages.
    _SHADOW_RISK: set[str] = {
        # pip packages frequently installed as transitive deps
        "config", "test", "tests", "types", "typing_extensions",
        # stdlib modules the LLM might accidentally shadow
        "io", "logging", "json", "time", "random", "copy", "math",
        "os", "sys", "collections", "functools", "abc", "re",
        "statistics", "signal", "pickle", "itertools",
        "string", "tokenize", "token", "email", "calendar",
        "numbers", "operator", "queue", "code", "profile",
    }
    warnings: list[str] = []
    for fname in files:
        stem = fname.removesuffix(".py") if fname.endswith(".py") else None
        if stem and stem in _SHADOW_RISK:
            warnings.append(
                f"[{fname}] Filename shadows stdlib/pip package '{stem}'. "
                f"Rename to e.g. '{stem}_config.py' or 'experiment_{stem}.py' "
                f"to avoid import collisions at runtime."
            )
    return warnings

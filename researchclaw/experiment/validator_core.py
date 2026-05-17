"""Core experiment-code validation primitives."""

from __future__ import annotations

import ast
import logging
import sys
from dataclasses import dataclass, field

logger = logging.getLogger("researchclaw.experiment.validator")


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # "error" | "warning"
    category: str  # "syntax" | "security" | "import" | "style"
    message: str
    line: int | None = None
    col: int | None = None


@dataclass
class CodeValidation:
    """Aggregated validation result for a code snippet."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        errs = len(self.errors)
        warns = len(self.warnings)
        if errs == 0 and warns == 0:
            return "Code validation passed."
        parts: list[str] = []
        if errs:
            parts.append(f"{errs} error(s)")
        if warns:
            parts.append(f"{warns} warning(s)")
        return "Code validation: " + ", ".join(parts)



# ---------------------------------------------------------------------------
# Dangerous call patterns (security scan)
# ---------------------------------------------------------------------------

# Fully-qualified call names that are forbidden in experiment code.
DANGEROUS_CALLS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.popen",
        "os.exec",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
        "shutil.rmtree",
    }
)

# Bare built-in names that should never appear in experiment code.
DANGEROUS_BUILTINS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
    }
)

# Modules that should not be imported at all.
BANNED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "shutil",
        "socket",
        "http",
        "urllib",
        "requests",
        "ftplib",
        "smtplib",
        "ctypes",
        "signal",
    }
)

# Packages considered safe / always available in experiment sandbox.
SAFE_STDLIB: frozenset[str] = frozenset(
    {
        "abc",
        "ast",
        "bisect",
        "builtins",
        "collections",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "glob",
        "gzip",
        "hashlib",
        "heapq",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "operator",
        "os",  # os itself is ok, certain calls aren't
        "pathlib",
        "pickle",
        "pprint",
        "random",
        "re",
        "statistics",
        "string",
        "struct",
        "sys",
        "tempfile",
        "textwrap",
        "time",
        "traceback",
        "typing",
        "unittest",
        "uuid",
        "warnings",
        "zipfile",
    }
)

COMMON_SCIENCE: frozenset[str] = frozenset(
    {
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "sklearn",
        "matplotlib",
        "plt",
        "seaborn",
        "torch",
        "tensorflow",
        "tf",
        "jax",
        "transformers",
        "datasets",
        "tqdm",
        "yaml",
        "pyyaml",
        "rich",
        # LLM training stack
        "peft",
        "trl",
        "accelerate",
        "bitsandbytes",
        "sentencepiece",
        "tokenizers",
        "safetensors",
        "evaluate",
        "rouge_score",
        # Runtime-injected by the experiment harness
        "experiment_harness",
    }
)


# ---------------------------------------------------------------------------
# AST visitor for security checks
# ---------------------------------------------------------------------------



class _SecurityVisitor(ast.NodeVisitor):
    """Walk AST to detect dangerous calls and imports."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    # -- function calls --

    def visit_Call(self, node: ast.Call) -> None:
        name = _resolve_call_name(node.func)
        if name in DANGEROUS_BUILTINS:
            self.issues.append(
                ValidationIssue(
                    severity="error",
                    category="security",
                    message=f"Dangerous built-in call: {name}()",
                    line=node.lineno,
                    col=node.col_offset,
                )
            )
        elif name in DANGEROUS_CALLS:
            self.issues.append(
                ValidationIssue(
                    severity="error",
                    category="security",
                    message=f"Dangerous call: {name}()",
                    line=node.lineno,
                    col=node.col_offset,
                )
            )
        self.generic_visit(node)

    # -- import statements --

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in BANNED_MODULES:
                self.issues.append(
                    ValidationIssue(
                        severity="error",
                        category="security",
                        message=f"Banned module import: {alias.name}",
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top in BANNED_MODULES:
                self.issues.append(
                    ValidationIssue(
                        severity="error",
                        category="security",
                        message=f"Banned module import: from {node.module}",
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)


def _resolve_call_name(node: ast.expr) -> str:
    """Best-effort name resolution for a Call node's func."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _resolve_call_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
        return node.attr
    return ""


def extract_imports(code: str) -> set[str]:
    """Return top-level module names imported by *code*.

    Returns an empty set if the code can't be parsed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def validate_syntax(code: str) -> CodeValidation:
    """Check *code* parses as valid Python."""
    result = CodeValidation()
    try:
        ast.parse(code)
    except SyntaxError as exc:
        result.issues.append(
            ValidationIssue(
                severity="error",
                category="syntax",
                message=str(exc.msg) if exc.msg else str(exc),
                line=exc.lineno,
                col=exc.offset,
            )
        )
    return result


def validate_security(code: str) -> CodeValidation:
    """Scan *code* AST for dangerous calls and imports."""
    result = CodeValidation()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # If can't parse, skip security — syntax check will catch it.
        return result
    visitor = _SecurityVisitor()
    visitor.visit(tree)
    result.issues.extend(visitor.issues)
    return result


def validate_imports(
    code: str,
    available: set[str] | None = None,
) -> CodeValidation:
    """Check that all imported modules are available.

    *available* defaults to ``SAFE_STDLIB | COMMON_SCIENCE`` plus any
    modules already in ``sys.modules``.
    """
    result = CodeValidation()
    if available is None:
        available = set(SAFE_STDLIB) | set(COMMON_SCIENCE) | set(sys.modules.keys())

    imports = extract_imports(code)
    for mod in sorted(imports):
        if mod not in available:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="import",
                    message=f"Module '{mod}' may not be available in sandbox",
                )
            )
    return result


def validate_code(
    code: str,
    *,
    available_packages: set[str] | None = None,
    skip_security: bool = False,
    skip_imports: bool = False,
) -> CodeValidation:
    """Run all validations and return a combined :class:`CodeValidation`.

    1. Syntax check (always)
    2. Security scan (unless *skip_security*)
    3. Import availability (unless *skip_imports*)
    """
    combined = CodeValidation()

    # 1. Syntax
    syntax = validate_syntax(code)
    combined.issues.extend(syntax.issues)
    if not syntax.ok:
        # No point running further checks if code doesn't parse
        return combined

    # 2. Security
    if not skip_security:
        security = validate_security(code)
        combined.issues.extend(security.issues)

    # 3. Import availability
    if not skip_imports:
        imp = validate_imports(code, available=available_packages)
        combined.issues.extend(imp.issues)

    return combined


def format_issues_for_llm(validation: CodeValidation) -> str:
    """Format validation issues as a concise error report for LLM repair."""
    if validation.ok and not validation.warnings:
        return "No issues found."
    lines: list[str] = []
    for issue in validation.issues:
        loc = f"line {issue.line}" if issue.line else "unknown location"
        lines.append(
            f"- [{issue.severity.upper()}] ({issue.category}) {issue.message} @ {loc}"
        )
    return "\n".join(lines)


def check_code_complexity(code: str) -> list[str]:
    """Check whether generated experiment code is too simplistic.

    Returns a list of warning strings.  Empty list means no quality concerns.
    """
    warnings: list[str] = []

    # Count non-blank, non-comment, non-import lines
    effective_lines = [
        l
        for l in code.splitlines()
        if l.strip()
        and not l.strip().startswith("#")
        and not l.strip().startswith(("import ", "from "))
    ]

    if len(effective_lines) < 10:
        warnings.append(
            f"Code has only {len(effective_lines)} effective lines "
            f"(excluding blanks/comments/imports) — likely too simple for "
            f"a research experiment"
        )

    # Check for trivially short functions/methods
    try:
        tree = ast.parse(code)
        func_count = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_count += 1
        if func_count == 0 and len(effective_lines) > 5:
            warnings.append(
                "Code has no function definitions — research experiments "
                "should be structured with reusable functions"
            )
    except SyntaxError as exc:
        logger.debug(
            "Could not parse code while checking complexity: %s",
            exc,
        )

    # Check for hardcoded metrics (a common LLM failure mode)
    import re

    hardcoded_patterns = [
        (r"print\(['\"].*:\s*0\.\d+['\"]\)", "print statement with hardcoded metric value"),
        (r"metric.*=\s*0\.\d{2,}", "hardcoded metric assignment"),
    ]
    for pattern, desc in hardcoded_patterns:
        if re.search(pattern, code):
            warnings.append(f"Possible hardcoded metric: {desc}")

    # Check for trivial computation patterns
    trivial_patterns = [
        ("sum(x**2)", "trivial sum-of-squares computation"),
        ("np.sum(x**2)", "trivial sum-of-squares computation"),
        ("0.3 + idx * 0.03", "formulaic/simulated metric generation"),
    ]
    for pattern, desc in trivial_patterns:
        if pattern in code:
            warnings.append(f"Trivial computation detected: {desc}")

    return warnings

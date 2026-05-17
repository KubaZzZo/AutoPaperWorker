"""Advanced multi-phase code generation agent.

The public ``CodeAgent`` API remains here; large phase implementations live in
focused mixin modules to keep each file navigable.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from researchclaw.pipeline.code_agent_blueprint import CodeAgentBlueprintMixin
from researchclaw.pipeline.code_agent_generation import CodeAgentGenerationMixin
from researchclaw.pipeline.code_agent_models import (
    CodeAgentConfig,
    CodeAgentResult,
    SolutionNode,
    _SandboxLike,
    _SimpleResult,
)
from researchclaw.pipeline.code_agent_search_review import CodeAgentSearchReviewMixin

logger = logging.getLogger(__name__)


class CodeAgent(
    CodeAgentBlueprintMixin,
    CodeAgentGenerationMixin,
    CodeAgentSearchReviewMixin,
):
    """Multi-phase code generation agent."""

    def __init__(
        self,
        llm: Any,
        prompts: Any,
        config: CodeAgentConfig,
        stage_dir: Path,
        sandbox_factory: Any | None = None,
        experiment_config: Any | None = None,
        domain_profile: Any | None = None,
        code_search_result: Any | None = None,
    ) -> None:
        self._llm = llm
        self._pm = prompts
        self._cfg = config
        self._stage_dir = stage_dir
        self._sandbox_factory = sandbox_factory
        self._exp_config = experiment_config
        self._domain_profile = domain_profile
        self._code_search_result = code_search_result
        self._calls = 0
        self._runs = 0
        self._log: list[str] = []
        self._sandbox: _SandboxLike | None = None

    def generate(
        self,
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        max_tokens: int = 8192,
    ) -> CodeAgentResult:
        """Execute all enabled phases and return generated files."""
        t0 = time.time()
        self._log_event("CodeAgent.generate() started")

        # Phase 1: Blueprint planning
        arch_spec = ""
        blueprint = None
        if self._cfg.architecture_planning:
            arch_spec, blueprint = self._phase1_blueprint(
                topic, exp_plan, metric,
            )

        # Phase 2: Code generation
        nodes_explored = 0
        if self._cfg.tree_search_enabled and self._sandbox_factory:
            best, nodes_explored = self._phase3_tree_search(
                topic, exp_plan, metric, pkg_hint, arch_spec, max_tokens,
            )
        elif (
            self._cfg.sequential_generation
            and blueprint is not None
            and self._is_valid_blueprint(blueprint)
        ):
            # Sequential file generation following blueprint
            files = self._phase2_sequential_generate(
                topic, exp_plan, metric, pkg_hint, arch_spec, blueprint,
            )
            # Hard validation gates (E-03)
            if self._cfg.hard_validation:
                files = self._hard_validate_and_repair(
                    files, topic, exp_plan, metric, pkg_hint, arch_spec,
                )
            # Exec-fix loop
            files = self._exec_fix_loop(files)
            best = SolutionNode(
                node_id="sequential", files=files, runs_ok=True, score=1.0,
            )
        else:
            # Fallback: single-shot generation
            if self._cfg.sequential_generation and blueprint is None:
                self._log_event(
                    "  Sequential generation requested but blueprint "
                    "invalid — falling back to single-shot"
                )
            files = self._phase2_generate_and_fix(
                topic, exp_plan, metric, pkg_hint, arch_spec, max_tokens,
            )
            # Hard validation gates (E-03) for single-shot too
            if self._cfg.hard_validation and files:
                files = self._hard_validate_and_repair(
                    files, topic, exp_plan, metric, pkg_hint, arch_spec,
                )
            best = SolutionNode(
                node_id="single", files=files,
                runs_ok=bool(files), score=1.0 if files else 0.0,
            )

        # Phase 5: Review dialog
        review_rounds = 0
        if self._cfg.review_max_rounds > 0:
            best.files, review_rounds = self._phase4_review(
                best.files, topic, exp_plan, metric,
            )

        elapsed = time.time() - t0
        self._log_event(
            f"CodeAgent.generate() done in {elapsed:.1f}s — "
            f"{self._calls} LLM calls, {self._runs} sandbox runs"
        )

        return CodeAgentResult(
            files=best.files,
            architecture_spec=arch_spec,
            validation_log=list(self._log),
            total_llm_calls=self._calls,
            total_sandbox_runs=self._runs,
            best_score=best.score,
            tree_nodes_explored=nodes_explored,
            review_rounds=review_rounds,
        )

    @staticmethod
    def _build_code_summary(
        filename: str, code: str,
    ) -> dict[str, Any]:
        """Build a CodeMem-style compressed summary via AST analysis."""
        summary: dict[str, Any] = {
            "filename": filename,
            "classes": [],
            "functions": [],
            "imports": [],
        }

        try:
            tree = ast.parse(code)
        except SyntaxError:
            summary["parse_error"] = True
            return summary

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for n in node.body:
                    if isinstance(n, ast.FunctionDef):
                        args = [a.arg for a in n.args.args if a.arg != "self"]
                        methods.append({
                            "name": n.name,
                            "args": args,
                        })
                summary["classes"].append({
                    "name": node.name,
                    "bases": [ast.unparse(b) for b in node.bases],
                    "methods": methods,
                })
            elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                args = [a.arg for a in node.args.args]
                summary["functions"].append({
                    "name": node.name,
                    "args": args,
                })
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                try:
                    summary["imports"].append(ast.unparse(node))
                except (AttributeError, ValueError) as exc:
                    logger.debug(
                        "Failed to summarize import node in %s: %s",
                        filename,
                        exc,
                        exc_info=True,
                    )

        return summary

    def _hard_validate_and_repair(
        self,
        files: dict[str, str],
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        arch_spec: str,
    ) -> dict[str, str]:
        """Run AST-based hard validation and repair critical issues.

        Critical issues trigger targeted file regeneration.  Non-critical
        issues are logged as warnings only.
        """
        self._log_event("Phase 2.5: Hard validation gates")

        for attempt in range(self._cfg.hard_validation_max_repairs + 1):
            critical, warnings = self._hard_validate(files)

            # Log warnings
            for w in warnings:
                self._log_event(f"  WARNING: {w}")

            if not critical:
                self._log_event(
                    f"  Hard validation passed "
                    f"({len(warnings)} warning(s), attempt {attempt})"
                )
                return files

            self._log_event(
                f"  Hard validation found {len(critical)} CRITICAL issue(s) "
                f"(attempt {attempt}/{self._cfg.hard_validation_max_repairs})"
            )
            for c in critical:
                self._log_event(f"  CRITICAL: {c}")

            if attempt >= self._cfg.hard_validation_max_repairs:
                self._log_event(
                    "  Max repair attempts reached — proceeding with warnings"
                )
                return files

            # Targeted repair: ask LLM to fix specific critical issues
            files = self._repair_critical_issues(
                files, critical, topic, exp_plan, metric, arch_spec,
            )

        return files

    def _hard_validate(
        self, files: dict[str, str],
    ) -> tuple[list[str], list[str]]:
        """Run AST-based checks and classify as CRITICAL or WARNING.

        Returns (critical_issues, warning_issues).
        """
        critical: list[str] = []
        warnings: list[str] = []

        from researchclaw.experiment.validator import (
            check_api_correctness,
            check_class_quality,
            check_code_complexity,
            check_variable_scoping,
            validate_syntax,
        )

        # 1. Syntax check — always critical
        for fname, code in files.items():
            if not fname.endswith(".py"):
                continue
            syn = validate_syntax(code)
            if not syn.ok:
                for issue in syn.errors:
                    critical.append(
                        f"[{fname}] Syntax error: {issue.message} "
                        f"(line {issue.line})"
                    )

        # 2. Class quality — some are critical
        class_warns = check_class_quality(files)
        for w in class_warns:
            if "identical AST to parent" in w or "NOT a real ablation" in w or "creates nn.Module" in w and "inside forward()" in w:
                critical.append(w)
            elif "empty or trivial subclass" in w:
                # Critical: ablation classes must have real implementations
                critical.append(w)
            else:
                warnings.append(w)

        # 3. Code complexity — hardcoded metrics are critical
        for fname, code in files.items():
            if not fname.endswith(".py"):
                continue
            complexity_warns = check_code_complexity(code)
            for w in complexity_warns:
                if "hardcoded metric" in w.lower() or "trivial computation" in w.lower():
                    critical.append(f"[{fname}] {w}")
                else:
                    warnings.append(f"[{fname}] {w}")

        # 4. API correctness — NameError-causing issues are critical
        for fname, code in files.items():
            if not fname.endswith(".py"):
                continue
            api_warns = check_api_correctness(code, fname)
            for w in api_warns:
                if "NameError" in w or "Import-usage mismatch" in w or "does not exist" in w:
                    critical.append(w)
                else:
                    warnings.append(w)

        # 5. Variable scoping — UnboundLocalError is critical
        for fname, code in files.items():
            if not fname.endswith(".py"):
                continue
            scope_warns = check_variable_scoping(code, fname)
            for w in scope_warns:
                if "UnboundLocalError" in w:
                    critical.append(w)
                else:
                    warnings.append(w)

        # 6. Cross-file import consistency — check local imports resolve
        known_modules = {
            fname.replace(".py", "")
            for fname in files
            if fname.endswith(".py")
        }
        for fname, code in files.items():
            if not fname.endswith(".py"):
                continue
            try:
                tree = ast.parse(code)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod_top = node.module.split(".")[0]
                    # Check if importing from a local module that exists
                    if mod_top in known_modules:
                        # Verify imported names exist in target file
                        target_file = f"{mod_top}.py"
                        if target_file in files and node.names:
                            target_code = files[target_file]
                            try:
                                target_tree = ast.parse(target_code)
                            except SyntaxError:
                                continue
                            exported = set()
                            for tnode in ast.walk(target_tree):
                                if isinstance(tnode, ast.ClassDef) or isinstance(tnode, ast.FunctionDef):
                                    exported.add(tnode.name)
                                elif isinstance(tnode, ast.Assign):
                                    for t in tnode.targets:
                                        if isinstance(t, ast.Name):
                                            exported.add(t.id)
                            for alias in node.names:
                                name = alias.name
                                if name != "*" and name not in exported:
                                    critical.append(
                                        f"[{fname}] ImportError: "
                                        f"'{name}' not defined in "
                                        f"'{target_file}' — will crash"
                                    )

        # 7. BUG-R41-04: main.py MUST have an `if __name__ == "__main__"` block
        #    and must call a training/experiment function — otherwise Docker runs
        #    the file and exits 0 with no output.
        main_code = files.get("main.py", "")
        if main_code:
            try:
                main_tree = ast.parse(main_code)
                has_main_guard = False
                for node in ast.walk(main_tree):
                    if isinstance(node, ast.If):
                        # Check for `if __name__ == "__main__"` pattern
                        test = node.test
                        if isinstance(test, ast.Compare):
                            left = test.left
                            if (
                                isinstance(left, ast.Name)
                                and left.id == "__name__"
                                and len(test.comparators) == 1
                            ):
                                comp = test.comparators[0]
                                if (
                                    isinstance(comp, ast.Constant)
                                    and comp.value == "__main__"
                                ):
                                    has_main_guard = True
                                    break
                if not has_main_guard:
                    critical.append(
                        "[main.py] Missing `if __name__ == \"__main__\":` block — "
                        "script will define functions/classes but never execute "
                        "training. Add a main guard that calls the experiment entry "
                        "point."
                    )
            except SyntaxError as exc:
                logger.debug(
                    "Skipping main guard validation because main.py failed to parse: %s",
                    exc,
                    exc_info=True,
                )

        return critical, warnings

    def _repair_critical_issues(
        self,
        files: dict[str, str],
        critical_issues: list[str],
        topic: str,
        exp_plan: str,
        metric: str,
        arch_spec: str,
    ) -> dict[str, str]:
        """Ask LLM to fix critical validation issues."""
        self._log_event("  Targeted repair for critical issues")

        # Identify which files need repair
        affected_files: set[str] = set()
        for issue in critical_issues:
            # Extract filename from issue string: [filename.py] ...
            m = re.match(r"\[([^\]]+\.py)\]", issue)
            if m:
                affected_files.add(m.group(1))
            else:
                # If no filename found, assume all files affected
                affected_files.update(
                    f for f in files if f.endswith(".py")
                )

        if not affected_files:
            affected_files.update(f for f in files if f.endswith(".py"))

        files_ctx = self._format_files(files)
        issues_text = "\n".join(f"- {issue}" for issue in critical_issues)

        prompt = (
            "Your generated code has CRITICAL issues that will cause "
            "runtime failures or produce invalid results. Fix ALL of them.\n\n"
            "## Critical Issues Found\n"
            f"{issues_text}\n\n"
            "## Architecture Blueprint\n"
            f"{arch_spec[:4000]}\n\n"
            "## Current Code\n"
            f"{files_ctx}\n\n"
            "## Rules\n"
            "1. Fix every critical issue listed above\n"
            "2. Ablation/variant classes MUST have different implementations "
            "from their parent — change the forward() or core method\n"
            "3. Never hardcode metric values — compute them from actual data\n"
            "4. nn.Module layers must be created in __init__(), not forward()\n"
            "5. All cross-file imports must reference names that actually exist\n"
            "6. Output ALL files in ```filename:xxx.py``` format\n"
        )

        sys_prompt = self._pm.system("code_generation")
        resp = self._chat(sys_prompt, prompt, max_tokens=16384)

        fixed = self._extract_files(resp.content)
        if fixed:
            merged = dict(files)
            merged.update(fixed)
            self._log_event(
                f"  Repair updated {len(fixed)} file(s): "
                f"{', '.join(sorted(fixed))}"
            )
            return merged

        self._log_event("  WARNING: Repair produced no extractable files")
        return files

    def _chat(self, system: str, user: str, max_tokens: int = 8192) -> Any:
        """Make an LLM call and track count."""
        self._calls += 1
        messages = [{"role": "user", "content": user}]
        return self._llm.chat(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
        )

    def _get_or_create_sandbox(self) -> _SandboxLike:
        """Lazily create a single sandbox instance for all validation runs."""
        if self._sandbox is None:
            sandbox_dir = self._stage_dir / "agent_sandbox"
            sandbox_dir.mkdir(parents=True, exist_ok=True)
            self._sandbox = self._sandbox_factory(
                self._exp_config, sandbox_dir,
            )
        return self._sandbox

    def _run_in_sandbox(
        self,
        files: dict[str, str],
        timeout_sec: int | None = None,
    ) -> Any:
        """Write files to a temp directory and run in sandbox."""
        if not self._sandbox_factory:
            raise RuntimeError("No sandbox factory configured")

        self._runs += 1
        timeout = timeout_sec or self._cfg.exec_fix_timeout_sec

        # Write files to a numbered attempt directory
        run_dir = self._stage_dir / "agent_runs" / f"attempt_{self._runs:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        for fname, code in files.items():
            fpath = (run_dir / fname).resolve()
            # BUG-CA-10: Prevent path traversal from LLM-generated filenames
            if not fpath.is_relative_to(run_dir.resolve()):
                self._log_event(f"  WARNING: Skipping path-traversal filename: {fname}")
                continue
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(code, encoding="utf-8")

        # Run using the sandbox
        sandbox = self._get_or_create_sandbox()
        try:
            result = sandbox.run_project(run_dir, timeout_sec=timeout)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._log_event(f"  Sandbox run failed: {exc}")
            result = _SimpleResult(
                returncode=1,
                stdout="",
                stderr=f"Sandbox exception: {exc}",
            )

        return result

    def _extract_files(self, content: str) -> dict[str, str]:
        """Extract multi-file code blocks from LLM output."""
        # Local import to avoid circular dependency with executor.py
        from researchclaw.pipeline.executor import _extract_multi_file_blocks

        return _extract_multi_file_blocks(content)

    @staticmethod
    def _format_files(files: dict[str, str]) -> str:
        """Format files for inclusion in a prompt."""
        parts = []
        for fname in sorted(files):
            parts.append(f"```filename:{fname}\n{files[fname]}\n```")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Best-effort JSON extraction from LLM response.

        BUG-17: Always returns ``dict | None`` — never a bare string or list,
        which would cause ``.get()`` crashes in callers.
        """
        def _as_dict(val: Any) -> dict[str, Any] | None:
            return val if isinstance(val, dict) else None

        # Direct parse
        try:
            return _as_dict(json.loads(text))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                "Failed to parse code-agent JSON directly: %s",
                exc,
                exc_info=True,
            )
        # ```json``` fenced block
        m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return _as_dict(json.loads(m.group(1)))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug(
                    "Failed to parse code-agent fenced JSON: %s",
                    exc,
                    exc_info=True,
                )
        # First {...} object (supports up to 2 levels of nesting)
        m = re.search(
            r"\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\}",
            text, re.DOTALL,
        )
        if m:
            try:
                return _as_dict(json.loads(m.group(0)))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug(
                    "Failed to parse code-agent embedded JSON: %s",
                    exc,
                    exc_info=True,
                )
        return None

    def _log_event(self, msg: str) -> None:
        """Log to both Python logger and the internal validation log."""
        logger.info("[CodeAgent] %s", msg)
        self._log.append(msg)

__all__ = [
    "CodeAgent",
    "CodeAgentConfig",
    "CodeAgentResult",
    "SolutionNode",
    "_SandboxLike",
    "_SimpleResult",
]

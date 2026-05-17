"""Generation and repair-loop mixin for CodeAgent."""

from __future__ import annotations

import json
import re
from typing import Any


class CodeAgentGenerationMixin:
    def _phase2_sequential_generate(
        self,
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        arch_spec: str,
        blueprint: dict[str, Any],
    ) -> dict[str, str]:
        """Generate files one-by-one following blueprint dependency order."""
        self._log_event("Phase 2: Sequential generation (blueprint-guided)")

        generated_files: dict[str, str] = {}
        code_memory: dict[str, dict[str, Any]] = {}  # CodeMem summaries

        # Sort files by generation_order
        file_specs = blueprint.get("files", [])
        file_specs = [f for f in file_specs if isinstance(f, dict)]

        # Ensure generation_order exists; default to list position
        for i, fs in enumerate(file_specs):
            if "generation_order" not in fs:
                fs["generation_order"] = i + 1

        file_specs.sort(key=lambda f: f.get("generation_order", 99))

        for file_spec in file_specs:
            file_name = file_spec.get("name", "")
            if not file_name:
                continue

            self._log_event(
                f"  Generating {file_name} "
                f"(order={file_spec.get('generation_order')})"
            )

            # Build dependency context
            deps = file_spec.get("dependencies", [])
            dep_summaries = ""
            dep_code = ""

            for dep in deps:
                if isinstance(dep, str):
                    if dep in code_memory:
                        dep_summaries += (
                            f"\n### {dep} (summary)\n"
                            + json.dumps(code_memory[dep], indent=2)
                            + "\n"
                        )
                    if dep in generated_files:
                        dep_code += (
                            f"\n### {dep}\n```python\n"
                            + generated_files[dep]
                            + "\n```\n"
                        )

            if not dep_summaries:
                dep_summaries = "(no dependencies yet)"
            if not dep_code:
                dep_code = "(no dependencies yet)"

            # Generate this file via LLM
            file_spec_str = json.dumps(file_spec, indent=2, default=str)
            sp = self._pm.sub_prompt(
                "generate_single_file",
                file_name=file_name,
                file_spec=file_spec_str,
                blueprint=arch_spec,
                dependency_summaries=dep_summaries,
                dependency_code=dep_code,
                topic=topic,
                exp_plan=exp_plan[:4000],  # Truncate to avoid token overflow
                pkg_hint=pkg_hint,
            )
            resp = self._chat(sp.system, sp.user, max_tokens=8192)

            # Extract code from response
            code = self._extract_single_file_code(resp.content, file_name)
            if not code:
                self._log_event(f"  WARNING: Empty code for {file_name}")
                continue

            generated_files[file_name] = code

            # Build CodeMem summary via AST
            code_memory[file_name] = self._build_code_summary(
                file_name, code,
            )

            self._log_event(
                f"  {file_name}: {len(code.split(chr(10)))} lines, "
                f"{len(code_memory[file_name].get('classes', []))} classes"
            )

        # Verify we have main.py
        if "main.py" not in generated_files:
            self._log_event("  WARNING: No main.py generated, promoting first file")
            if generated_files:
                first_key = next(iter(generated_files))
                generated_files["main.py"] = generated_files.pop(first_key)

        self._log_event(
            f"  Sequential generation complete: {len(generated_files)} files"
        )
        return generated_files

    @staticmethod
    def _extract_single_file_code(content: str, expected_name: str) -> str:
        """Extract Python code from LLM response for a single file."""
        # Try to extract from ```python``` block
        m = re.search(r"```python\s*\n(.*?)```", content, re.DOTALL)
        if m:
            return m.group(1).strip()

        # Try ```filename:xxx.py block
        m = re.search(
            rf"```(?:filename:)?{re.escape(expected_name)}\s*\n(.*?)```",
            content, re.DOTALL,
        )
        if m:
            return m.group(1).strip()

        # If content looks like raw Python (starts with import/from/# or def)
        stripped = content.strip()
        if stripped and (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped.startswith("#")
            or stripped.startswith("def ")
            or stripped.startswith("class ")
            or stripped.startswith('"""')
        ):
            return stripped

        return ""

    def _phase2_generate_and_fix(
        self,
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        arch_spec: str,
        max_tokens: int,
    ) -> dict[str, str]:
        """Generate code in single shot, then iteratively fix via sandbox."""
        self._log_event("Phase 2: Single-shot generate + exec-fix")

        # Initial generation (uses the existing code_generation prompt)
        files = self._generate_code(
            topic, exp_plan, metric, pkg_hint, arch_spec, max_tokens,
        )
        if not files:
            self._log_event("  WARNING: empty generation, returning fallback")
            return files

        return self._exec_fix_loop(files)

    def _exec_fix_loop(self, files: dict[str, str]) -> dict[str, str]:
        """Run exec-fix loop if sandbox is available."""
        if not self._sandbox_factory or self._cfg.exec_fix_max_iterations <= 0:
            return files

        for i in range(self._cfg.exec_fix_max_iterations):
            result = self._run_in_sandbox(files)
            if result.returncode == 0:
                self._log_event(f"  Exec-fix iter {i}: code runs OK")
                break

            self._log_event(
                f"  Exec-fix iter {i}: crashed (rc={result.returncode}), "
                f"stderr={len(result.stderr or '')} chars"
            )
            files = self._fix_runtime_error(files, result)

        return files

    def _generate_code(
        self,
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        arch_spec: str,
        max_tokens: int,
    ) -> dict[str, str]:
        """Single code generation call with architecture spec injected."""
        # Inject architecture specification into the pkg_hint slot
        hint = pkg_hint
        if arch_spec:
            hint = (
                f"{pkg_hint}\n\n"
                "## ARCHITECTURE SPECIFICATION (follow this file and class structure)\n"
                f"{arch_spec}\n"
            )

        # BUG-004: Inject numerical stability requirements
        hint += (
            "\n\n## NUMERICAL STABILITY (MANDATORY)\n"
            "- Add gradient clipping: `torch.nn.utils.clip_grad_norm_(params, 1.0)`\n"
            "- After each optimizer step, check for NaN loss:\n"
            "  `if torch.isnan(loss): print('FAIL: NaN detected'); break`\n"
            "- When logging metrics, guard against NaN/Inf:\n"
            "  `v = float(val); v = 0.0 if (math.isnan(v) or math.isinf(v)) else v`\n"
            "- For RL: clip rewards to [-10, 10], use reward normalization\n"
        )

        sp = self._pm.for_stage(
            "code_generation",
            topic=topic,
            metric=metric,
            pkg_hint=hint,
            exp_plan=exp_plan,
        )
        resp = self._chat(sp.system, sp.user, max_tokens=max_tokens)

        files = self._extract_files(resp.content)
        if not files and resp.content.strip():
            # Retry with higher token budget
            self._log_event("  Empty extraction, retrying with 32768 tokens")
            resp = self._chat(sp.system, sp.user, max_tokens=32768)
            files = self._extract_files(resp.content)

        return files

    def _fix_runtime_error(
        self, files: dict[str, str], result: Any,
    ) -> dict[str, str]:
        """Fix a runtime error using targeted or full-file repair.

        E-05: Parse the error traceback to identify the failing file and
        line, then send only the affected file with a focused context
        window.  Falls back to full-file repair if parsing fails.
        """
        stderr_tail = (result.stderr or "")[-3000:]
        stdout_tail = "\n".join(
            (result.stdout or "").split("\n")[-50:]
        )

        # Try targeted repair first (E-05)
        error_loc = self._parse_error_location(stderr_tail, files)
        if error_loc:
            fname, lineno, error_msg = error_loc
            self._log_event(
                f"  Targeted repair: {fname}:{lineno} — {error_msg[:80]}"
            )
            fixed = self._targeted_file_repair(
                files, fname, lineno, error_msg, stderr_tail,
            )
            if fixed:
                return fixed

        # Fallback: full-file repair
        files_ctx = self._format_files(files)
        sp = self._pm.sub_prompt(
            "code_exec_fix",
            stderr=stderr_tail or "(empty)",
            stdout_tail=stdout_tail or "(empty)",
            returncode=str(result.returncode),
            files_context=files_ctx,
        )
        resp = self._chat(sp.system, sp.user, max_tokens=16384)

        fixed = self._extract_files(resp.content)
        if fixed:
            merged = dict(files)
            merged.update(fixed)
            return merged
        return files

    @staticmethod
    def _parse_error_location(
        stderr: str, files: dict[str, str],
    ) -> tuple[str, int, str] | None:
        """Parse Python traceback to find failing file and line.

        Returns (filename, line_number, error_message) or None.
        """
        known_files = set(files.keys())
        # Parse traceback lines: File "xxx.py", line NNN
        tb_pattern = re.compile(
            r'File "(?:[^"]*[/\\])?([^"]+\.py)", line (\d+)'
        )
        matches = list(tb_pattern.finditer(stderr))
        if not matches:
            return None

        # Find the last match that references one of our files
        for m in reversed(matches):
            fname = m.group(1)
            lineno = int(m.group(2))
            if fname in known_files:
                # Extract error message (last line of stderr)
                lines = stderr.strip().split("\n")
                error_msg = lines[-1] if lines else "Unknown error"
                return fname, lineno, error_msg

        return None

    def _targeted_file_repair(
        self,
        files: dict[str, str],
        target_file: str,
        error_line: int,
        error_msg: str,
        full_stderr: str,
    ) -> dict[str, str] | None:
        """Repair a single file with focused context around the error."""
        if target_file not in files:
            return None

        code = files[target_file]
        code_lines = code.split("\n")
        total_lines = len(code_lines)

        # Extract context window: ±30 lines around error
        window = 30
        start = max(0, error_line - window - 1)
        end = min(total_lines, error_line + window)
        context_lines = code_lines[start:end]

        # Number the lines for the LLM
        numbered = "\n".join(
            f"{start + i + 1:4d} | {line}"
            for i, line in enumerate(context_lines)
        )

        # Build compact dependency context (summaries only)
        dep_summaries = ""
        for fname, fcode in files.items():
            if fname != target_file and fname.endswith(".py"):
                summary = self._build_code_summary(fname, fcode)
                dep_summaries += (
                    f"\n### {fname}: "
                    f"{len(summary.get('classes', []))} classes, "
                    f"{len(summary.get('functions', []))} functions\n"
                )
                for cls in summary.get("classes", []):
                    methods = ", ".join(
                        m["name"] for m in cls.get("methods", [])
                    )
                    dep_summaries += (
                        f"  class {cls['name']}"
                        f"({', '.join(cls.get('bases', []))})"
                        f": [{methods}]\n"
                    )

        prompt = (
            f"Fix the runtime error in `{target_file}` at line {error_line}.\n\n"
            f"## Error\n```\n{error_msg}\n```\n\n"
            f"## Full Traceback (last 1500 chars)\n"
            f"```\n{full_stderr[-1500:]}\n```\n\n"
            f"## {target_file} (lines {start + 1}-{end})\n"
            f"```python\n{numbered}\n```\n\n"
            f"## Other Files in Project\n{dep_summaries}\n\n"
            f"## Full File ({target_file}, {total_lines} lines)\n"
            f"```python\n{code}\n```\n\n"
            f"Output the COMPLETE fixed `{target_file}` in "
            f"```filename:{target_file}``` format. Fix the root cause, "
            f"not just the symptom."
        )

        sys_prompt = (
            "You are a debugging expert. Fix the specific runtime error "
            "shown. Preserve experiment design and scientific methodology. "
            "Output the COMPLETE fixed file."
        )
        resp = self._chat(sys_prompt, prompt, max_tokens=16384)

        fixed = self._extract_files(resp.content)
        if not fixed:
            # Try extracting as single file
            code_match = re.search(
                r"```(?:python|filename:\S+)\s*\n(.*?)```",
                resp.content, re.DOTALL,
            )
            if code_match:
                fixed = {target_file: code_match.group(1).strip()}

        if fixed and target_file in fixed:
            merged = dict(files)
            merged.update(fixed)
            self._log_event(
                f"  Targeted repair applied to {target_file} "
                f"({len(fixed[target_file].split(chr(10)))} lines)"
            )
            return merged

        return None

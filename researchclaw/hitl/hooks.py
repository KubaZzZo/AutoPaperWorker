"""HITL hook system: extensible pre/post stage hooks.

Allows users to register custom scripts or callables that run
before/after stages, on errors, or on quality drops. Inspired by
Claude Code's hook system and git hooks.

Hook resolution order:
1. Python callables registered via ``register()``
2. Shell scripts in ``{run_dir}/hooks/`` or ``~/.researchclaw/hooks/``
3. Configured in ``config.yaml`` hitl.hooks section

Hook naming convention for shell scripts:
- ``pre_stage_08.sh`` — runs before Stage 8
- ``post_stage_08.sh`` — runs after Stage 8
- ``on_error.sh`` — runs on any stage error
- ``on_pause.sh`` — runs when pipeline pauses for HITL
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Result of a hook execution."""

    hook_name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_sec: float = 0.0


class HookRegistry:
    """Registry for pre/post stage hooks."""

    def __init__(
        self,
        run_dir: Path | None = None,
        global_hooks_dir: Path | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.global_hooks_dir = global_hooks_dir or Path.home() / ".researchclaw" / "hooks"

        self._pre_hooks: dict[int | str, list[Callable]] = {}
        self._post_hooks: dict[int | str, list[Callable]] = {}
        self._error_hooks: list[Callable] = []
        self._pause_hooks: list[Callable] = []

    def register_pre(
        self, stage: int | str, callback: Callable
    ) -> None:
        """Register a pre-stage hook.

        Args:
            stage: Stage number or "*" for all stages.
            callback: Callable that takes (stage_num, stage_name, run_dir).
        """
        self._pre_hooks.setdefault(stage, []).append(callback)

    def register_post(
        self, stage: int | str, callback: Callable
    ) -> None:
        """Register a post-stage hook."""
        self._post_hooks.setdefault(stage, []).append(callback)

    def register_error(self, callback: Callable) -> None:
        """Register an on-error hook."""
        self._error_hooks.append(callback)

    def register_pause(self, callback: Callable) -> None:
        """Register an on-pause hook."""
        self._pause_hooks.append(callback)

    def run_pre_hooks(
        self, stage_num: int, stage_name: str
    ) -> list[HookResult]:
        """Run all pre-stage hooks for a stage."""
        results: list[HookResult] = []

        # Python callables
        for key in (stage_num, "*"):
            for hook in self._pre_hooks.get(key, []):
                results.append(self._run_callable(
                    f"pre_stage_{stage_num}", hook, stage_num, stage_name
                ))

        # Shell scripts
        results.extend(self._run_shell_hooks(
            f"pre_stage_{stage_num:02d}", stage_num, stage_name
        ))

        return results

    def run_post_hooks(
        self,
        stage_num: int,
        stage_name: str,
        status: str = "done",
    ) -> list[HookResult]:
        """Run all post-stage hooks for a stage."""
        results: list[HookResult] = []

        for key in (stage_num, "*"):
            for hook in self._post_hooks.get(key, []):
                results.append(self._run_callable(
                    f"post_stage_{stage_num}", hook,
                    stage_num, stage_name, status
                ))

        results.extend(self._run_shell_hooks(
            f"post_stage_{stage_num:02d}", stage_num, stage_name
        ))

        return results

    def run_error_hooks(
        self, stage_num: int, stage_name: str, error: str
    ) -> list[HookResult]:
        """Run all on-error hooks."""
        results: list[HookResult] = []
        for hook in self._error_hooks:
            results.append(self._run_callable(
                "on_error", hook, stage_num, stage_name, error
            ))
        results.extend(self._run_shell_hooks(
            "on_error", stage_num, stage_name
        ))
        return results

    def run_pause_hooks(
        self, stage_num: int, stage_name: str, reason: str
    ) -> list[HookResult]:
        """Run all on-pause hooks."""
        results: list[HookResult] = []
        for hook in self._pause_hooks:
            results.append(self._run_callable(
                "on_pause", hook, stage_num, stage_name, reason
            ))
        results.extend(self._run_shell_hooks(
            "on_pause", stage_num, stage_name
        ))
        return results

    def _run_callable(
        self, name: str, hook: Callable, *args: Any
    ) -> HookResult:
        """Run a Python callable hook."""
        import time

        t0 = time.monotonic()
        try:
            result = hook(*args)
            return HookResult(
                hook_name=name,
                success=True,
                output=str(result) if result else "",
                duration_sec=time.monotonic() - t0,
            )
        except Exception as exc:
            return HookResult(
                hook_name=name,
                success=False,
                error=str(exc),
                duration_sec=time.monotonic() - t0,
            )

    def _run_shell_hooks(
        self, hook_name: str, stage_num: int, stage_name: str
    ) -> list[HookResult]:
        """Find and run shell script hooks."""
        results: list[HookResult] = []

        # Search in run_dir/hooks/ and global hooks dir
        search_dirs = []
        if self.run_dir:
            search_dirs.append(self.run_dir / "hooks")
        if self.global_hooks_dir:
            search_dirs.append(self.global_hooks_dir)

        for hooks_dir in search_dirs:
            if not hooks_dir.is_dir():
                continue
            for ext in (".sh", ".py", ""):
                script = hooks_dir / f"{hook_name}{ext}"
                if script.is_file() and os.access(script, os.X_OK):
                    results.append(self._run_script(
                        script, hook_name, stage_num, stage_name
                    ))

        return results

    def _run_script(
        self,
        script: Path,
        hook_name: str,
        stage_num: int,
        stage_name: str,
    ) -> HookResult:
        """Execute a shell/Python script hook."""
        import time

        env = os.environ.copy()
        env["RC_STAGE_NUM"] = str(stage_num)
        env["RC_STAGE_NAME"] = stage_name
        env["RC_HOOK_NAME"] = hook_name
        if self.run_dir:
            env["RC_RUN_DIR"] = str(self.run_dir)

        t0 = time.monotonic()
        try:
            cmd = [str(script)]
            if script.suffix == ".sh" and os.name == "nt":
                shell_exe = shutil.which("sh") or shutil.which("bash")
                if shell_exe:
                    cmd = [shell_exe, str(script)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=env,
                cwd=str(self.run_dir) if self.run_dir else None,
            )
            if (
                script.suffix == ".sh"
                and os.name == "nt"
                and result.returncode != 0
                and (
                    "WSL" in result.stderr
                    or "wsl" in result.stderr.lower()
                    or "No such file or directory" in result.stderr
                    or "CreateProcessCommon" in result.stderr
                )
            ):
                result = self._run_simple_windows_shell_script(script, env)
            return HookResult(
                hook_name=f"{hook_name} ({script.name})",
                success=result.returncode == 0,
                output=result.stdout[:1000],
                error=result.stderr[:500] if result.returncode != 0 else "",
                duration_sec=time.monotonic() - t0,
            )
        except subprocess.TimeoutExpired:
            return HookResult(
                hook_name=f"{hook_name} ({script.name})",
                success=False,
                error="Hook timed out (30s)",
                duration_sec=time.monotonic() - t0,
            )
        except Exception as exc:
            return HookResult(
                hook_name=f"{hook_name} ({script.name})",
                success=False,
                error=str(exc),
                duration_sec=time.monotonic() - t0,
            )

    def _run_simple_windows_shell_script(self, script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        """Run a tiny safe subset of .sh hooks when no POSIX shell is available."""
        output: list[str] = []
        for raw_line in script.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("echo "):
                text = line[5:].strip()
                if (
                    len(text) >= 2
                    and text[0] == text[-1]
                    and text[0] in ("'", '"')
                ):
                    text = text[1:-1]
                output.append(os.path.expandvars(text))
                continue
            return subprocess.CompletedProcess(
                args=[str(script)],
                returncode=127,
                stdout="\n".join(output) + ("\n" if output else ""),
                stderr=f"No POSIX shell available for unsupported command: {line}",
            )
        return subprocess.CompletedProcess(
            args=[str(script)],
            returncode=0,
            stdout="\n".join(output) + ("\n" if output else ""),
            stderr="",
        )

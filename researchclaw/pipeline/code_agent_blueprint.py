"""Blueprint-planning mixin for CodeAgent."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("researchclaw.pipeline.code_agent")


class CodeAgentBlueprintMixin:
    def _phase1_blueprint(
        self, topic: str, exp_plan: str, metric: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Generate a deep implementation blueprint.

        Returns (raw_yaml_str, parsed_blueprint_dict_or_None).
        """
        self._log_event("Phase 1: Blueprint planning")

        sp = self._pm.sub_prompt(
            "architecture_planning",
            topic=topic,
            exp_plan=exp_plan,
            metric=metric,
        )

        # Inject domain context and code search results into blueprint prompt
        domain_context = self._build_domain_context()
        if domain_context:
            sp = type(sp)(
                system=sp.system,
                user=sp.user + "\n\n" + domain_context,
            )
            self._log_event("  Injected domain context into blueprint prompt")

        resp = self._chat(sp.system, sp.user, max_tokens=8192)

        # Extract YAML block from response
        arch_spec = resp.content
        yaml_match = re.search(r"```ya?ml\s*\n(.*?)```", arch_spec, re.DOTALL)
        if yaml_match:
            arch_spec = yaml_match.group(1).strip()

        self._log_event(f"  Blueprint spec: {len(arch_spec)} chars")

        # Parse YAML into structured blueprint
        blueprint = self._parse_blueprint(arch_spec)
        if blueprint:
            n_files = len(blueprint.get("files", []))
            self._log_event(f"  Parsed blueprint: {n_files} files")
        else:
            self._log_event("  WARNING: Could not parse blueprint YAML")

        return arch_spec, blueprint

    def _build_domain_context(self) -> str:
        """Build domain-specific context for injection into prompts.

        Includes:
        - Domain profile hints (file structure, libraries, evaluation)
        - Code search results (API patterns, reference code)
        """
        parts: list[str] = []

        # Domain profile context
        if self._domain_profile is not None:
            try:
                from researchclaw.domains.prompt_adapter import get_adapter
                adapter = get_adapter(self._domain_profile)
                blueprint_ctx = adapter.get_blueprint_context()
                if blueprint_ctx:
                    parts.append(
                        "# Domain-Specific Guidance\n" + blueprint_ctx
                    )
            except (ImportError, RuntimeError, TypeError, ValueError):
                logger.debug("Failed to get domain context", exc_info=True)

        # Code search results
        if self._code_search_result is not None:
            try:
                prompt_ctx = self._code_search_result.to_prompt_context()
                if prompt_ctx:
                    parts.append(
                        "# Reference Code from GitHub\n"
                        "The following patterns were found in relevant open-source projects. "
                        "Use them as reference for API usage and project structure.\n\n"
                        + prompt_ctx
                    )
            except (RuntimeError, TypeError, ValueError):
                logger.debug("Failed to get code search context", exc_info=True)

        return "\n\n".join(parts)

    def _parse_blueprint(self, yaml_text: str) -> dict[str, Any] | None:
        """Parse blueprint YAML into a structured dict.

        BUG-178: LLM often includes Python type annotations in signature
        values (e.g. ``signature: (self, name: str) -> Config``).  The
        bare ``:`` breaks YAML parsing.  We quote unquoted signature
        values before parsing.
        """
        # Pre-process: sanitize values that contain Python type annotations,
        # unclosed quotes, or other patterns that break YAML parsing.
        import re as _bp_re

        import yaml
        sanitized_lines = []
        for line in yaml_text.split("\n"):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                sanitized_lines.append(line)
                continue

            # Skip lines that are pure list markers or block scalars
            if stripped.startswith(("- ", "---", "...")):
                # For list items like `- key: value`, extract after `- `
                if stripped.startswith("- ") and ":" in stripped[2:]:
                    inner = stripped[2:]
                else:
                    sanitized_lines.append(line)
                    continue
            elif ":" in stripped:
                inner = stripped
            else:
                sanitized_lines.append(line)
                continue

            # Find the YAML key separator (first `:` followed by space or EOL)
            m = _bp_re.search(r":\s", inner)
            if not m:
                sanitized_lines.append(line)
                continue

            val_part = inner[m.end():].strip()
            if not val_part:
                sanitized_lines.append(line)
                continue

            # Already properly quoted — skip
            if val_part.startswith(("'", "|", ">")):
                sanitized_lines.append(line)
                continue

            # Check if value needs quoting:
            # 1) Contains `:` or `->` (type annotations)
            # 2) Starts with `"` but doesn't end with `"` (unclosed quote)
            # 3) Contains `[` with `:` (e.g. dict[str, float])
            needs_quoting = False
            if val_part.startswith('"'):
                # Already quoted — check if properly closed
                if not val_part.endswith('"') or val_part.count('"') % 2 != 0:
                    needs_quoting = True  # unclosed or malformed quote
                else:
                    sanitized_lines.append(line)
                    continue
            elif ":" in val_part or "->" in val_part:
                needs_quoting = True

            if needs_quoting:
                # Strip any existing partial quotes, escape internal quotes
                clean = val_part.strip('"').replace('"', '\\"')
                # Remove inline comments (# ...) to avoid YAML issues
                comment_idx = clean.find("  #")
                if comment_idx >= 0:
                    clean = clean[:comment_idx].rstrip()
                indent = line[:len(line) - len(stripped)]
                prefix = stripped[:len(stripped) - len(inner)]  # e.g. "- "
                key_sep = inner[:m.end()]
                sanitized_lines.append(
                    f'{indent}{prefix}{key_sep}"{clean}"'
                )
            else:
                sanitized_lines.append(line)
        sanitized = "\n".join(sanitized_lines)

        for attempt_text in (sanitized, yaml_text):
            try:
                data = yaml.safe_load(attempt_text)
                if isinstance(data, dict) and "files" in data:
                    return data
            except (yaml.YAMLError, TypeError, ValueError) as exc:
                self._log_event(f"  Blueprint YAML parse error: {exc}")
        return None

    @staticmethod
    def _is_valid_blueprint(blueprint: dict[str, Any]) -> bool:
        """Check if a blueprint has the minimum required structure."""
        files = blueprint.get("files", [])
        if not files or not isinstance(files, list):
            return False
        # Need at least 2 files with generation_order
        has_order = sum(
            1 for f in files
            if isinstance(f, dict) and "generation_order" in f
        )
        return has_order >= 2

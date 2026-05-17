"""CodeGen Agent — generates visualization code for each figure.

Takes the Planner's figure specifications and experiment data, then
generates either:
  - Standalone Python scripts (Matplotlib/Seaborn) — run by Renderer
  - LaTeX code (TikZ/PGFPlots) — embedded directly in the paper

Architecture follows Visual ChatGPT (Wu et al., 2023): the LLM acts
as a *controller* calling deterministic render tools instead of
generating pixels directly.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from researchclaw.agents.base import AgentStepResult, BaseAgent
from researchclaw.agents.figure_agent.schema import normalize_figure_spec, normalize_figure_specs
from researchclaw.agents.figure_agent.style_config import get_style_preamble
from researchclaw.agents.figure_agent.codegen_templates import (
    _LATEX_TEMPLATES,
    _TEMPLATES,
    _esc,
    _humanize_label,
    _is_degenerate_data,
)
from researchclaw.utils.sanitize import sanitize_figure_id
from researchclaw.utils.thinking_tags import strip_thinking_tags

logger = logging.getLogger(__name__)




# ---------------------------------------------------------------------------
# Degenerate data detection
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Metric name humanization
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Built-in chart templates
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# LaTeX / PGFPlots templates — for direct LaTeX embedding
# ---------------------------------------------------------------------------






class CodeGenAgent(BaseAgent):
    """Generates visualization code (Python or LaTeX) for each planned figure.

    Supports two output formats:
      - ``"python"`` (default): Matplotlib/Seaborn scripts executed by Renderer
      - ``"latex"``: TikZ/PGFPlots code embedded directly in the paper
    """

    name = "figure_codegen"

    def __init__(self, llm: Any, *, output_format: str = "python", use_docker: bool = False) -> None:
        super().__init__(llm)
        self._output_format = output_format  # "python" or "latex"
        self._use_docker = use_docker  # BUG-60: generate Docker paths when True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, context: dict[str, Any]) -> AgentStepResult:
        """Generate plotting scripts for all planned figures.

        Context keys:
            figures (list[dict]): Figure plan from Planner
            experiment_results (dict): Raw experiment data
            condition_summaries (dict): Per-condition aggregated stats
            metrics_summary (dict): Per-metric aggregated stats
            metric_key (str): Primary metric name
            output_dir (str): Directory for output scripts
            critic_feedback (list[dict], optional): Previous Critic feedback
        """
        try:
            figures = normalize_figure_specs(context.get("figures", []))
            experiment_results = context.get("experiment_results", {})
            condition_summaries = context.get("condition_summaries", {})
            metrics_summary = context.get("metrics_summary", {})
            metric_key = context.get("metric_key", "primary_metric")
            output_dir = context.get("output_dir", "charts")
            critic_feedback = context.get("critic_feedback", [])

            scripts: list[dict[str, Any]] = []

            for fig_spec in figures:
                figure_id = fig_spec.get("figure_id", "unknown")
                chart_type = fig_spec.get("chart_type", "bar_comparison")

                # Check for critic feedback on this specific figure
                fig_feedback = None
                for fb in critic_feedback:
                    # BUG-FIX: guard against non-dict entries in feedback
                    if isinstance(fb, dict) and fb.get("figure_id") == figure_id:
                        fig_feedback = fb
                        break

                script = self._generate_script(
                    fig_spec=fig_spec,
                    chart_type=chart_type,
                    condition_summaries=condition_summaries,
                    metrics_summary=metrics_summary,
                    experiment_results=experiment_results,
                    metric_key=metric_key,
                    output_dir=output_dir,
                    critic_feedback=fig_feedback,
                )

                scripts.append({
                    "figure_id": figure_id,
                    "chart_type": chart_type,
                    "script": script,
                    "output_filename": f"{figure_id}.png",
                    "title": fig_spec.get("title", ""),
                    "caption": fig_spec.get("caption", ""),
                    "section": fig_spec.get("section", "results"),
                    "width": fig_spec.get("width", "single_column"),
                })

            return self._make_result(True, data={"scripts": scripts})
        except Exception as exc:
            self.logger.error("CodeGen failed: %s", exc)
            return self._make_result(False, error=str(exc))

    # ------------------------------------------------------------------
    # Script generation
    # ------------------------------------------------------------------

    def _generate_script(
        self,
        *,
        fig_spec: dict[str, Any],
        chart_type: str,
        condition_summaries: dict[str, Any],
        metrics_summary: dict[str, Any],
        experiment_results: dict[str, Any],
        metric_key: str,
        output_dir: str,
        critic_feedback: dict[str, Any] | None,
    ) -> str:
        """Generate a plotting script for a single figure."""
        fig_spec = normalize_figure_spec(fig_spec) or {}
        figure_id = sanitize_figure_id(fig_spec.get("figure_id", "figure"))
        # BUG-20: Use absolute path to avoid CWD-relative savefig errors
        # BUG-60: When running in Docker, use container path directly so
        # renderer doesn't need fragile regex rewriting of host paths.
        if self._use_docker:
            output_path = f"/workspace/output/{figure_id}.png"
        else:
            output_path = str((Path(output_dir) / f"{figure_id}.png").resolve())
        title = fig_spec.get("title", "")
        x_label = fig_spec.get("x_label", "")
        y_label = fig_spec.get("y_label", "")
        width_key = fig_spec.get("width", "single_column")
        # BUG-FIX: LLM may return data_source as a plain string (e.g.
        # "condition_comparison") instead of a dict.  Normalize to dict.
        _raw_ds = fig_spec.get("data_source", {})
        if isinstance(_raw_ds, str):
            data_source = {"type": _raw_ds}
        elif isinstance(_raw_ds, dict):
            data_source = _raw_ds
        else:
            data_source = {}

        from researchclaw.agents.figure_agent.style_config import (
            DEFAULT_FIGURE_HEIGHT,
            FIGURE_WIDTH,
        )
        width = FIGURE_WIDTH.get(width_key, FIGURE_WIDTH["single_column"])
        height = DEFAULT_FIGURE_HEIGHT

        # Try template-based generation first
        template = _TEMPLATES.get(chart_type)
        if template and not critic_feedback:
            script = self._fill_template(
                template=template,
                chart_type=chart_type,
                data_source=data_source,
                condition_summaries=condition_summaries,
                metrics_summary=metrics_summary,
                experiment_results=experiment_results,
                metric_key=metric_key,
                output_path=output_path,
                title=title,
                x_label=x_label,
                y_label=y_label,
                width=width,
                height=height,
                width_key=width_key,
            )
            if script:
                return script

        # Fall back to LLM-generated script
        return self._llm_generate_script(
            fig_spec=fig_spec,
            chart_type=chart_type,
            condition_summaries=condition_summaries,
            metrics_summary=metrics_summary,
            experiment_results=experiment_results,
            metric_key=metric_key,
            output_path=output_path,
            width=width,
            height=height,
            critic_feedback=critic_feedback,
            width_key=width_key,
        )

    def _fill_template(
        self,
        *,
        template: str,
        chart_type: str,
        data_source: dict[str, Any],
        condition_summaries: dict[str, Any],
        metrics_summary: dict[str, Any],
        experiment_results: dict[str, Any],
        metric_key: str,
        output_path: str,
        title: str,
        x_label: str,
        y_label: str,
        width: float,
        height: float,
        width_key: str = "single_column",
    ) -> str:
        """Fill a template with actual data values."""
        style_preamble = get_style_preamble(width_key=width_key)
        source_type = data_source.get("type", "condition_comparison")

        if chart_type in ("bar_comparison", "ablation_grouped"):
            return self._fill_bar_template(
                template=template,
                condition_summaries=condition_summaries,
                metric_key=data_source.get("metric", metric_key),
                output_path=output_path,
                title=title,
                x_label=x_label,
                y_label=y_label,
                width=width,
                height=height,
                style_preamble=style_preamble,
            )

        if chart_type == "grouped_bar" and source_type == "multi_metric":
            # BUG-37: LLM may return nested lists in metrics — flatten to list[str]
            _raw_metrics = data_source.get("metrics", [])
            _flat_metrics: list[str] = []
            for _mi in (_raw_metrics if isinstance(_raw_metrics, list) else []):
                if isinstance(_mi, str):
                    _flat_metrics.append(_mi)
                elif isinstance(_mi, list):
                    _flat_metrics.extend(str(x) for x in _mi)
                else:
                    _flat_metrics.append(str(_mi))
            return self._fill_grouped_bar_template(
                template=template,
                condition_summaries=condition_summaries,
                metrics=_flat_metrics,
                output_path=output_path,
                title=title,
                x_label=x_label,
                y_label=y_label,
                width=width,
                height=height,
                style_preamble=style_preamble,
            )

        if chart_type in ("heatmap", "confusion_matrix"):
            return self._fill_heatmap_template(
                template=template,
                condition_summaries=condition_summaries,
                metrics_summary=metrics_summary,
                output_path=output_path,
                title=title,
                x_label=x_label,
                y_label=y_label,
                width=width,
                height=height,
                style_preamble=style_preamble,
            )

        # For other types, fall through to LLM generation
        return ""

    def _fill_bar_template(
        self,
        *,
        template: str,
        condition_summaries: dict[str, Any],
        metric_key: str,
        output_path: str,
        title: str,
        x_label: str,
        y_label: str,
        width: float,
        height: float,
        style_preamble: str,
    ) -> str:
        """Fill bar comparison template with condition data."""
        conditions: list[str] = []
        values: list[float] = []
        ci_low: list[float] = []
        ci_high: list[float] = []

        for cond, cdata in condition_summaries.items():
            if not isinstance(cdata, dict):
                continue
            metrics = cdata.get("metrics", {})
            val = metrics.get(f"{metric_key}_mean") or metrics.get(metric_key)
            if val is None:
                continue
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue

            conditions.append(cond)
            values.append(fval)
            ci_low.append(float(cdata.get("ci95_low", fval)))
            ci_high.append(float(cdata.get("ci95_high", fval)))

        if not conditions:
            return ""

        # Skip degenerate data (all zeros, all identical)
        if _is_degenerate_data(values):
            logger.warning("Skipping degenerate bar chart: all values are identical or zero")
            return ""

        # Humanize empty/raw labels
        if not y_label or y_label.lower().replace("_", "") in ("primarymetric", "metric"):
            y_label = _humanize_label(metric_key)
        if not x_label:
            x_label = "Method"

        return template.format(
            style_preamble=style_preamble,
            conditions=repr(conditions),
            values=repr(values),
            ci_low=repr(ci_low),
            ci_high=repr(ci_high),
            output_path=output_path,
            title=_esc(title),
            x_label=_esc(x_label),
            y_label=_esc(y_label),
            width=width,
            height=height,
        )

    def _fill_grouped_bar_template(
        self,
        *,
        template: str,
        condition_summaries: dict[str, Any],
        metrics: list[str],
        output_path: str,
        title: str,
        x_label: str,
        y_label: str,
        width: float,
        height: float,
        style_preamble: str,
    ) -> str:
        """Fill grouped bar template with multi-metric data."""
        conditions: list[str] = list(condition_summaries.keys())
        if not conditions or not metrics:
            return ""

        data_matrix: list[list[float]] = []
        for cond in conditions:
            cdata = condition_summaries.get(cond, {})
            cmetrics = cdata.get("metrics", {}) if isinstance(cdata, dict) else {}
            row = []
            for m in metrics:
                val = cmetrics.get(f"{m}_mean") or cmetrics.get(m, 0)
                try:
                    row.append(float(val))
                except (ValueError, TypeError):
                    row.append(0.0)
            data_matrix.append(row)

        return template.format(
            style_preamble=style_preamble,
            conditions=repr(conditions),
            metric_names=repr(metrics),
            data_matrix=repr(data_matrix),
            output_path=output_path,
            title=_esc(title),
            x_label=_esc(x_label),
            y_label=_esc(y_label),
            width=width,
            height=height,
        )

    def _fill_heatmap_template(
        self,
        *,
        template: str,
        condition_summaries: dict[str, Any],
        metrics_summary: dict[str, Any],
        output_path: str,
        title: str,
        x_label: str,
        y_label: str,
        width: float,
        height: float,
        style_preamble: str,
    ) -> str:
        """Fill heatmap template — rows=conditions, cols=metrics."""
        conditions = list(condition_summaries.keys())
        # Select non-timing metrics
        metric_names = [
            m for m in metrics_summary
            if not any(t in m.lower() for t in ["time", "elapsed", "seed", "runtime"])
        ][:8]

        if not conditions or not metric_names:
            return ""

        data_matrix: list[list[float]] = []
        for cond in conditions:
            cdata = condition_summaries.get(cond, {})
            cmetrics = cdata.get("metrics", {}) if isinstance(cdata, dict) else {}
            row = []
            for m in metric_names:
                val = cmetrics.get(f"{m}_mean") or cmetrics.get(m, 0)
                try:
                    row.append(round(float(val), 4))
                except (ValueError, TypeError):
                    row.append(0.0)
            data_matrix.append(row)

        # Skip degenerate heatmaps (all values identical)
        all_vals = [v for row in data_matrix for v in row]
        if _is_degenerate_data(all_vals):
            logger.warning("Skipping degenerate heatmap: all values are identical or zero")
            return ""

        # Also skip single-row heatmaps (meaningless)
        if len(conditions) < 2:
            logger.warning("Skipping heatmap with only %d row(s)", len(conditions))
            return ""

        return template.format(
            style_preamble=style_preamble,
            row_labels=repr(conditions),
            col_labels=repr(metric_names),
            data_matrix=repr(data_matrix),
            output_path=output_path,
            title=_esc(title),
            x_label=_esc(x_label or "Metric"),
            y_label=_esc(y_label or "Method"),
            width=max(width, len(metric_names) * 0.8),
            height=max(height, len(conditions) * 0.6),
        )

    # ------------------------------------------------------------------
    # LLM-based script generation
    # ------------------------------------------------------------------

    def _llm_generate_script(
        self,
        *,
        fig_spec: dict[str, Any],
        chart_type: str,
        condition_summaries: dict[str, Any],
        metrics_summary: dict[str, Any],
        experiment_results: dict[str, Any],
        metric_key: str,
        output_path: str,
        width: float,
        height: float,
        critic_feedback: dict[str, Any] | None,
        width_key: str = "single_column",
    ) -> str:
        """Generate a plotting script using LLM."""
        if self._output_format == "latex":
            return self._llm_generate_latex(
                fig_spec=fig_spec,
                chart_type=chart_type,
                condition_summaries=condition_summaries,
                metrics_summary=metrics_summary,
                metric_key=metric_key,
                width=width,
                height=height,
                critic_feedback=critic_feedback,
            )

        style_preamble = get_style_preamble(width_key=width_key)

        system_prompt = (
            "You are an expert scientific visualization programmer. "
            "Generate a standalone Python script that creates a publication-quality "
            "matplotlib chart.\n\n"
            "RULES:\n"
            "- The script must be completely self-contained (no external imports "
            "beyond matplotlib, numpy, seaborn)\n"
            "- All data values must be hardcoded in the script (no file I/O)\n"
            "- Use the provided style preamble at the top of the script\n"
            "- Output format: PNG at 300 DPI\n"
            "- Use colorblind-safe colors from the COLORS list\n"
            "- Include descriptive axis labels and title\n"
            "- Use constrained_layout=True in plt.subplots() — do NOT call fig.tight_layout()\n"
            "- Call fig.savefig() and plt.close(fig) at the end\n"
            "- Print 'Saved: <path>' after saving\n"
            "- NEVER embed caption, description, or subtitle text inside the figure "
            "using fig.text() or ax.text() for long descriptions. "
            "All captions are added by LaTeX \\caption{}\n"
            "- Place legends OUTSIDE the data area when possible. "
            "Use bbox_to_anchor=(1.02, 1) with loc='upper left' for legends "
            "that would overlap bars or data points\n"
            "- Do NOT include any <think> or </think> tags\n\n"
            "Return ONLY the Python script, no explanation."
        )

        # Build data context (truncated to avoid token overflow)
        data_context = {
            "conditions": list(condition_summaries.keys())[:10],
            "metric_key": metric_key,
        }
        # Add condition values
        for cond, cdata in list(condition_summaries.items())[:10]:
            if isinstance(cdata, dict):
                data_context[cond] = {
                    "metrics": {k: v for k, v in (cdata.get("metrics") or {}).items()
                                if not any(t in k.lower()
                                           for t in ["time", "elapsed", "runtime"])},
                    "ci95_low": cdata.get("ci95_low"),
                    "ci95_high": cdata.get("ci95_high"),
                }

        user_prompt = (
            f"Style preamble (paste at top of script):\n```python\n{style_preamble}\n```\n\n"
            f"Figure specification:\n{json.dumps(fig_spec, indent=2)}\n\n"
            f"Experiment data:\n{json.dumps(data_context, indent=2, default=str)}\n\n"
            f"Output path: {output_path}\n"
            f"Figure size: ({width}, {height})\n"
        )

        if critic_feedback:
            user_prompt += (
                f"\n\nPREVIOUS ATTEMPT FAILED REVIEW. Fix these issues:\n"
                f"{json.dumps(critic_feedback.get('issues', []), indent=2)}\n"
            )

        raw = self._chat(system_prompt, user_prompt, max_tokens=4096, temperature=0.3)

        # Strip reasoning model thinking tags before parsing
        raw = strip_thinking_tags(raw)

        # Strip markdown fences
        script = self._strip_fences(raw)

        # Ensure style preamble is present
        if "matplotlib" not in script:
            script = style_preamble + "\n\n" + script

        return script

    def _llm_generate_latex(
        self,
        *,
        fig_spec: dict[str, Any],
        chart_type: str,
        condition_summaries: dict[str, Any],
        metrics_summary: dict[str, Any],
        metric_key: str,
        width: float,
        height: float,
        critic_feedback: dict[str, Any] | None,
    ) -> str:
        """Generate LaTeX TikZ/PGFPlots code for a figure.

        This produces code that compiles directly in a LaTeX document that
        includes ``\\usepackage{pgfplots}`` and ``\\usepackage{tikz}``.
        """
        system_prompt = (
            "You are an expert scientific visualization programmer specializing "
            "in LaTeX/TikZ/PGFPlots.\n\n"
            "Generate LaTeX code using PGFPlots that creates a publication-quality "
            "chart suitable for a top-tier AI conference paper.\n\n"
            "RULES:\n"
            "- Use pgfplots (version ≥ 1.18) with \\pgfplotsset{compat=1.18}\n"
            "- All data values must be hardcoded in the LaTeX source\n"
            "- Use the colorbrewer palette or viridis colormap\n"
            "- Include descriptive axis labels and title\n"
            "- Wrap in a figure environment with \\caption and \\label\n"
            "- Font sizes should match: title 12pt, labels 10pt, ticks 9pt\n"
            "- Width should be \\columnwidth or 0.48\\textwidth for single column\n"
            "- Do NOT include any <think> or </think> tags\n\n"
            "Return ONLY the LaTeX code, no explanation."
        )

        # Build data context
        data_context = {
            "conditions": list(condition_summaries.keys())[:10],
            "metric_key": metric_key,
        }
        for cond, cdata in list(condition_summaries.items())[:10]:
            if isinstance(cdata, dict):
                data_context[cond] = {
                    "metrics": {k: v for k, v in (cdata.get("metrics") or {}).items()
                                if not any(t in k.lower()
                                           for t in ["time", "elapsed", "runtime"])},
                }

        user_prompt = (
            f"Chart type: {chart_type}\n"
            f"Figure specification:\n{json.dumps(fig_spec, indent=2)}\n\n"
            f"Experiment data:\n{json.dumps(data_context, indent=2, default=str)}\n\n"
            f"Figure dimensions: width={width}in, height={height}in\n"
        )

        if critic_feedback:
            user_prompt += (
                f"\n\nPREVIOUS ATTEMPT FAILED REVIEW. Fix these issues:\n"
                f"{json.dumps(critic_feedback.get('issues', []), indent=2)}\n"
            )

        raw = self._chat(system_prompt, user_prompt, max_tokens=4096, temperature=0.3)

        # Strip reasoning model thinking tags before parsing
        raw = strip_thinking_tags(raw)

        # Strip markdown fences (```latex ... ```)
        return self._strip_latex_fences(raw)

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove markdown code fences from LLM output."""
        m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text.strip()

    @staticmethod
    def _strip_latex_fences(text: str) -> str:
        """Remove markdown code fences from LaTeX LLM output."""
        m = re.search(r"```(?:latex|tex)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text.strip()

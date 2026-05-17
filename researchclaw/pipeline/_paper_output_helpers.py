"""Paper-output helper functions used by the pipeline facade."""

from __future__ import annotations

import logging
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable

from researchclaw.config import RCConfig
from researchclaw.llm.client import LLMClient
from researchclaw.prompts import PromptManager

logger = logging.getLogger("researchclaw.pipeline._helpers")

def _generate_neurips_checklist(
    has_experiments: bool = True,
    has_theory: bool = False,
    has_code: bool = True,
) -> str:
    """Generate a NeurIPS-style paper checklist appendix in markdown.

    This checklist is based on the NeurIPS 2025 submission requirements.
    It is appended to the paper before LaTeX conversion.
    """
    items = [
        ("Claims", "Do the main claims accurately reflect the paper's contributions and scope?", "Yes"),
        ("Limitations", "Does the paper discuss limitations of the work?", "Yes"),
    ]
    if has_theory:
        items.append(
            ("Theory", "Are all assumptions stated and proofs included?", "Yes")
        )
    items.extend([
        ("Experiments reproducibility", "Does the paper fully disclose experimental settings?", "Yes" if has_experiments else "NA"),
        ("Code and data", "Is code or data provided for reproducibility?", "Yes" if has_code else "No"),
        ("Experimental details", "Are training details and hyperparameters specified?", "Yes" if has_experiments else "NA"),
        ("Error bars", "Are error bars or confidence intervals reported?", "Yes" if has_experiments else "NA"),
        ("Compute resources", "Are compute requirements documented?", "Yes" if has_experiments else "NA"),
        ("Code of ethics", "Does the work comply with the code of ethics?", "Yes"),
        ("Broader impacts", "Are potential negative societal impacts discussed?", "Yes"),
        ("Licenses", "Are licenses for used assets respected?", "Yes"),
        ("New assets", "Are newly released assets documented?", "NA"),
        ("Human subjects", "Were IRB approvals obtained if applicable?", "NA"),
    ])

    lines = [
        "## NeurIPS Paper Checklist",
        "",
    ]
    for label, question, answer in items:
        lines.append(f"**{label}**: {question}")
        lines.append(f"Answer: [{answer}]")
        lines.append("")

    return "\n".join(lines)


def _extract_paper_title(md_text: str) -> str:
    """Extract paper title from markdown text for LaTeX generation.

    Prioritises H1 headings that appear *before* the abstract section and
    look like real titles (>= 4 words, starts with uppercase).  This avoids
    picking up pseudocode comments or algorithm step labels.

    Also handles the common LLM pattern where a ``# Title`` heading is
    followed by the actual title as a plain text line (possibly bold):

        # Title

        NORM-PPO: Observation Normalization and Reward Scaling Effects
    """
    import re as _re

    # Strip outer markdown fence (LLMs sometimes wrap entire paper)
    _text = md_text
    _fence_m = _re.match(r"^\s*```(?:markdown|md|latex|tex)?\s*\n", _text)
    if _fence_m:
        _text = _text[_fence_m.end():]
        # Also strip trailing fence
        _text = _re.sub(r"\n\s*```\s*$", "", _text)

    # Limit search to content before Abstract heading
    abstract_pos = _re.search(
        r"^#{1,2}\s+(Abstract|ABSTRACT)", _text, _re.MULTILINE
    )
    search_region = _text[: abstract_pos.start()] if abstract_pos else _text[:3000]

    _SKIP = {"title", "abstract", "references", "appendix"}
    candidates: list[str] = []
    _saw_title_heading = False

    lines = search_region.splitlines()
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()

        # BUG-171: When we see a "# Title" or "## Title" heading, the actual
        # title is often on the next non-empty line as plain text or bold text.
        if _saw_title_heading and line:
            # Strip bold markers: **Title Text** → Title Text
            candidate = _re.sub(r"\*\*(.+?)\*\*", r"\1", line).strip()
            # Make sure it's not another heading or a skip heading
            if not line.startswith("#") and candidate:
                candidates.insert(0, candidate)  # highest priority
            _saw_title_heading = False

        # Match H1 or H2 headings
        hm = _re.match(r"^(#{1,2})\s+(.+)$", line)
        if hm:
            heading = hm.group(2).strip()
            heading_lower = heading.lower()
            # Handle "## Title Actual Paper Title" pattern
            if heading_lower.startswith("title ") and len(heading) > 6:
                heading = heading[6:].strip()
                heading_lower = heading.lower()
            if heading_lower in _SKIP:
                # Mark that we saw a "# Title" heading — next non-empty line
                # is likely the actual title text
                if heading_lower == "title":
                    _saw_title_heading = True
                continue
            candidates.append(heading)
            continue
        # Bold title line (e.g. **My Paper Title**)
        m = _re.match(r"\*\*(.+?)\*\*$", line)
        if m and len(m.group(1).split()) >= 3:
            candidates.append(m.group(1))

    # Prefer candidates that look like real titles (>= 4 words, capitalised)
    for c in candidates:
        words = c.split()
        if len(words) >= 4 and c[0].isupper():
            return c

    # Fallback: any candidate
    if candidates:
        return candidates[0]

    return "Untitled Paper"


def _generate_framework_diagram_prompt(
    paper_text: str,
    config: RCConfig,
    *,
    llm: LLMClient | None = None,
    chat_with_prompt: Callable[..., Any] | None = None,
) -> str:
    """Generate a text-to-image prompt for a methodology framework diagram.

    Reads the paper's method section and produces a detailed prompt suitable
    for AI image generators (DALL-E, Midjourney, etc.).  The prompt describes
    an academic-style architecture/framework overview figure.

    Returns the prompt as a Markdown string, or empty string on failure.
    """
    import re as _re

    # Extract method/approach section from paper
    _method_section = ""
    _method_patterns = [
        r"(?:^#{1,3}\s+(?:Method(?:ology)?|Approach|Proposed\s+(?:Method|Framework|Approach)|Our\s+Method|Technical\s+Approach|Model\s+Architecture).*?)(?=^#{1,3}\s+|\Z)",
    ]
    for _pat in _method_patterns:
        _match = _re.search(_pat, paper_text, _re.MULTILINE | _re.DOTALL | _re.IGNORECASE)
        if _match:
            _method_section = _match.group(0)[:3000]
            break

    if not _method_section:
        # Fallback: use abstract + first 1500 chars
        _abs_match = _re.search(
            r"(?:^#{1,2}\s+Abstract\s*\n)(.*?)(?=^#{1,2}\s+|\Z)",
            paper_text, _re.MULTILINE | _re.DOTALL | _re.IGNORECASE,
        )
        _method_section = (_abs_match.group(1)[:1500] if _abs_match else paper_text[:2000])

    title = _extract_paper_title(paper_text)
    topic = config.research.topic

    # Use LLM to generate the prompt if available
    if llm is not None:
        _system = (
            "You are an expert academic figure designer. Generate a detailed text-to-image "
            "prompt for creating a methodology framework/architecture overview diagram.\n\n"
            "Requirements:\n"
            "- Academic style: clean, professional, suitable for a top-tier ML conference paper\n"
            "- Color palette: sophisticated and harmonious (suggest specific hex colors, "
            "prefer muted blues #4477AA, teals #44AA99, warm accents #CCBB44, soft purples #AA3377)\n"
            "- Layout: left-to-right or top-to-bottom data flow, with clearly labeled components\n"
            "- Components: boxes/modules with rounded corners, directional arrows, clear labels\n"
            "- Information density: high but not cluttered — each box should have a short label\n"
            "- Text on figure: minimal, only component names and key annotations\n"
            "- Background: white or very light grey\n"
            "- Style: vector-art look, flat design with subtle shadows, NO photorealism\n\n"
            "Output ONLY the prompt text (no markdown headers, no explanations). "
            "The prompt should be 150-300 words, highly specific and actionable."
        )
        _user = (
            f"Paper title: {title}\n"
            f"Research topic: {topic}\n\n"
            f"Method section excerpt:\n{_method_section}\n\n"
            "Generate a detailed text-to-image prompt for the methodology framework diagram."
        )
        try:
            if chat_with_prompt is None:
                return ""
            resp = chat_with_prompt(llm, _system, _user, max_tokens=1024)
            _llm_prompt = resp.content.strip()
            if len(_llm_prompt) > 50:
                return (
                    f"# Framework Diagram Prompt\n\n"
                    f"**Paper**: {title}\n\n"
                    f"## Image Generation Prompt\n\n"
                    f"{_llm_prompt}\n\n"
                    f"## Usage Instructions\n\n"
                    f"1. Copy the prompt above into an AI image generator "
                    f"(DALL-E 3, Midjourney, Ideogram, etc.)\n"
                    f"2. Generate the image at high resolution (2048x1024 or similar landscape)\n"
                    f"3. Save as `framework_diagram.png` in the same `charts/` folder\n"
                    f"4. Insert into the paper's Method section using:\n"
                    f"   - LaTeX: `\\includegraphics[width=\\textwidth]{{charts/framework_diagram.png}}`\n"
                    f"   - Markdown: `![Framework Overview](charts/framework_diagram.png)`\n"
                )
        except (
            RuntimeError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
            urllib.error.URLError,
        ):
            logger.debug("Framework prompt LLM generation failed, using template")

    # Fallback: template-based prompt without LLM
    _components = []
    _component_patterns = [
        (r"(?:encoder|decoder|transformer|attention|convolution|MLP|GNN|ResNet|ViT)", "Neural Network Module"),
        (r"(?:loss|objective|criterion|training|optimization)", "Training/Optimization"),
        (r"(?:data|dataset|input|preprocessing|augmentation)", "Data Pipeline"),
        (r"(?:output|prediction|inference|evaluation)", "Output/Evaluation"),
    ]
    _method_lower = _method_section.lower()
    for pat, label in _component_patterns:
        if _re.search(pat, _method_lower):
            _components.append(label)

    if not _components:
        _components = ["Input Processing", "Core Model", "Training Loop", "Evaluation"]

    return (
        f"# Framework Diagram Prompt\n\n"
        f"**Paper**: {title}\n\n"
        f"## Image Generation Prompt\n\n"
        f"Create a clean, academic-style methodology framework diagram for a research paper "
        f"titled \"{title}\". "
        f"The diagram should show a left-to-right data flow pipeline with these main components: "
        f"{', '.join(_components)}. "
        f"Use a professional color palette with muted blues (#4477AA), teals (#44AA99), "
        f"warm yellows (#CCBB44), and soft purples (#AA3377) on a white background. "
        f"Each component should be a rounded rectangle with a short label inside. "
        f"Connect components with clean directional arrows. "
        f"Add subtle shadows for depth. Flat vector-art style, no photorealism. "
        f"High information density but visually clean. "
        f"Suitable for a top-tier machine learning conference paper (ICML/NeurIPS/ICLR). "
        f"Landscape orientation, 2048x1024 resolution.\n\n"
        f"## Usage Instructions\n\n"
        f"1. Copy the prompt above into an AI image generator "
        f"(DALL-E 3, Midjourney, Ideogram, etc.)\n"
        f"2. Generate the image at high resolution (2048x1024 or similar landscape)\n"
        f"3. Save as `framework_diagram.png` in the same `charts/` folder\n"
        f"4. Insert into the paper's Method section using:\n"
        f"   - LaTeX: `\\includegraphics[width=\\textwidth]{{charts/framework_diagram.png}}`\n"
        f"   - Markdown: `![Framework Overview](charts/framework_diagram.png)`\n"
    )


def _safe_filename(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)
    return name[:100] or "unnamed"


def _default_hypotheses(topic: str, utcnow_iso: Callable[[], str]) -> str:
    return f"""# Hypotheses

## H1
Increasing protocol control for {topic} improves metric stability across random seeds.

## H2
Adding robustness-aware objectives for {topic} improves out-of-domain performance without major in-domain regression.

## H3
The combined approach outperforms either component under fixed compute budget.

## Generated
{utcnow_iso()}
"""


def _default_paper_outline(topic: str, utcnow_iso: Callable[[], str]) -> str:
    return f"""# Paper Outline

## 1. Title
Focused title on {topic}

## 2. Abstract
- Problem framing
- Method overview
- Key quantitative result

## 3. Introduction
- Motivation
- Gap statement
- Contributions

## 4. Related Work
- Method families
- Evaluation practices

## 5. Method
- Problem setup
- Model/algorithm
- Complexity and constraints

## 6. Experiments
- Datasets and metrics
- Baselines and ablations
- Reproducibility protocol

## 7. Results
- Main table
- Robustness analysis
- Failure cases

## 8. Discussion
- Practical implications
- Limitations

## 9. Conclusion
- Findings and next steps

Generated: {utcnow_iso()}
"""


def _default_quality_report(threshold: float, utcnow_iso: Callable[[], str]) -> dict[str, Any]:
    # When LLM fails, return below-threshold score to force revision
    score = max(1.0, float(threshold) - 2.0) if threshold > 0 else 5.0
    score = max(1.0, min(10.0, score))
    verdict = "revise"
    return {
        "score_1_to_10": round(score, 2),
        "verdict": verdict,
        "criteria": {
            "novelty": round(min(10.0, score + 0.3), 2),
            "methodological_rigor": round(score, 2),
            "clarity": round(max(1.0, score - 0.2), 2),
            "reproducibility": round(min(10.0, score + 0.1), 2),
        },
        "strengths": [
            "Stage-by-stage evidence chain preserved",
            "Experiment artifacts are generated and archived",
        ],
        "weaknesses": [
            "Statistical significance may need stronger reporting",
            "Broader external validity remains partially evaluated",
        ],
        "required_actions": [
            "Report confidence intervals and seed variance",
            "Include at least one stronger external baseline",
        ],
        "generated": utcnow_iso(),
    }


def _multi_perspective_generate(
    llm: LLMClient,
    roles: dict[str, dict[str, str]],
    variables: dict[str, str],
    perspectives_dir: Path,
) -> dict[str, str]:
    """Generate outputs from multiple debate perspectives.

    Each role has its own system/user prompt. Outputs are saved to
    *perspectives_dir* and returned as ``{role_name: response_text}``.
    """
    from researchclaw.prompts import _render  # noqa: PLC0415

    perspectives_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for role_name, role_prompts in roles.items():
        try:
            system = _render(role_prompts["system"], variables)
            user = _render(role_prompts["user"], variables)
            resp = llm.chat(
                [{"role": "user", "content": user}],
                system=system,
            )
            results[role_name] = resp.content
            (perspectives_dir / f"{role_name}.md").write_text(
                resp.content, encoding="utf-8"
            )
            logger.info("Debate perspective '%s' generated (%d chars)", role_name, len(resp.content))
        except (
            RuntimeError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
            urllib.error.URLError,
        ) as exc:
            logger.warning("Debate perspective '%s' failed: %s", role_name, exc, exc_info=True)
    if len(results) < 2:
        logger.error("Multi-perspective debate: only %d/%d roles succeeded", len(results), len(roles))
    return results


def _synthesize_perspectives(
    llm: LLMClient,
    perspectives: dict[str, str],
    sub_prompt_name: str,
    prompts: PromptManager,
) -> str:
    """Synthesize multiple perspective outputs into a unified result."""
    parts = []
    for role_name, text in perspectives.items():
        parts.append(f"### Perspective: {role_name}\n{text}")
    combined = "\n\n---\n\n".join(parts)
    sp = prompts.sub_prompt(sub_prompt_name, perspectives=combined)
    resp = llm.chat(
        [{"role": "user", "content": sp.user}],
        system=sp.system,
    )
    return resp.content


def reconcile_figure_refs(
    tex_path: Path,
    charts_dir: Path,
) -> dict[str, str]:
    """Fix ``\\includegraphics`` paths in *tex_path* that don't match files in *charts_dir*.

    Three-tier matching strategy:
      1. **Exact stem** — e.g. ``accuracy_plot`` matches ``accuracy_plot.png``
      2. **Normalized keyword overlap** — tokenize on ``[-_]``, apply singular/plural
         normalization, require Jaccard similarity >= 0.4
      3. **Substring containment** — one stem is a substring of the other

    Returns a ``{old_path: new_path}`` dict of fixes applied (empty if none needed).
    """
    if not tex_path.exists():
        return {}

    tex_text = tex_path.read_text(encoding="utf-8")
    fig_refs = re.findall(
        r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex_text
    )
    if not fig_refs:
        return {}

    # Build map of actual chart files: lowered-stem -> charts/filename
    actual_files: dict[str, str] = {}
    if charts_dir.is_dir():
        for af in charts_dir.iterdir():
            if af.is_file() and af.suffix.lower() in (
                ".png", ".jpg", ".jpeg", ".pdf", ".svg",
            ):
                actual_files[af.stem.lower()] = f"charts/{af.name}"

    if not actual_files:
        return {}

    def _singularize(word: str) -> str:
        """Cheap singular/plural normalization."""
        if word.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"
        if word.endswith("ses") and len(word) > 4:
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
            return word[:-1]
        return word

    def _tokenize(stem: str) -> set[str]:
        return {_singularize(w) for w in stem.replace("-", "_").split("_") if w}

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    fixes: dict[str, str] = {}
    for ref in fig_refs:
        ref_resolved = tex_path.parent / ref
        if ref_resolved.exists():
            continue

        ref_stem = Path(ref).stem.lower()

        # Tier 1: exact stem match
        if ref_stem in actual_files:
            fixes[ref] = actual_files[ref_stem]
            continue

        # Tier 2: keyword overlap with Jaccard >= 0.4
        ref_tokens = _tokenize(ref_stem)
        best_match, best_score = "", 0.0
        for stem, apath in actual_files.items():
            score = _jaccard(ref_tokens, _tokenize(stem))
            if score > best_score:
                best_score = score
                best_match = apath
        if best_score >= 0.4 and best_match:
            fixes[ref] = best_match
            continue

        # Tier 3: substring containment
        for stem, apath in actual_files.items():
            if ref_stem in stem or stem in ref_stem:
                fixes[ref] = apath
                break

    if fixes:
        for old_path, new_path in fixes.items():
            tex_text = tex_text.replace(f"{{{old_path}}}", f"{{{new_path}}}")
        tex_path.write_text(tex_text, encoding="utf-8")
        logger.warning(
            "reconcile_figure_refs: Fixed %d figure path mismatch(es): %s",
            len(fixes),
            ", ".join(f"{k} → {v}" for k, v in fixes.items()),
        )

    return fixes


__all__ = [
    "_default_hypotheses",
    "_default_paper_outline",
    "_default_quality_report",
    "_extract_paper_title",
    "_generate_framework_diagram_prompt",
    "_generate_neurips_checklist",
    "_multi_perspective_generate",
    "_safe_filename",
    "_synthesize_perspectives",
    "reconcile_figure_refs",
]

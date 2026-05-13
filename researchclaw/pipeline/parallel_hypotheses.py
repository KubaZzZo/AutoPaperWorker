"""Branch planning for parallel hypothesis exploration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from researchclaw.config import ParallelHypothesesConfig


_NUMBERED_RE = re.compile(r"^\s*\d+[\.)]\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_HEADING_RE = re.compile(r"^\s*#{2,6}\s+(?:H\d+[:.)-]?\s*)?(.+?)\s*$", re.I)


def extract_hypothesis_candidates(text: str) -> list[str]:
    """Extract candidate hypotheses from simple Markdown lists or headings."""
    candidates: list[str] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line:
            continue

        numbered = _NUMBERED_RE.match(line)
        bullet = _BULLET_RE.match(line)
        heading = _HEADING_RE.match(line)
        candidate = ""
        if numbered:
            candidate = numbered.group(1)
        elif bullet:
            candidate = bullet.group(1)
        elif heading:
            heading_text = heading.group(1).strip()
            body_lines: list[str] = []
            while idx < len(lines):
                next_line = lines[idx].strip()
                if not next_line:
                    idx += 1
                    if body_lines:
                        break
                    continue
                if _HEADING_RE.match(next_line):
                    break
                body_lines.append(next_line)
                idx += 1
            candidate = " ".join(body_lines) or heading_text

        cleaned = _clean_candidate(candidate)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    return candidates


def build_hypothesis_branch_plan(
    hypotheses_markdown: str,
    config: ParallelHypothesesConfig,
) -> dict[str, Any]:
    """Build a serializable plan for branch-level hypothesis exploration."""
    if not config.enabled:
        return {
            "enabled": False,
            "selection_metric": config.selection_metric,
            "branches": [],
        }

    candidates = extract_hypothesis_candidates(hypotheses_markdown)
    selected = candidates[: max(1, config.max_branches)]
    branches = [
        {
            "branch_id": f"hypothesis-{rank:02d}",
            "rank": rank,
            "hypothesis": hypothesis,
            "status": "planned",
        }
        for rank, hypothesis in enumerate(selected, start=1)
    ]
    return {
        "enabled": True,
        "selection_metric": config.selection_metric,
        "branches": branches,
    }


def write_hypothesis_branch_plan(
    stage_dir: Path,
    hypotheses_markdown: str,
    config: ParallelHypothesesConfig,
) -> Path:
    """Write ``hypothesis_branches.json`` and return its path."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    plan = build_hypothesis_branch_plan(hypotheses_markdown, config)
    output = stage_dir / "hypothesis_branches.json"
    output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return output


def _clean_candidate(text: str) -> str:
    text = re.sub(r"^\*\*(?:hypothesis|h\d+)[:.)-]?\*\*\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:hypothesis|h\d+)[:.)-]\s*", "", text, flags=re.I)
    return " ".join(text.strip().split())

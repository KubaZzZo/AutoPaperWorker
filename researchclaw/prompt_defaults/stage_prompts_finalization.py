"""Default prompt templates for a focused group of pipeline stages."""

from __future__ import annotations

from typing import Any

_FINALIZATION_STAGES: dict[str, dict[str, Any]] = {
    "knowledge_archive": {
        "system": "You produce reproducibility-focused research retrospectives.",
        "user": (
            "{preamble}\n\n"
            "Write retrospective archive markdown with lessons, "
            "reproducibility notes, and future work.\n"
            "Decision:\n{decision}\n\nAnalysis:\n{analysis}\n\n"
            "Revised paper:\n{revised}"
        ),
        "max_tokens": 8192,
    },
    "export_publish": {
        "system": "You are a publication formatting editor.",
        "user": (
            "Format revised paper into clean final markdown for publication "
            "export.\n"
            "Preserve content quality and readability.\n"
            "CITATION FORMAT (CRITICAL): All citations MUST remain in [cite_key] bracket "
            "format, e.g. [smith2024transformer]. Do NOT convert to author-year "
            "format like [Smith et al., 2024]. The [cite_key] format is required "
            "for downstream LaTeX \\cite{{}} generation.\n"
            "Input paper:\n{revised}"
        ),
        "max_tokens": 16384,
    },
}

__all__ = ["_FINALIZATION_STAGES"]

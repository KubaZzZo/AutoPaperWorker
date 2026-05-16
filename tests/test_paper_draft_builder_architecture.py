"""Architecture checks for Stage 17 paper draft construction."""

from __future__ import annotations

import inspect


def test_execute_paper_draft_delegates_to_builder() -> None:
    from researchclaw.pipeline.stage_impls import _paper_writing

    assert hasattr(_paper_writing, "PaperDraftBuilder")
    assert _paper_writing._execute_paper_draft.__name__ == "_execute_paper_draft"
    source = inspect.getsource(_paper_writing._execute_paper_draft)
    assert "PaperDraftBuilder" in source
    assert len(source.splitlines()) <= 25


def test_paper_draft_builder_exposes_section_methods() -> None:
    from researchclaw.pipeline.stage_impls._paper_writing import PaperDraftBuilder

    expected_methods = {
        "build_abstract_section",
        "build_introduction_section",
        "build_method_section",
        "build_experiments_section",
        "build_conclusion_section",
    }

    for name in expected_methods:
        attr = getattr(PaperDraftBuilder, name)
        assert callable(attr)


def test_paper_draft_builder_section_methods_extract_markdown_sections() -> None:
    from pathlib import Path
    from unittest.mock import Mock

    from researchclaw.pipeline.stage_impls._paper_writing import PaperDraftBuilder

    builder = PaperDraftBuilder(
        Path("stage-17"),
        Path("run"),
        Mock(),
        Mock(),
    )
    draft = "\n".join(
        [
            "# Title",
            "## Abstract",
            "Abstract text.",
            "## Introduction",
            "Intro text.",
            "## Method",
            "Method text.",
            "## Experiments",
            "Experiment text.",
            "## Conclusion",
            "Conclusion text.",
        ]
    )

    assert builder.build_abstract_section(draft) == "## Abstract\nAbstract text."
    assert builder.build_introduction_section(draft) == "## Introduction\nIntro text."
    assert builder.build_method_section(draft) == "## Method\nMethod text."
    assert builder.build_experiments_section(draft) == "## Experiments\nExperiment text."
    assert builder.build_conclusion_section(draft) == "## Conclusion\nConclusion text."

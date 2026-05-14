# pyright: basic, reportMissingImports=false
"""Tests for shared stop-word constants."""

from __future__ import annotations


def test_pipeline_and_novelty_use_shared_stop_words() -> None:
    from researchclaw.literature import novelty
    from researchclaw.pipeline import _helpers
    from researchclaw.utils.text import BASE_STOP_WORDS, NOVELTY_STOP_WORDS

    assert _helpers._STOP_WORDS is BASE_STOP_WORDS
    assert novelty._STOP_WORDS is NOVELTY_STOP_WORDS
    assert BASE_STOP_WORDS < NOVELTY_STOP_WORDS


def test_novelty_extra_stop_words_preserve_behavior() -> None:
    from researchclaw.literature.novelty import _extract_keywords

    keywords = _extract_keywords("show results performance evaluation attention")

    assert keywords == ["attention"]

from __future__ import annotations

from researchclaw.templates.converter import (
    _convert_inline as legacy_convert_inline,
    _escape_latex as legacy_escape_latex,
)
from researchclaw.templates.inline import _convert_inline, _escape_latex


def test_inline_converter_module_matches_legacy_exports() -> None:
    text = r"**Result**: RawObs\_PPO reached $x^2$ with [ref](https://example.com/a_b)."

    assert _convert_inline(text) == legacy_convert_inline(text)
    assert _escape_latex(r"value \(x_1\) & more") == legacy_escape_latex(
        r"value \(x_1\) & more"
    )


def test_inline_converter_handles_unicode_and_citations() -> None:
    result = _convert_inline("α improves reward [smith2024rl] by ≥ 5%")

    assert r"$\alpha$" in result
    assert r"\cite{smith2024rl}" in result
    assert r"$\geq$" in result
    assert r"5\%" in result


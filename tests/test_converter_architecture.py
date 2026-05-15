from __future__ import annotations

from researchclaw.templates.converter import (
    _collect_list as legacy_collect_list,
    _escape_algo_line as legacy_escape_algo_line,
    _convert_inline as legacy_convert_inline,
    _escape_latex as legacy_escape_latex,
    _render_figure as legacy_render_figure,
    _parse_alignments as legacy_parse_alignments,
    _parse_table_row as legacy_parse_table_row,
    _reset_render_counters,
    _render_code_block as legacy_render_code_block,
    _render_enumerate as legacy_render_enumerate,
    _render_itemize as legacy_render_itemize,
    _render_table as legacy_render_table,
    check_paper_completeness as legacy_check_paper_completeness,
)
from researchclaw.templates.codeblocks import _escape_algo_line, _render_code_block
from researchclaw.templates.completeness import check_paper_completeness
from researchclaw.templates.figures import _render_figure
from researchclaw.templates.inline import _convert_inline, _escape_latex
from researchclaw.templates.lists import (
    _collect_list,
    _render_enumerate,
    _render_itemize,
)
from researchclaw.templates.tables import (
    _parse_alignments,
    _parse_table_row,
    _render_table,
)


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


def test_table_module_matches_legacy_exports() -> None:
    table = [
        "| Method | Accuracy | Notes |",
        "| --- | ---: | :--- |",
        "| Baseline | 0.81 | short |",
        "| Proposed | 0.91 | better |",
    ]

    assert _parse_table_row(table[0]) == legacy_parse_table_row(table[0])
    assert _parse_alignments(table[1], 3) == legacy_parse_alignments(table[1], 3)
    _reset_render_counters()
    legacy_result = legacy_render_table(table)

    next_table = iter([1]).__next__
    assert _render_table(
        table,
        inline_converter=_convert_inline,
        next_table_num=next_table,
    ) == legacy_result


def test_codeblock_module_matches_legacy_exports() -> None:
    code = "Input: x_y\nFor each item # update\nReturn x_y"

    assert _escape_algo_line("x_y # update") == legacy_escape_algo_line(
        "x_y # update"
    )
    assert _render_code_block(
        "algorithm",
        code,
        inline_converter=_convert_inline,
    ) == legacy_render_code_block(
        "algorithm", code
    )


def test_completeness_module_matches_legacy_export() -> None:
    section = type(
        "_Section",
        (),
        {
            "level": 1,
            "heading": "Method",
            "heading_lower": "method",
            "body": "short body",
        },
    )()

    assert check_paper_completeness([section]) == legacy_check_paper_completeness(
        [section]
    )


def test_figure_module_matches_legacy_export() -> None:
    _reset_render_counters()
    legacy_result = legacy_render_figure("Result Plot", "charts/result plot.png")

    next_figure = iter([1]).__next__
    assert _render_figure(
        "Result Plot",
        "charts/result plot.png",
        inline_converter=_convert_inline,
        next_figure_num=next_figure,
    ) == legacy_result


def test_list_module_matches_legacy_exports() -> None:
    import re

    lines = [
        "- First **item**",
        "",
        "- Second item",
        "  continued",
        "Paragraph",
    ]
    pattern = re.compile(r"^(\s*)[-*+]\s+(.*)$")

    assert _collect_list(lines, 0, pattern) == legacy_collect_list(lines, 0, pattern)
    assert _render_itemize(
        ["First **item**"],
        inline_converter=_convert_inline,
    ) == legacy_render_itemize(["First **item**"])
    assert _render_enumerate(
        ["Step _one_"],
        inline_converter=_convert_inline,
    ) == legacy_render_enumerate(["Step _one_"])

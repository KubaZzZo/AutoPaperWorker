"""Markdown table rendering helpers for the LaTeX converter."""

from __future__ import annotations

import re
from collections.abc import Callable

_LATEX_ROW_END = r" \\"


def _collect_table(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect table lines (header + separator + body rows)."""
    table: list[str] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        table.append(lines[i])
        i += 1
    return table, i


def _render_table(
    table_lines: list[str],
    caption: str = "",
    *,
    inline_converter: Callable[[str], str],
    next_table_num: Callable[[], int],
) -> str:
    """Render a Markdown table as a LaTeX tabular inside a table environment.

    IMP-23: Auto-wraps in ``\resizebox`` when columns > 5 or any cell
    text exceeds 25 characters, preventing overflow in conference formats.
    IMP-32: Generates descriptive captions from header columns when the
    caption is empty or just 'Table N'.
    """
    if len(table_lines) < 2:
        return ""

    header = _parse_table_row(table_lines[0])
    body_rows = [_parse_table_row(line) for line in table_lines[2:] if line.strip()]
    ncols = len(header)

    alignments = _parse_alignments(table_lines[1], ncols)
    col_spec = "".join(alignments)

    table_num = next_table_num()

    max_cell_len = max(
        (len(c) for row in [header] + body_rows for c in row),
        default=0,
    )
    needs_resize = ncols > 5 or max_cell_len > 25

    lines_out: list[str] = []
    lines_out.append("\\begin{table}[ht]")
    lines_out.append("\\centering")

    if caption:
        cap_text = re.sub(r"^Table\s+\d+[.:]\s*", "", caption).strip()
        if cap_text:
            lines_out.append(f"\\caption{{{inline_converter(cap_text)}}}")
        else:
            auto_cap = _auto_table_caption(header, table_num, inline_converter=inline_converter)
            lines_out.append(f"\\caption{{{auto_cap}}}")
    else:
        auto_cap = _auto_table_caption(header, table_num, inline_converter=inline_converter)
        lines_out.append(f"\\caption{{{auto_cap}}}")
    lines_out.append(f"\\label{{tab:{table_num}}}")

    if needs_resize:
        lines_out.append("\\resizebox{\\columnwidth}{!}{%")
    lines_out.append(f"\\begin{{tabular}}{{{col_spec}}}")
    lines_out.append("\\toprule")
    lines_out.append(
        " & ".join(f"\\textbf{{{inline_converter(c)}}}" for c in header)
        + _LATEX_ROW_END
    )
    lines_out.append("\\midrule")
    for row in body_rows:
        padded = row + [""] * (ncols - len(row))
        lines_out.append(
            " & ".join(inline_converter(c) for c in padded[:ncols])
            + _LATEX_ROW_END
        )
    lines_out.append("\\bottomrule")
    lines_out.append("\\end{tabular}")
    if needs_resize:
        lines_out.append("}")
    lines_out.append("\\end{table}")

    return "\n".join(lines_out)


def _auto_table_caption(
    header: list[str],
    table_num: int,
    *,
    inline_converter: Callable[[str], str],
) -> str:
    """IMP-32: Generate a descriptive caption from table header columns."""
    if len(header) <= 1:
        return f"Table {table_num}"
    cols = [c.strip() for c in header if c.strip()]
    if len(cols) < 2:
        return f"Table {table_num}"
    col0 = cols[0].lower()
    rest = [inline_converter(c) for c in cols[1:min(5, len(cols))]]
    _HP_HINTS = {"hyperparameter", "parameter", "param", "hp", "setting", "config"}
    _ABL_HINTS = {"component", "variant", "ablation", "configuration", "module"}
    _MODEL_HINTS = {"model", "method", "approach", "algorithm", "baseline"}
    if any(h in col0 for h in _HP_HINTS):
        return "Hyperparameter settings"
    if any(h in col0 for h in _ABL_HINTS):
        return f"Ablation study results across {', '.join(rest)}"
    if any(h in col0 for h in _MODEL_HINTS):
        return f"Performance comparison of different methods on {', '.join(rest)}"
    return f"Comparison of {inline_converter(cols[0])} across {', '.join(rest)}"


def _parse_table_row(line: str) -> list[str]:
    """Parse ``| a | b | c |`` into ``['a', 'b', 'c']``."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _parse_alignments(sep_line: str, ncols: int) -> list[str]:
    """Parse alignment indicators from separator line."""
    cells = _parse_table_row(sep_line)
    aligns: list[str] = []
    for cell in cells:
        raw = cell.strip()
        left = raw.startswith(":")
        right = raw.endswith(":")
        if left and right:
            aligns.append("c")
        elif right:
            aligns.append("r")
        else:
            aligns.append("l")
    while len(aligns) < ncols:
        aligns.append("l")
    return aligns[:ncols]

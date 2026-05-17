"""Figure rendering helpers for Markdown-to-LaTeX conversion."""

from __future__ import annotations

import re
from collections.abc import Callable

InlineConverter = Callable[[str], str]
FigureCounter = Callable[[], int]


def _render_figure(
    caption: str,
    path: str,
    *,
    inline_converter: InlineConverter,
    next_figure_num: FigureCounter,
) -> str:
    """Render a markdown image as a LaTeX figure environment."""
    fig_num = next_figure_num()
    path = path.replace(" ", "_")
    cap_tex = inline_converter(caption) if caption else f"Figure {fig_num}"
    label_key = re.sub(r"[^a-z0-9]+", "_", caption.lower()).strip("_")[:30]
    if not label_key:
        label_key = str(fig_num)
    return (
        "\\begin{figure}[t]\n"
        "\\centering\n"
        f"\\includegraphics[width=0.95\\columnwidth]{{{path}}}\n"
        f"\\caption{{{cap_tex}}}\n"
        f"\\label{{fig:{label_key}}}\n"
        "\\end{figure}"
    )

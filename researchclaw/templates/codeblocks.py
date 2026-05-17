"""Code block rendering helpers for the LaTeX converter."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

_UNICODE_TO_ASCII: dict[str, str] = {
    "\u2190": "<-",   "\u2192": "->",   "\u21d0": "<=",   "\u21d2": "=>",
    "\u2264": "<=",   "\u2265": ">=",   "\u2260": "!=",   "\u2248": "~=",
    "\u2208": " in ", "\u2209": " not in ",
    "\u2200": "forall ", "\u2203": "exists ",
    "\u2207": "nabla", "\u221e": "inf",  "\u00b1": "+/-",
    "\u00d7": "x",    "\u00b7": "*",    "\u2026": "...",
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma",
    "\u03b4": "delta", "\u03b5": "epsilon", "\u03b6": "zeta",
    "\u03b7": "eta",   "\u03b8": "theta", "\u03b9": "iota",
    "\u03ba": "kappa", "\u03bb": "lambda", "\u03bc": "mu",
    "\u03bd": "nu",    "\u03be": "xi",    "\u03c0": "pi",
    "\u03c1": "rho",   "\u03c3": "sigma",  "\u03c4": "tau",
    "\u03c5": "upsilon", "\u03c6": "phi", "\u03c7": "chi",
    "\u03c8": "psi",   "\u03c9": "omega",
    "\u0394": "Delta", "\u0398": "Theta", "\u039b": "Lambda",
    "\u03a3": "Sigma", "\u03a6": "Phi",   "\u03a8": "Psi",
    "\u03a9": "Omega",
    "\u2113": "ell",   "\u2202": "d",     "\u222b": "int",
}


_ALGO_KEYWORDS = re.compile(
    r"\b(Input|Output|Return|While|For|If|Else|Repeat|Until|Function|Procedure|Algorithm)\b",
    re.IGNORECASE,
)


def _escape_algo_line(line: str) -> str:
    """Escape LaTeX special characters in an algorithmic pseudocode line."""
    _comment_match = re.search(r"(?<=\s)#\s*(.+)$", line)
    comment_suffix = ""
    if _comment_match:
        comment_text = _comment_match.group(1).strip()
        line = line[: _comment_match.start()].rstrip()
        comment_suffix = f" \\COMMENT{{{comment_text}}}"
    elif line.strip().startswith("#"):
        comment_text = line.strip().lstrip("#").strip()
        return f"\\COMMENT{{{comment_text}}}"

    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        idx = len(protected)
        protected.append(m.group(0))
        return f"\x00ALG{idx}\x00"

    line = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", _protect, line)
    line = re.sub(r"\$[^$]+\$", _protect, line)
    line = re.sub(r"\\\(.+?\\\)", _protect, line)

    line = line.replace("&", "\\&")
    line = line.replace("%", "\\%")
    line = line.replace("#", "\\#")
    line = line.replace("_", "\\_")
    line = line.replace("{", "\\{")
    line = line.replace("}", "\\}")
    line = line.replace("~", "\\textasciitilde{}")
    line = line.replace("^", "\\textasciicircum{}")

    for idx, val in enumerate(protected):
        line = line.replace(f"\x00ALG{idx}\x00", val)

    return line + comment_suffix


def _render_code_block(
    lang: str,
    code: str,
    *,
    inline_converter: Callable[[str], str],
) -> str:
    """Render a fenced code block as a LaTeX environment."""
    escaped = code.rstrip("\n")
    for uni, ascii_eq in _UNICODE_TO_ASCII.items():
        escaped = escaped.replace(uni, ascii_eq)
    escaped = "".join(c for c in escaped if not unicodedata.combining(c))

    lang_lower = lang.lower().strip()
    is_algo = lang_lower in ("algorithm", "pseudocode", "algo")
    if not is_algo:
        is_algo = len(_ALGO_KEYWORDS.findall(escaped)) >= 3

    if is_algo:
        algo_lines = escaped.split("\n")
        caption = "Algorithm"
        if algo_lines and algo_lines[0].strip().startswith("//"):
            caption = algo_lines[0].strip().lstrip("/ ").strip()
            algo_lines = algo_lines[1:]
        _algo_cmds = {"\\STATE", "\\IF", "\\ELSE", "\\ELSIF", "\\ENDIF",
                       "\\FOR", "\\ENDFOR", "\\WHILE", "\\ENDWHILE",
                       "\\REPEAT", "\\UNTIL", "\\RETURN", "\\REQUIRE", "\\ENSURE"}
        wrapped_lines = []
        for al in algo_lines:
            stripped = al.strip()
            if not stripped:
                continue
            if any(stripped.startswith(cmd) for cmd in _algo_cmds):
                wrapped_lines.append(stripped)
            else:
                wrapped_lines.append(f"\\STATE {_escape_algo_line(stripped)}")
        body = "\n".join(wrapped_lines)
        return (
            "\\begin{algorithm}[ht]\n"
            f"\\caption{{{inline_converter(caption)}}}\n"
            "\\begin{algorithmic}[1]\n"
            f"{body}\n"
            "\\end{algorithmic}\n"
            "\\end{algorithm}"
        )

    return f"\\begin{{verbatim}}\n{escaped}\n\\end{{verbatim}}"

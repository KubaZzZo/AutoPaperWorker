"""Citation post-processing helpers for Stage 22 export."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from researchclaw.pipeline.stage_impls._stage23_citations import _remove_bibtex_entries

logger = logging.getLogger(__name__)


def postprocess_export_citations(
    stage_dir: Path,
    run_dir: Path,
    final_paper: str,
    *,
    read_prior_artifact: Callable[[Path, str], str | None],
    resolve_missing_citations: Callable[[set[str], str], tuple[set[str], list[str]]],
) -> tuple[list[str], str, str, str | None, dict[str, str]]:
    # Initialize artifacts list
    artifacts = ["paper_final.md"]
    # F2.7: Post-process citations — [cite_key] → \cite{cite_key}
    # and copy final references.bib to export stage
    _ay_map: dict[str, str] = {}  # BUG-102: author-year → cite_key map
    final_paper_latex = final_paper  # default when no bib_text available
    bib_text = read_prior_artifact(run_dir, "references.bib")
    if bib_text:
        # Replace [cite_key] patterns in the final paper with \cite{cite_key}
        # Collect all valid cite_keys from the bib file
        valid_keys = set(re.findall(r"@\w+\{([^,]+),", bib_text))

        # BUG-102: Recover author-year citations → [cite_key] format.
        # When Stage 19 (paper_revision) converts [cite_key] to [Author et al., 2024],
        # the downstream regex can't match them. Build a reverse map from bib entries.
        def _build_author_year_map(bib: str, keys: set[str]) -> dict[str, str]:
            """Build mapping from author-year patterns to cite_keys.

            Returns dict like:
              "Raissi et al., 2019" → "raissi2019physicsinformed"
              "Tavella and Randall, 2000" → "tavella2000pricing"
            """
            mapping: dict[str, str] = {}
            # Parse each bib entry for author + year
            # BUG-DA8-17: Allow newline OR whitespace before closing brace
            # Use \n} or just } at start-of-line to avoid greedy cross-entry match
            entry_pat = re.compile(
                r"@\w+\{([^,]+),\s*(.*?)(?:\n\}|^[ \t]*\})", re.DOTALL | re.MULTILINE
            )
            for m in entry_pat.finditer(bib):
                key = m.group(1).strip()
                if key not in keys:
                    continue
                body = m.group(2)
                # Extract author field
                author_m = re.search(
                    r"author\s*=\s*[\{\"](.*?)[\}\"]", body, re.IGNORECASE
                )
                year_m = re.search(
                    r"year\s*=\s*[\{\"]?(\d{4})[\}\"]?", body, re.IGNORECASE
                )
                if not author_m or not year_m:
                    continue
                author_raw = author_m.group(1).strip()
                year = year_m.group(1)
                # Parse author names (split on " and ")
                authors = [a.strip() for a in re.split(r"\s+and\s+", author_raw)]
                # Extract last names
                last_names = []
                for a in authors:
                    if "," in a:
                        last_names.append(a.split(",")[0].strip())
                    else:
                        parts = a.split()
                        last_names.append(parts[-1] if parts else a)
                if not last_names:
                    continue
                # Generate author-year patterns:
                # 1 author: "Smith, 2024"
                # 2 authors: "Smith and Jones, 2024"
                # 3+ authors: "Smith et al., 2024"
                if len(last_names) == 1:
                    patterns = [f"{last_names[0]}, {year}"]
                elif len(last_names) == 2:
                    patterns = [
                        f"{last_names[0]} and {last_names[1]}, {year}",
                        f"{last_names[0]} \\& {last_names[1]}, {year}",
                    ]
                else:
                    patterns = [
                        f"{last_names[0]} et al., {year}",
                        f"{last_names[0]} et al. {year}",
                    ]
                    # Also add "Smith and Jones, 2024" for first two authors
                    patterns.append(
                        f"{last_names[0]} and {last_names[1]}, {year}"
                    )
                for pat in patterns:
                    mapping[pat] = key
            return mapping

        _ay_map = _build_author_year_map(bib_text, valid_keys)
        if _ay_map:
            # Count how many author-year citations exist in the paper
            _ay_found = 0
            for _ay_pat in _ay_map:
                if _ay_pat in final_paper:
                    _ay_found += 1
            if _ay_found > 0:
                logger.info(
                    "Stage 22: Found %d author-year citation patterns — "
                    "converting back to [cite_key] format.",
                    _ay_found,
                )
                # Sort by longest pattern first to avoid partial matches
                for _ay_pat in sorted(_ay_map, key=len, reverse=True):
                    _ay_key = _ay_map[_ay_pat]
                    # Match [Author et al., 2024] or [Author and Jones, 2024; ...]
                    # Handle single-citation brackets
                    final_paper = final_paper.replace(
                        f"[{_ay_pat}]", f"[{_ay_key}]"
                    )
                    # Handle within multi-citation brackets [A et al., 2020; B et al., 2021]
                    # Replace the author-year segment only inside [...] brackets
                    final_paper = re.sub(
                        r'\[([^\]]*?)' + re.escape(_ay_pat) + r'([^\]]*?)\]',
                        lambda _m: '[' + _m.group(1) + _ay_key + _m.group(2) + ']',
                        final_paper,
                    )
                # Fix multi-key brackets: [key1; key2] → [key1, key2]
                # (author-year uses semicolons, cite-keys use commas)
                def _fix_semicolon_cites(m_sc: re.Match[str]) -> str:
                    inner = m_sc.group(1)
                    # Only convert if ALL segments look like cite keys
                    parts = [p.strip() for p in inner.split(";")]
                    _ck = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9_]*"
                    if all(re.fullmatch(_ck, p) for p in parts):
                        return "[" + ", ".join(parts) + "]"
                    return m_sc.group(0)
                final_paper = re.sub(
                    r"\[([^\]]+;[^\]]+)\]", _fix_semicolon_cites, final_paper
                )
                (stage_dir / "paper_final.md").write_text(
                    final_paper, encoding="utf-8"
                )

        # R10-Fix4: Citation cross-validation
        # BUG-187: Also parse multi-key brackets like [key1, key2, key3].
        # The old regex only matched single-key brackets [key2020word].
        _cite_key_pat = r"[a-zA-Z]+\d{4}[a-zA-Z0-9_-]*"
        cited_keys_in_paper: set[str] = set()
        # Single-key brackets
        for m in re.finditer(rf"\[({_cite_key_pat})\]", final_paper):
            cited_keys_in_paper.add(m.group(1))
        # Multi-key brackets [key1, key2] or [key1; key2]
        for m in re.finditer(r"\[([^\]]{10,300})\]", final_paper):
            inner = m.group(1)
            # Only parse if it looks like citation keys (has year-like digits)
            parts = re.split(r"[,;]\s*", inner)
            if all(re.fullmatch(_cite_key_pat, p.strip()) for p in parts if p.strip()):
                for p in parts:
                    if p.strip():
                        cited_keys_in_paper.add(p.strip())

        if valid_keys and cited_keys_in_paper:
            invalid_keys = cited_keys_in_paper - valid_keys
            if invalid_keys:
                logger.warning(
                    "Stage 22: Found %d citation keys in paper not in references.bib: %s",
                    len(invalid_keys),
                    ", ".join(sorted(invalid_keys)[:20]),
                )
                # BUG-176: Try to resolve missing citations before removing them.
                # Parse cite_key → search query, look up via academic APIs,
                # and add found entries to references.bib.
                resolved_keys: set[str] = set()
                new_bib_entries: list[str] = []
                if len(invalid_keys) <= 30:  # Sanity: don't flood APIs
                    resolved_keys, new_bib_entries = resolve_missing_citations(
                        invalid_keys, bib_text
                    )
                    if resolved_keys:
                        valid_keys.update(resolved_keys)
                        bib_text += "\n" + "\n\n".join(new_bib_entries) + "\n"
                        logger.info(
                            "Stage 22: Resolved %d/%d missing citations via API lookup",
                            len(resolved_keys), len(invalid_keys),
                        )

                still_invalid = invalid_keys - resolved_keys
                if still_invalid:
                    # IMP-29: Remove remaining unresolvable citations from
                    # BOTH single-key and multi-key brackets.
                    for bad_key in still_invalid:
                        # Remove single-key brackets
                        final_paper = final_paper.replace(f"[{bad_key}]", "")
                        # Remove from multi-key brackets: [good, BAD, good] → [good, good]
                        def _remove_from_multi(m: re.Match) -> str:
                            inner = m.group(1)
                            parts = [p.strip() for p in re.split(r"[,;]\s*", inner)]
                            filtered = [p for p in parts if p != bad_key]
                            if not filtered:
                                return ""
                            return "[" + ", ".join(filtered) + "]"
                        final_paper = re.sub(
                            r"\[([^\]]*\b" + re.escape(bad_key) + r"\b[^\]]*)\]",
                            _remove_from_multi,
                            final_paper,
                        )
                    # Clean up whitespace artifacts from removed citations
                    final_paper = re.sub(r"  +", " ", final_paper)
                    final_paper = re.sub(r" ([.,;:)])", r"\1", final_paper)
                (stage_dir / "paper_final.md").write_text(final_paper, encoding="utf-8")
                if still_invalid:
                    (stage_dir / "invalid_citations.json").write_text(
                        json.dumps(sorted(still_invalid), indent=2), encoding="utf-8"
                    )
                    artifacts.append("invalid_citations.json")
                if resolved_keys:
                    (stage_dir / "resolved_citations.json").write_text(
                        json.dumps(sorted(resolved_keys), indent=2), encoding="utf-8"
                    )
                    artifacts.append("resolved_citations.json")

        final_paper_latex = final_paper  # default: no citation conversion
        if valid_keys:
            _CITE_KEY_PAT = r"[a-zA-Z][a-zA-Z0-9_-]*\d{4}[a-zA-Z0-9]*"

            # Step 1: Convert multi-key brackets [key1, key2] → \cite{key1, key2}
            def _replace_multi_cite(m: re.Match[str]) -> str:
                keys = [k.strip() for k in m.group(1).split(",")]
                matched = [k for k in keys if k in valid_keys]
                if matched:
                    return "\\cite{" + ", ".join(matched) + "}"
                return m.group(0)

            final_paper_latex = re.sub(
                rf"\[({_CITE_KEY_PAT}(?:\s*,\s*{_CITE_KEY_PAT})+)\]",
                _replace_multi_cite,
                final_paper,
            )

            # Step 2: Convert single-key brackets [key] → \cite{key}
            def _replace_cite(m: re.Match[str]) -> str:
                key = m.group(1)
                if key in valid_keys:
                    return f"\\cite{{{key}}}"
                return m.group(0)

            final_paper_latex = re.sub(
                rf"\[({_CITE_KEY_PAT})\]", _replace_cite, final_paper_latex
            )

            # Step 3: Merge adjacent \cite{a} \cite{b} → \cite{a, b}
            def _merge_adjacent_cites(m: re.Match[str]) -> str:
                keys = re.findall(r"\\cite\{([^}]+)\}", m.group(0))
                return "\\cite{" + ", ".join(keys) + "}"

            final_paper_latex = re.sub(
                r"\\cite\{[^}]+\}(?:\s*\\cite\{[^}]+\})+",
                _merge_adjacent_cites,
                final_paper_latex,
            )

            (stage_dir / "paper_final_latex.md").write_text(
                final_paper_latex, encoding="utf-8"
            )
            artifacts.append("paper_final_latex.md")
        # IMP-1: Prune uncited bibliography entries — keep only keys
        # that actually appear in the paper text (bracket or \cite form).
        if valid_keys:
            _all_cited: set[str] = set()
            # Bracket-format citations [key]
            _all_cited.update(
                re.findall(r"\[([a-zA-Z]+\d{4}[a-zA-Z0-9_-]*)\]", final_paper)
            )
            # \cite{key, key2} format (original + latex-converted)
            for _src in (
                final_paper,
                final_paper_latex,
            ):
                for _cm in re.finditer(r"\\cite\{([^}]+)\}", _src):
                    _all_cited.update(
                        k.strip() for k in _cm.group(1).split(",")
                    )
            uncited_keys = valid_keys - _all_cited
            if uncited_keys:
                bib_text = _remove_bibtex_entries(bib_text, uncited_keys)
                logger.info(
                    "Stage 22: Pruned %d uncited bibliography entries "
                    "(kept %d)",
                    len(uncited_keys),
                    len(valid_keys) - len(uncited_keys),
                )

        # Write final references.bib
        (stage_dir / "references.bib").write_text(bib_text, encoding="utf-8")
        artifacts.append("references.bib")
        logger.info(
            "Stage 22: Exported references.bib with %d entries",
            len(valid_keys) if valid_keys else 0,
        )

    return artifacts, final_paper, final_paper_latex, bib_text, _ay_map

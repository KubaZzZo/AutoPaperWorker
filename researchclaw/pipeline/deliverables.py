from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from researchclaw.config import RCConfig
from researchclaw.pipeline.progress import utcnow_iso as _utcnow_iso

logger = logging.getLogger(__name__)


def package_deliverables(
    run_dir: Path,
    run_id: str,
    config: RCConfig,
) -> Path | None:
    """Collect all final user-facing deliverables into a single ``deliverables/`` folder.

    Returns the deliverables directory path, or None if nothing was packaged.

    Packaged artifacts (best-available version selected automatically):
    - paper_final.md          — Final paper (Markdown)
    - paper.tex               — Conference-ready LaTeX
    - references.bib          — BibTeX bibliography
    - code/                   — Experiment code package
    - verification_report.json — Citation verification report (if available)
    """
    dest = run_dir / "deliverables"
    dest.mkdir(parents=True, exist_ok=True)

    packaged: list[str] = []

    # --- 1. Final paper (Markdown) ---
    # Prefer verified version (stage 23) over base version (stage 22)
    paper_md = None
    for candidate in [
        run_dir / "stage-23" / "paper_final_verified.md",
        run_dir / "stage-22" / "paper_final.md",
    ]:
        if candidate.exists() and candidate.stat().st_size > 0:
            paper_md = candidate
            break
    if paper_md is not None:
        shutil.copy2(paper_md, dest / "paper_final.md")
        packaged.append("paper_final.md")

    # --- 2. LaTeX paper ---
    # BUG-183: Stage 22's paper.tex has been sanitized (fabricated numbers
    # replaced with ---).  Regenerating from Markdown would undo this because
    # the Markdown was never sanitized.  Prefer Stage-22 paper.tex when a
    # sanitization report exists.  Only regenerate from verified Markdown if
    # no sanitization was performed (i.e., the run was clean).
    tex_regenerated = False
    _sanitization_report = run_dir / "stage-22" / "sanitization_report.json"
    _was_sanitized = _sanitization_report.exists()
    verified_md = run_dir / "stage-23" / "paper_final_verified.md"
    if (
        not _was_sanitized
        and paper_md is not None
        and paper_md == verified_md
        and verified_md.exists()
        and verified_md.stat().st_size > 0
    ):
        try:
            from researchclaw.templates import get_template, markdown_to_latex
            from researchclaw.pipeline.executor import _extract_paper_title

            tpl = get_template(config.export.target_conference)
            v_text = verified_md.read_text(encoding="utf-8")
            tex_content = markdown_to_latex(
                v_text,
                tpl,
                title=_extract_paper_title(v_text),
                authors=config.export.authors,
                bib_file=config.export.bib_file,
            )
            # IMP-17: Quality check — ensure regenerated LaTeX has
            # proper structure (abstract, multiple sections)
            _has_abstract = (
                "\\begin{abstract}" in tex_content
                and tex_content.split("\\begin{abstract}")[1]
                .split("\\end{abstract}")[0]
                .strip()
            )
            _section_count = tex_content.count("\\section{")
            if _has_abstract and _section_count >= 3:
                (dest / "paper.tex").write_text(tex_content, encoding="utf-8")
                packaged.append("paper.tex")
                tex_regenerated = True
                logger.info(
                    "Deliverables: regenerated paper.tex from verified markdown"
                )
            else:
                logger.warning(
                    "Regenerated paper.tex has poor structure "
                    "(abstract=%s, sections=%d) — using Stage 22 version",
                    bool(_has_abstract),
                    _section_count,
                )
        except Exception:  # noqa: BLE001
            logger.debug("paper.tex regeneration from verified md failed")
    elif _was_sanitized:
        logger.info(
            "Deliverables: using Stage 22 paper.tex (sanitized) — "
            "skipping markdown regeneration to preserve sanitization"
        )

    if not tex_regenerated:
        tex_src = run_dir / "stage-22" / "paper.tex"
        if tex_src.exists() and tex_src.stat().st_size > 0:
            shutil.copy2(tex_src, dest / "paper.tex")
            packaged.append("paper.tex")

    # --- 3. References (BibTeX) ---
    # Prefer verified bib (stage 23) over base bib (stage 22)
    bib_src = None
    for candidate in [
        run_dir / "stage-23" / "references_verified.bib",
        run_dir / "stage-22" / "references.bib",
    ]:
        if candidate.exists() and candidate.stat().st_size > 0:
            bib_src = candidate
            break
    if bib_src is not None:
        shutil.copy2(bib_src, dest / "references.bib")
        packaged.append("references.bib")

    # --- 4. Experiment code package ---
    code_src = run_dir / "stage-22" / "code"
    if code_src.is_dir():
        code_dest = dest / "code"
        if code_dest.exists():
            shutil.rmtree(code_dest)
        shutil.copytree(code_src, code_dest)
        packaged.append("code/")

    # --- 5. Verification report (optional) ---
    verify_src = run_dir / "stage-23" / "verification_report.json"
    if verify_src.exists() and verify_src.stat().st_size > 0:
        shutil.copy2(verify_src, dest / "verification_report.json")
        packaged.append("verification_report.json")

    # --- 5b. Sanitization report (degraded mode) ---
    san_src = run_dir / "stage-22" / "sanitization_report.json"
    if san_src.exists() and san_src.stat().st_size > 0:
        shutil.copy2(san_src, dest / "sanitization_report.json")
        packaged.append("sanitization_report.json")

    # --- 6. Charts (optional) ---
    charts_src = run_dir / "stage-22" / "charts"
    if charts_src.is_dir() and any(charts_src.iterdir()):
        charts_dest = dest / "charts"
        if charts_dest.exists():
            shutil.rmtree(charts_dest)
        shutil.copytree(charts_src, charts_dest)
        packaged.append("charts/")

    # --- 7. Conference style files (.sty, .bst) ---
    try:
        from researchclaw.templates import get_template

        tpl = get_template(config.export.target_conference)
        style_files = tpl.get_style_files()
        for sf in style_files:
            shutil.copy2(sf, dest / sf.name)
            packaged.append(sf.name)
        if style_files:
            logger.info(
                "Deliverables: bundled %d style files for %s",
                len(style_files),
                tpl.display_name,
            )
    except Exception:  # noqa: BLE001
        logger.debug("Style file bundling skipped (template lookup failed)")

    # --- 8. Verify & repair cite key coverage (IMP-12 + IMP-14) ---
    tex_path = dest / "paper.tex"
    bib_path = dest / "references.bib"
    if tex_path.exists() and bib_path.exists():
        try:
            tex_text = tex_path.read_text(encoding="utf-8")
            bib_text = bib_path.read_text(encoding="utf-8")
            import re as _re

            # IMP-15: Deduplicate .bib entries
            _seen_bib_keys: set[str] = set()
            _deduped_entries: list[str] = []
            for _bm in _re.finditer(
                r"(@\w+\{([^,]+),.*?\n\})", bib_text, _re.DOTALL
            ):
                _bkey = _bm.group(2).strip()
                if _bkey not in _seen_bib_keys:
                    _seen_bib_keys.add(_bkey)
                    _deduped_entries.append(_bm.group(1))
            if len(_deduped_entries) < len(
                list(_re.finditer(r"@\w+\{", bib_text))
            ):
                bib_text = "\n\n".join(_deduped_entries) + "\n"
                bib_path.write_text(bib_text, encoding="utf-8")
                logger.info(
                    "Deliverables: deduplicated .bib → %d entries",
                    len(_deduped_entries),
                )

            # Collect all cite keys from \cite{key1, key2}
            all_cite_keys: set[str] = set()
            for cm in _re.finditer(r"\\cite\{([^}]+)\}", tex_text):
                all_cite_keys.update(k.strip() for k in cm.group(1).split(","))
            bib_keys = set(_re.findall(r"@\w+\{([^,]+),", bib_text))
            missing = all_cite_keys - bib_keys

            # IMP-14: Strip orphaned \cite{key} from paper.tex
            if missing:
                logger.warning(
                    "Deliverables: stripping %d orphaned cite keys from "
                    "paper.tex: %s",
                    len(missing),
                    sorted(missing)[:10],
                )

                def _filter_cite(m: _re.Match[str]) -> str:
                    keys = [k.strip() for k in m.group(1).split(",")]
                    kept = [k for k in keys if k not in missing]
                    if not kept:
                        return ""
                    return "\\cite{" + ", ".join(kept) + "}"

                tex_text = _re.sub(r"\\cite\{([^}]+)\}", _filter_cite, tex_text)
                # Clean up whitespace artifacts: double spaces, space before period
                tex_text = _re.sub(r"  +", " ", tex_text)
                tex_text = _re.sub(r" ([.,;:)])", r"\1", tex_text)
                tex_path.write_text(tex_text, encoding="utf-8")
                logger.info(
                    "Deliverables: paper.tex repaired — all remaining cite "
                    "keys verified"
                )
            else:
                logger.info(
                    "Deliverables: all %d cite keys verified in references.bib",
                    len(all_cite_keys),
                )
        except Exception:  # noqa: BLE001
            logger.debug("Cite key verification/repair skipped")

    # --- 9. IMP-18: Compile LaTeX to verify paper.tex ---
    if tex_path.exists() and bib_path.exists():
        try:
            from researchclaw.templates.compiler import compile_latex

            compile_result = compile_latex(tex_path, max_attempts=3, timeout=120)
            if compile_result.success:
                logger.info("IMP-18: paper.tex compiles successfully")
                # Keep the generated PDF
                pdf_path = dest / tex_path.stem
                pdf_file = dest / (tex_path.stem + ".pdf")
                if pdf_file.exists():
                    packaged.append(f"{tex_path.stem}.pdf")
            else:
                logger.warning(
                    "IMP-18: paper.tex compilation failed after %d attempts: %s",
                    compile_result.attempts,
                    compile_result.errors[:3],
                )
            if compile_result.fixes_applied:
                logger.info(
                    "IMP-18: Applied %d auto-fixes: %s",
                    len(compile_result.fixes_applied),
                    compile_result.fixes_applied,
                )
        except Exception:  # noqa: BLE001
            logger.debug("IMP-18: LaTeX compilation skipped (non-blocking)")

    if not packaged:
        # Nothing to package — remove empty dir
        dest.rmdir()
        return None

    # --- Write manifest ---
    manifest = {
        "run_id": run_id,
        "target_conference": config.export.target_conference,
        "files": packaged,
        "generated": _utcnow_iso(),
        "notes": {
            "paper_final.md": "Final paper in Markdown format",
            "paper.tex": f"Conference-ready LaTeX ({config.export.target_conference})",
            "references.bib": "BibTeX bibliography (verified citations only)",
            "code/": "Experiment source code with requirements.txt",
            "verification_report.json": "Citation integrity & relevance verification",
            "charts/": "Result visualizations",
        },
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    logger.info(
        "Deliverables packaged: %s (%d items)",
        dest,
        len(packaged),
    )
    return dest


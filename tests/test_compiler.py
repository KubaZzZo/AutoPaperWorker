"""Tests for researchclaw.templates.compiler — BUG-197 and general compilation.

BUG-197: pdflatex stdout containing broken UTF-8 (from U+202F error messages)
caused UnicodeDecodeError that killed the compilation pipeline, preventing
bibtex from running and leaving all citations as [?].
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from researchclaw.templates.compiler import (
    CompileResult,
    _is_fatal_error,
    _parse_log,
    _sanitize_bib_file,
    _sanitize_tex_unicode,
    check_compiled_quality,
    fix_common_latex_errors,
    remove_missing_figures,
)


# ---------------------------------------------------------------------------
# _is_fatal_error
# ---------------------------------------------------------------------------

class TestIsFatalError:
    """Test that _is_fatal_error correctly classifies errors."""

    def test_unicode_char_not_set_up_is_nonfatal(self):
        """BUG-197: Unicode character errors should be non-fatal.

        The error line captured by _parse_log is a single line:
        ``! LaTeX Error: Unicode character X (U+202F)``
        (the "not set up" text is on a continuation line).
        """
        err = "! LaTeX Error: Unicode character \u202f (U+202F)"
        assert not _is_fatal_error(err)

    def test_unicode_char_various_codepoints_nonfatal(self):
        """Various Unicode character codepoints should be non-fatal."""
        for cp in ["U+00A0", "U+2009", "U+2007", "U+3000"]:
            err = f"! LaTeX Error: Unicode character X ({cp})"
            assert not _is_fatal_error(err), f"Expected non-fatal for {cp}"

    def test_undefined_control_sequence_is_fatal(self):
        err = "! Undefined control sequence."
        assert _is_fatal_error(err)

    def test_missing_dollar_is_fatal(self):
        err = "! Missing $ inserted."
        assert _is_fatal_error(err)

    def test_overfull_hbox_is_nonfatal(self):
        err = "! Overfull \\hbox (12.3pt too wide)"
        assert not _is_fatal_error(err)

    def test_float_lost_is_nonfatal(self):
        err = "! Float(s) lost."
        assert not _is_fatal_error(err)

    def test_unavailable_in_encoding_is_nonfatal(self):
        err = "! Package inputenc Error: Unicode character unavailable in encoding OT1."
        assert not _is_fatal_error(err)

    def test_emergency_stop_is_fatal(self):
        err = "!  ==> Fatal error occurred, no output PDF file produced!"
        assert _is_fatal_error(err)

    def test_non_bang_file_not_found_is_fatal(self):
        err = "File `missing.sty' not found."
        assert _is_fatal_error(err)


# ---------------------------------------------------------------------------
# _sanitize_tex_unicode
# ---------------------------------------------------------------------------

class TestSanitizeTexUnicode:
    """Test that _sanitize_tex_unicode strips problematic Unicode."""

    def test_replaces_narrow_no_break_space(self, tmp_path: Path):
        """BUG-197: U+202F should be replaced with ASCII space."""
        tex = tmp_path / "test.tex"
        tex.write_text("Hello\u202fWorld\n", encoding="utf-8")
        _sanitize_tex_unicode(tex)
        assert tex.read_text(encoding="utf-8") == "Hello World\n"

    def test_replaces_no_break_space(self, tmp_path: Path):
        """U+00A0 should be replaced with ASCII space."""
        tex = tmp_path / "test.tex"
        tex.write_text("Hello\u00a0World\n", encoding="utf-8")
        _sanitize_tex_unicode(tex)
        assert tex.read_text(encoding="utf-8") == "Hello World\n"

    def test_removes_zero_width_space(self, tmp_path: Path):
        """U+200B should be removed entirely."""
        tex = tmp_path / "test.tex"
        tex.write_text("Hello\u200bWorld\n", encoding="utf-8")
        _sanitize_tex_unicode(tex)
        assert tex.read_text(encoding="utf-8") == "HelloWorld\n"

    def test_removes_bom(self, tmp_path: Path):
        """U+FEFF BOM should be removed."""
        tex = tmp_path / "test.tex"
        tex.write_text("\ufeffHello\n", encoding="utf-8")
        _sanitize_tex_unicode(tex)
        assert tex.read_text(encoding="utf-8") == "Hello\n"

    def test_preserves_normal_text(self, tmp_path: Path):
        """Normal ASCII + standard Unicode should be untouched."""
        content = "Hello World, \\section{Intro} $x^2$\n"
        tex = tmp_path / "test.tex"
        tex.write_text(content, encoding="utf-8")
        _sanitize_tex_unicode(tex)
        assert tex.read_text(encoding="utf-8") == content

    def test_handles_multiple_types(self, tmp_path: Path):
        """Multiple types of problematic chars in one file."""
        tex = tmp_path / "test.tex"
        tex.write_text(
            "A\u202fB\u00a0C\u200bD\u200eE\n",
            encoding="utf-8",
        )
        _sanitize_tex_unicode(tex)
        result = tex.read_text(encoding="utf-8")
        assert result == "A B CDE\n"

    def test_nonexistent_file(self, tmp_path: Path):
        """Should not crash on nonexistent file."""
        _sanitize_tex_unicode(tmp_path / "nonexistent.tex")

    def test_cyrillic_transliterated_to_latin(self, tmp_path: Path):
        """BUG-201: Cyrillic author names should be transliterated."""
        tex = tmp_path / "test.tex"
        tex.write_text(
            "А. И. Колесников\n",
            encoding="utf-8",
        )
        _sanitize_tex_unicode(tex)
        result = tex.read_text(encoding="utf-8")
        assert "А" not in result  # no Cyrillic left
        assert "И" not in result
        assert "A. I. Kolesnikov" in result


# ---------------------------------------------------------------------------
# _sanitize_bib_file — Cyrillic transliteration
# ---------------------------------------------------------------------------

class TestSanitizeBibFile:
    """Test _sanitize_bib_file fixes."""

    def test_cyrillic_author_transliterated(self, tmp_path: Path):
        """BUG-201: Cyrillic in bib author names should be transliterated."""
        from researchclaw.templates.compiler import _sanitize_bib_file

        bib = tmp_path / "references.bib"
        bib.write_text(
            '@article{dehghani2023scaling,\n'
            '  author = {А. И. Колесников and J. Doe},\n'
            '  title = {Scaling Vision},\n'
            '}\n',
            encoding="utf-8",
        )
        _sanitize_bib_file(bib)
        result = bib.read_text(encoding="utf-8")
        assert "А" not in result
        assert "A. I. Kolesnikov" in result
        assert "J. Doe" in result  # Latin unchanged

    def test_escapes_bare_ampersands_in_field_values_but_not_urls(self, tmp_path: Path):
        """Bare ampersands in BibTeX fields should be escaped conservatively."""
        bib = tmp_path / "references.bib"
        bib.write_text(
            "@article{smith2024,\n"
            "  title = {Science & Technology},\n"
            "  journal = {AI & Society},\n"
            "  url = {https://example.test/?a=1&b=2},\n"
            "  doi = {10.1000/a&b},\n"
            "}\n",
            encoding="utf-8",
        )

        _sanitize_bib_file(bib)

        result = bib.read_text(encoding="utf-8")
        assert "Science \\& Technology" in result
        assert "AI \\& Society" in result
        assert "https://example.test/?a=1&b=2" in result
        assert "10.1000/a&b" in result

    def test_strips_literal_escape_sequences_from_field_values(self, tmp_path: Path):
        """Literal Python-style whitespace escapes are not valid BibTeX content."""
        bib = tmp_path / "references.bib"
        bib.write_text(
            "@article{smith2024,\n"
            "  title = {Line\\n Break and Tab\\t Gap and Return\\r Gone},\n"
            "}\n",
            encoding="utf-8",
        )

        _sanitize_bib_file(bib)

        result = bib.read_text(encoding="utf-8")
        assert "\\n " not in result
        assert "\\t " not in result
        assert "\\r " not in result
        assert "Line  Break and Tab  Gap and Return Gone" in result


# ---------------------------------------------------------------------------
# fix_common_latex_errors — Unicode handler
# ---------------------------------------------------------------------------

class TestFixUnicodeErrors:
    """Test fix_common_latex_errors for Unicode character issues."""

    def test_unicode_u202f_replaced_with_space(self):
        """BUG-197: U+202F in text should be replaced with space."""
        tex = "Hello\u202fWorld"
        errors = [
            "! LaTeX Error: Unicode character \u202f (U+202F)"
        ]
        fixed, fixes = fix_common_latex_errors(tex, errors)
        assert "\u202f" not in fixed
        assert "Hello World" in fixed
        assert any("U+202F" in f for f in fixes)

    def test_unicode_u200b_removed(self):
        """U+200B (zero-width space, category Cf) should be removed."""
        tex = "Hello\u200bWorld"
        errors = [
            "! LaTeX Error: Unicode character \u200b (U+200B)"
        ]
        fixed, fixes = fix_common_latex_errors(tex, errors)
        assert "\u200b" not in fixed
        assert "HelloWorld" in fixed

    def test_no_unicode_error_no_change(self):
        """Text without the offending char should not be modified."""
        tex = "Hello World"
        errors = [
            "! LaTeX Error: Unicode character \u202f (U+202F)"
        ]
        fixed, fixes = fix_common_latex_errors(tex, errors)
        assert fixed == tex
        # No fix should be applied since the char isn't in the text
        assert not any("U+202F" in f for f in fixes)


class TestFixCommonLatexErrors:
    """Test automated LaTeX text repair rules."""

    def test_structural_preflight_repairs_tabular_caption_and_clearpage(self):
        tex = (
            "\\begin{tabular}\\{lc\\}\n"
            "Method \\& Score \\\\\n"
            "A \\& 1.0 \\\\\n"
            "\\end{tabular}\n"
            "\\caption{Results.\\}\n"
            "\\clearpage\n\\clearpage\n"
            "\\end{figure}\n"
            "\\textbf{Figure 1. Duplicate caption}\n"
        )

        fixed, fixes = fix_common_latex_errors(tex, [])

        assert "\\begin{tabular}{lc}" in fixed
        assert "Method & Score" in fixed
        assert "\\caption{Results.}" in fixed
        assert fixed.count("\\clearpage") == 1
        assert "\\textbf{Figure 1." not in fixed
        assert "Fixed escaped braces in tabular column specs" in fixes
        assert any("tabular data" in fix for fix in fixes)
        assert "Fixed escaped \\} in \\caption arguments" in fixes
        assert "Collapsed multiple \\clearpage commands" in fixes
        assert "Removed duplicate bold Figure captions after \\end{figure}" in fixes

    def test_algorithmic_pseudocode_comments_and_identifiers_are_latex_safe(self):
        tex = (
            "\\begin{algorithmic}\n"
            "\\STATE best_score = current_score # keep best\n"
            "\\end{algorithmic}\n"
        )

        fixed, fixes = fix_common_latex_errors(tex, [])

        assert "best_score = current_score" in fixed
        assert "\\COMMENT{keep best}" in fixed
        assert "Fixed Python-style pseudocode in algorithmic environment" in fixes

    def test_undefined_safe_control_sequence_is_unwrapped(self):
        tex = "This is \\textsc{Small Caps} and \\unknown{kept}."
        fixed, fixes = fix_common_latex_errors(
            tex,
            ["! Undefined control sequence. \\textsc"],
        )

        assert "Small Caps" in fixed
        assert "\\textsc" not in fixed
        assert "\\unknown{kept}" in fixed
        assert "Removed undefined \\textsc" in fixes

    def test_missing_dollar_and_unsupported_k_command_are_repaired(self):
        tex = "metric\\\\_name and bare_value and \\k{a}"
        fixed, fixes = fix_common_latex_errors(
            tex,
            ["! Missing $ inserted.", "! LaTeX Error: Command \\k unavailable"],
        )

        assert "metric\\_name" in fixed
        assert "bare\\_value" in fixed
        assert "\\k" not in fixed
        assert "Collapsed double-escaped underscores" in fixes
        assert "Escaped bare underscores outside math" in fixes
        assert "Removed unsupported \\k command" in fixes

    def test_missing_package_and_float_overflow_repairs_are_applied(self):
        tex = (
            "\\documentclass{article}\n"
            "\\usepackage{missingpkg}\n"
            "\\begin{document}\n"
            "\\begin{figure}[t]\\resizebox{\\textwidth}{!}{x}\\end{figure}\n"
            "\\begin{table}[ht]x\\end{table}\n"
            "\\end{document}\n"
        )
        fixed, fixes = fix_common_latex_errors(
            tex,
            ["File `missingpkg.sty' not found.", "Too many unprocessed floats"],
        )

        assert "% IMP-18: Removed missing package missingpkg" in fixed
        assert "\\extrafloats{200}" in fixed
        assert "\\resizebox{\\columnwidth}" in fixed
        assert "\\begin{figure}[htbp!]" in fixed
        assert "\\clearpage\n\\begin{table}" in fixed
        assert "Removed missing package missingpkg" in fixes
        assert "Added \\extrafloats{200} for float overflow" in fixes
        assert "Replaced \\textwidth with \\columnwidth in resizebox" in fixes


class TestParseLogAndQualityChecks:
    """Test log parsing and post-compilation quality checks."""

    def test_parse_log_collects_errors_and_warnings_once(self):
        errors, warnings = _parse_log(
            "\n".join(
                [
                    "! Undefined control sequence.",
                    "LaTeX Warning: Citation `x' undefined.",
                    "Missing $ inserted.",
                    "File `missing.sty' not found.",
                    "Float(s) lost.",
                    "Too many unprocessed floats.",
                ]
            )
        )

        assert errors == [
            "! Undefined control sequence.",
            "Missing $ inserted.",
            "File `missing.sty' not found.",
            "Float(s) lost.",
            "Too many unprocessed floats.",
        ]
        assert warnings == ["LaTeX Warning: Citation `x' undefined."]

    def test_check_compiled_quality_summarizes_log_aux_and_figure_refs(self, tmp_path: Path):
        tex = tmp_path / "paper.tex"
        tex.write_text(
            "\\begin{figure}\\label{fig:unused}\\end{figure}\n"
            "See Figure~\\ref{fig:missing}.\n",
            encoding="utf-8",
        )
        (tmp_path / "paper.aux").write_text(
            "\\newlabel{LastPage}{{12}{1}}\n",
            encoding="utf-8",
        )
        (tmp_path / "paper.log").write_text(
            "\n".join(
                [
                    "LaTeX Warning: Reference `fig:missing' undefined.",
                    "LaTeX Warning: Citation `smith2024' undefined.",
                    "Overfull \\hbox (2.5pt too wide) in paragraph",
                    "Overfull \\hbox (0.5pt too wide) in paragraph",
                    "Underfull \\hbox (badness 7000) in paragraph",
                ]
            ),
            encoding="utf-8",
        )

        result = check_compiled_quality(tex, page_limit=10)

        assert result.has_critical_issues
        assert len(result.unresolved_refs) == 1
        assert len(result.unresolved_cites) == 1
        assert len(result.overfull_hboxes) == 1
        assert len(result.underfull_hboxes) == 1
        assert result.page_count == 12
        assert result.orphan_labels == ["fig:unused"]
        assert result.orphan_figures == ["fig:missing"]
        assert "Page count 12 exceeds limit 10" in result.warnings_summary

    def test_check_compiled_quality_falls_back_to_page_count_from_log(self, tmp_path: Path):
        tex = tmp_path / "paper.tex"
        tex.write_text("No figures here.", encoding="utf-8")
        (tmp_path / "paper.log").write_text(
            "Output written on paper.pdf (3 pages)",
            encoding="utf-8",
        )

        result = check_compiled_quality(tex, page_limit=10)

        assert result.page_count == 3
        assert not result.has_critical_issues
        assert result.warnings_summary == []


class TestRemoveMissingFigures:
    """Test figure block removal and automatic image remapping."""

    def test_removes_missing_figure_and_rewrites_orphan_references(self, tmp_path: Path):
        tex = (
            "See Figure~\\ref{fig:missing} for details.\n"
            "\\begin{figure}\n"
            "\\includegraphics{figures/missing.png}\n"
            "\\caption{Missing}\\label{fig:missing}\n"
            "\\end{figure}\n"
        )

        fixed, removed = remove_missing_figures(tex, tmp_path)

        assert removed == ["figures/missing.png"]
        assert "\\begin{figure}" not in fixed
        assert "(figure omitted)" in fixed

    def test_remaps_single_prefix_match_instead_of_removing(self, tmp_path: Path):
        figures = tmp_path / "figures"
        figures.mkdir()
        (figures / "main_results_comparison.png").write_bytes(b"png")
        tex = (
            "\\begin{figure}\n"
            "\\includegraphics{figures/main_results.png}\n"
            "\\caption{Results}\\label{fig:results}\n"
            "\\end{figure}\n"
        )

        fixed, removed = remove_missing_figures(tex, tmp_path)

        assert removed == []
        assert "main_results_comparison.png" in fixed
        assert "\\begin{figure}" in fixed


# ---------------------------------------------------------------------------
# _run_pdflatex — bytes mode decoding
# ---------------------------------------------------------------------------

class TestRunPdflatexByteMode:
    """Test that _run_pdflatex handles broken UTF-8 in stdout."""

    @patch("researchclaw.templates.compiler.subprocess.run")
    def test_broken_utf8_in_stdout_does_not_crash(self, mock_run):
        """BUG-197: Broken UTF-8 bytes should be decoded with replacement."""
        from researchclaw.templates.compiler import _run_pdflatex

        # Simulate pdflatex returning broken UTF-8 in stdout
        mock_proc = MagicMock()
        mock_proc.stdout = b"Normal output \xe2\x80 broken"  # Invalid UTF-8
        mock_proc.stderr = b""
        mock_proc.returncode = 1
        mock_run.return_value = mock_proc

        log_text, success = _run_pdflatex(Path("/tmp"), "test.tex", timeout=60)

        assert log_text is not None
        assert "Normal output" in log_text
        assert not success

    @patch("researchclaw.templates.compiler.subprocess.run")
    def test_valid_utf8_works(self, mock_run):
        """Normal UTF-8 output should work fine."""
        from researchclaw.templates.compiler import _run_pdflatex

        mock_proc = MagicMock()
        mock_proc.stdout = b"Output written on test.pdf (1 page)"
        mock_proc.stderr = b""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        log_text, success = _run_pdflatex(Path("/tmp"), "test.tex", timeout=60)

        assert log_text is not None
        assert "Output written" in log_text
        assert success


# ---------------------------------------------------------------------------
# _run_bibtex — bytes mode decoding + logging
# ---------------------------------------------------------------------------

class TestRunBibtex:
    """Test that _run_bibtex handles errors and logs properly."""

    @patch("researchclaw.templates.compiler.shutil.which", return_value="/usr/bin/bibtex")
    @patch("researchclaw.templates.compiler.subprocess.run")
    def test_bibtex_failure_logged(self, mock_run, mock_which, tmp_path):
        """Failed bibtex should log warning and return False."""
        from researchclaw.templates.compiler import _run_bibtex

        mock_proc = MagicMock()
        mock_proc.stdout = b"I couldn't open file name.aux"
        mock_proc.stderr = b""
        mock_proc.returncode = 1
        mock_run.return_value = mock_proc

        result = _run_bibtex(tmp_path, "paper", timeout=60)
        assert result is False

    @patch("researchclaw.templates.compiler.shutil.which", return_value="/usr/bin/bibtex")
    @patch("researchclaw.templates.compiler.subprocess.run")
    def test_bibtex_success_with_bbl(self, mock_run, mock_which, tmp_path):
        """Successful bibtex with .bbl creation should return True."""
        from researchclaw.templates.compiler import _run_bibtex

        # Create fake .bbl so the check passes
        (tmp_path / "paper.bbl").write_text("\\begin{thebibliography}{}")

        mock_proc = MagicMock()
        mock_proc.stdout = b"Database file #1: references.bib"
        mock_proc.stderr = b""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        result = _run_bibtex(tmp_path, "paper", timeout=60)
        assert result is True

    @patch("researchclaw.templates.compiler.shutil.which", return_value=None)
    def test_bibtex_not_found(self, mock_which, tmp_path):
        """Missing bibtex binary should return False."""
        from researchclaw.templates.compiler import _run_bibtex

        result = _run_bibtex(tmp_path, "paper", timeout=60)
        assert result is False

    @patch("researchclaw.templates.compiler.shutil.which", return_value="/usr/bin/bibtex")
    @patch("researchclaw.templates.compiler.subprocess.run")
    def test_bibtex_broken_utf8(self, mock_run, mock_which, tmp_path):
        """BUG-197: Broken UTF-8 in bibtex output should not crash."""
        from researchclaw.templates.compiler import _run_bibtex

        (tmp_path / "paper.bbl").write_text("\\begin{thebibliography}{}")

        mock_proc = MagicMock()
        mock_proc.stdout = b"Database file \xe2\x80 broken"
        mock_proc.stderr = b""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        # Should not raise
        result = _run_bibtex(tmp_path, "paper", timeout=60)
        assert result is True

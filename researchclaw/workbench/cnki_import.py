"""Semi-automatic CNKI import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CNKIRecord:
    title: str
    authors: tuple[str, ...] = ()
    year: int = 0
    venue: str = ""
    url: str = ""
    source: str = "cnki"
    file_path: str = ""


def import_cnki_files(paths: list[str | Path]) -> list[CNKIRecord]:
    """Import CNKI-exported metadata files and local PDFs."""
    records: list[CNKIRecord] = []
    for raw in paths:
        path = Path(raw)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            records.append(CNKIRecord(title=path.stem, file_path=str(path), source="cnki"))
        elif suffix == ".ris":
            records.extend(_parse_ris(path))
        elif suffix in {".txt", ".enw", ".bib"}:
            records.extend(_parse_simple_metadata(path))
    return records


def _parse_ris(path: Path) -> list[CNKIRecord]:
    entries: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if len(line) < 6 or "  - " not in line:
            continue
        tag, value = line[:2], line[6:].strip()
        if tag == "TY":
            current = {}
        elif tag == "ER":
            if current:
                entries.append(current)
                current = {}
        else:
            current.setdefault(tag, []).append(value)
    if current:
        entries.append(current)
    return [_record_from_mapping(entry) for entry in entries if entry.get("TI")]


def _parse_simple_metadata(path: Path) -> list[CNKIRecord]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    title = ""
    authors: tuple[str, ...] = ()
    year = 0
    venue = ""
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            key, sep, value = line.partition("=")
        key_norm = key.strip().lower()
        value = value.strip().strip("{}")
        if key_norm in {"title", "ti", "题名", "标题"}:
            title = value
        elif key_norm in {"author", "authors", "au", "作者"}:
            authors = tuple(
                a.strip() for a in value.replace(" and ", ";").split(";") if a.strip()
            )
        elif key_norm in {"year", "py", "年份"}:
            year = _safe_year(value)
        elif key_norm in {"journal", "jo", "venue", "来源"}:
            venue = value
    return [CNKIRecord(title=title or path.stem, authors=authors, year=year, venue=venue)]


def _record_from_mapping(data: dict[str, list[str]]) -> CNKIRecord:
    return CNKIRecord(
        title=_first(data, "TI"),
        authors=tuple(data.get("AU", ())),
        year=_safe_year(_first(data, "PY")),
        venue=_first(data, "JO"),
        url=_first(data, "UR"),
    )


def _first(data: dict[str, list[str]], key: str) -> str:
    values = data.get(key) or []
    return values[0] if values else ""


def _safe_year(value: str) -> int:
    try:
        return int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return 0

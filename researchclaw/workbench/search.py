"""Literature search adapters for the workbench."""

from __future__ import annotations

from dataclasses import dataclass

from researchclaw.literature.search import search_papers


@dataclass(frozen=True)
class WorkbenchPaper:
    title: str
    year: int = 0
    source: str = ""
    url: str = ""
    abstract: str = ""
    citation_count: int = 0


def search_papers_for_workbench(topic: str, limit: int = 10) -> list[WorkbenchPaper]:
    """Search OpenAlex and arXiv for a lightweight workbench preview."""
    papers = search_papers(
        query=topic,
        limit=limit,
        sources=("openalex", "arxiv"),
        deduplicate=True,
    )
    return [
        WorkbenchPaper(
            title=p.title,
            year=p.year,
            source=p.source,
            url=p.url,
            abstract=p.abstract,
            citation_count=p.citation_count,
        )
        for p in papers
    ]


def cnki_search_url(query: str) -> str:
    """Return the CNKI entry URL.

    CNKI stays semi-automatic: users open the site, log in, search, export,
    and import the resulting metadata/PDFs.
    """
    _ = query
    return "https://www.cnki.net/"

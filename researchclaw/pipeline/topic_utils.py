"""Topic query and constraint helpers for pipeline stages."""

from __future__ import annotations

import re
from collections.abc import Iterable

from researchclaw.utils.text import BASE_STOP_WORDS


def build_fallback_queries(topic: str) -> list[str]:
    """Extract targeted search queries from a long research topic."""
    chunks = re.split(r"[,:;()\[\]]+", topic)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 8]
    cleaned_chunks = []
    for c in chunks:
        c = re.sub(
            r"^(and|or|the|a|an|in|of|for|with|across|multiple|three|various)\s+",
            "",
            c,
            flags=re.IGNORECASE,
        )
        c = c.strip()
        if len(c) > 8:
            cleaned_chunks.append(c)
    chunks = cleaned_chunks

    stop = {
        "the", "and", "for", "with", "from", "that", "this", "into",
        "over", "across", "multiple", "three", "result", "comprehensive",
        "using", "based", "between", "various", "different", "several",
        "parameter", "parameters", "analysis", "approach", "method",
        "framework", "frameworks",
    }
    words = topic.lower().split()
    key_terms = [w for w in words if len(w) > 3 and w not in stop]

    queries: list[str] = []
    for chunk in chunks[:4]:
        if len(chunk) > 60:
            chunk = " ".join(chunk.split()[:6])
        if chunk and chunk not in queries:
            queries.append(chunk)

    clean_terms = [t for t in key_terms if re.match(r"^[a-z]", t) and ":" not in t]
    for i in range(min(len(clean_terms) - 1, 4)):
        bigram = f"{clean_terms[i]} {clean_terms[i + 1]}"
        if bigram not in queries:
            queries.append(bigram)

    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        query_lower = query.strip().lower()
        if query_lower and query_lower not in seen:
            seen.add(query_lower)
            unique.append(query.strip())

    topic_short = topic[:60].strip()
    for suffix in ("survey", "review", "benchmark", "state of the art", "recent advances"):
        if len(unique) >= 5:
            break
        candidate = f"{topic_short} {suffix}".strip()
        if candidate.lower() not in seen:
            seen.add(candidate.lower())
            unique.append(candidate)

    return unique[:10]


def extract_topic_keywords(
    topic: str,
    domains: Iterable[str] = (),
    *,
    stop_words: frozenset[str] = BASE_STOP_WORDS,
) -> list[str]:
    """Extract lowercased topic/domain keywords for relevance filters."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", topic.lower())
    keywords = [t for t in tokens if t not in stop_words and len(t) >= 3]
    for domain in domains:
        for part in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", domain.lower()):
            if part not in stop_words and len(part) >= 2:
                keywords.append(part)

    seen: set[str] = set()
    unique: list[str] = []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            unique.append(keyword)
    return unique


def topic_constraint_block(topic: str) -> str:
    """Return a hard constraint instruction anchoring paper content to the topic."""
    return (
        "\n\n=== HARD TOPIC CONSTRAINT ===\n"
        f"The paper MUST be about: {topic}\n"
        "PROHIBITED content (unless user explicitly specifies case-study mode):\n"
        "- Do NOT treat environment setup, dependency installation, or infrastructure "
        "failures as a research contribution.\n"
        "- Do NOT present debugging logs, system errors, or configuration issues "
        "as experimental findings.\n"
        "- Do NOT drift to tangential topics not directly related to the stated topic.\n"
        "- Every section MUST connect back to the core research question.\n"
        "- The Abstract and Introduction MUST clearly state the research problem "
        f"derived from: {topic}\n"
        "- The Method section MUST describe a technical approach, not a workflow.\n"
        "- The Results section MUST report quantitative outcomes of experiments, "
        "not environment status.\n"
        "=== END CONSTRAINT ===\n"
    )

"""Default prompt templates for a focused group of pipeline stages."""

from __future__ import annotations

from typing import Any

_LITERATURE_STAGES: dict[str, dict[str, Any]] = {
    "search_strategy": {
        "system": (
            "You design literature retrieval strategies and source verification plans."
        ),
        "user": (
            "Create a merged search strategy package.\n"
            "Return a JSON object with keys: search_plan_yaml, sources.\n"
            "search_plan_yaml must be valid YAML text.\n"
            "sources must include id,name,type,url,status,query,verified_at.\n"
            "Topic: {topic}\n"
            "Problem tree:\n{problem_tree}"
        ),
        "json_mode": True,
    },
    "literature_collect": {
        "system": "You are a literature mining assistant.",
        "user": (
            "Generate candidate papers from the search plan.\n"
            "Return JSON: {candidates:[...]} with >=8 rows.\n"
            "Each candidate must include id,title,source,url,year,abstract,"
            "collected_at.\n"
            "Topic: {topic}\n"
            "Search plan:\n{plan_text}"
        ),
        "json_mode": True,
    },
    "literature_screen": {
        "system": (
            "You are a strict domain-aware reviewer with zero tolerance for "
            "cross-domain false positives. You MUST reject papers that are "
            "from unrelated fields, even if they share superficial keyword "
            "overlap. A paper about 'normalization in database systems' is "
            "NOT relevant to 'normalization in deep learning'. A paper about "
            "'graph theory in social networks' is NOT relevant to 'graph "
            "neural networks for molecular property prediction'."
        ),
        "user": (
            "Perform merged relevance+quality screening and return shortlist.\n"
            "Return JSON: {shortlist:[...]} each with title, cite_key "
            "(if present), relevance_score (0-1), quality_score (0-1), "
            "keep_reason.\n"
            "Preserve all original fields (paper_id, doi, arxiv_id, cite_key, "
            "etc.) from the input.\n"
            "Topic: {topic}\n"
            "Domains: {domains}\n"
            "Threshold: {quality_threshold}\n\n"
            "SCREENING RULES (apply strictly):\n"
            "1. DOMAIN MATCH: The paper's actual research domain must match "
            "the topic's domain. Shared keywords across domains do NOT count.\n"
            "2. METHOD RELEVANCE: The paper must discuss methods, benchmarks, "
            "or findings directly applicable to the research topic.\n"
            "3. CROSS-DOMAIN REJECTION: Reject papers from unrelated fields "
            "(e.g., wireless communications, database systems, social science) "
            "even if they use similar terminology.\n"
            "4. RECENCY PREFERENCE: Prefer papers from 2020+ for methodology, "
            "but accept foundational papers (pre-2020) if they introduced key "
            "techniques still in use today.\n"
            "5. SEMINAL PAPERS: Papers marked as source='seminal_library' are "
            "pre-vetted foundational references — keep them if their keywords "
            "match the topic (relevance_score >= 0.7).\n"
            "6. QUALITY FLOOR: Reject papers with no abstract, no venue, and "
            "no citation count (likely not real papers).\n"
            "Candidates JSONL:\n{candidates_text}"
        ),
        "json_mode": True,
    },
    "knowledge_extract": {
        "system": "You extract high-signal evidence cards from papers.",
        "user": (
            "Extract structured knowledge cards from shortlist.\n"
            "Return JSON: {cards:[{card_id,title,cite_key,problem,method,"
            "data,metrics,findings,limitations,citation}]}.\n"
            "IMPORTANT: If the input contains cite_key fields, preserve them "
            "exactly in the output.\n"
            "Shortlist:\n{shortlist}"
        ),
        "json_mode": True,
    },
    "synthesis": {
        "system": "You are a synthesis specialist for literature reviews.",
        "user": (
            "Produce merged synthesis output (topic clusters + research gaps).\n"
            "Output markdown with sections: Cluster Overview, Cluster 1..N, "
            "Gap 1..N, Prioritized Opportunities.\n"
            "Topic: {topic}\n"
            "Cards context:\n{cards_context}"
        ),
        "max_tokens": 8192,
    },
    "hypothesis_gen": {
        "system": (
            "You formulate testable scientific hypotheses that address gaps "
            "NOT covered by existing literature. Your hypotheses must be:\n"
            "1. NOVEL: Not simply replicating known results or testing obvious things.\n"
            "2. GAP-FILLING: Address specific weaknesses or blind spots identified "
            "in the literature synthesis.\n"
            "3. FEASIBLE: Testable with limited compute (single GPU, <1 day runtime).\n"
            "4. FALSIFIABLE: Have clear failure conditions that would definitively "
            "reject the hypothesis.\n"
            "5. SURPRISING: At least one hypothesis should challenge conventional "
            "wisdom or test a counter-intuitive prediction."
        ),
        "user": (
            "Generate at least 2 falsifiable hypotheses from the synthesis below.\n"
            "For each hypothesis provide:\n"
            "- **Hypothesis statement**: A clear, testable claim\n"
            "- **Novelty argument**: Why this has NOT been tested before, citing "
            "specific gaps from the synthesis\n"
            "- **Rationale**: Theoretical or empirical basis for expecting this result\n"
            "- **Measurable prediction**: Specific quantitative outcome expected\n"
            "- **Failure condition**: What result would reject this hypothesis?\n"
            "- **Required baselines**: What modern, state-of-the-art methods must be "
            "compared against to make the finding meaningful?\n\n"
            "AVOID:\n"
            "- Hypotheses that are trivially obvious (e.g., 'more data improves accuracy')\n"
            "- Hypotheses that replicate well-known results already in the literature\n"
            "- Hypotheses that cannot be tested within the compute budget\n\n"
            "Synthesis:\n{synthesis}"
        ),
    },
}

__all__ = ["_LITERATURE_STAGES"]

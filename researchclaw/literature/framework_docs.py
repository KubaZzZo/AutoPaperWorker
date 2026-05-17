"""Live framework documentation fetcher (F-01 Phase 2).

Fetches latest API docs from framework documentation sites, with a fallback
chain: llms.txt → web crawl → static bundled docs.

Cache directory: ``.researchclaw_cache/framework_docs/``
TTL: 7 days
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from researchclaw.data import _FRAMEWORK_REGISTRY as _STATIC_REGISTRY
from researchclaw.utils.http import urlopen_http

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".researchclaw_cache") / "framework_docs"
_TTL_SEC = 86400 * 7  # 7 days
_USER_AGENT = "ResearchClaw/0.5 (Academic Research Bot; framework doc fetcher)"

# Framework documentation URLs for live fetching.
# Each framework entry maps to its documentation root and optional llms.txt URL.
_FRAMEWORK_DOC_URLS: dict[str, dict[str, str]] = {
    "trl": {
        "docs_url": "https://huggingface.co/docs/trl/main/en/",
        "llms_txt": "https://huggingface.co/docs/trl/main/en/llms.txt",
    },
    "peft": {
        "docs_url": "https://huggingface.co/docs/peft/main/en/",
        "llms_txt": "https://huggingface.co/docs/peft/main/en/llms.txt",
    },
    "transformers_training": {
        "docs_url": "https://huggingface.co/docs/transformers/main/en/training",
        "llms_txt": "https://huggingface.co/docs/transformers/main/en/llms.txt",
    },
    "llamafactory": {
        "docs_url": "https://llamafactory.readthedocs.io/en/latest/",
        "llms_txt": "https://llamafactory.readthedocs.io/en/latest/llms.txt",
    },
    "axolotl": {
        "docs_url": "https://axolotl-ai-cloud.github.io/axolotl/",
        "llms_txt": "https://axolotl-ai-cloud.github.io/axolotl/llms.txt",
    },
}


def _cache_dir() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _cache_key(framework_id: str) -> str:
    return hashlib.sha256(f"framework_doc:{framework_id}".encode()).hexdigest()[:16]


def _get_cached(framework_id: str) -> str | None:
    """Return cached doc content or None if miss/expired."""
    key = _cache_key(framework_id)
    path = _cache_dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - data.get("timestamp", 0)
        if age > _TTL_SEC:
            logger.debug("Framework doc cache expired: %s (age=%.0fs)", framework_id, age)
            return None
        return data.get("content", "")
    except Exception:
        return None


def _put_cache(framework_id: str, content: str) -> None:
    key = _cache_key(framework_id)
    path = _cache_dir() / f"{key}.json"
    path.write_text(
        json.dumps({"framework_id": framework_id, "content": content, "timestamp": time.time()}),
        encoding="utf-8",
    )


def _http_get(url: str, timeout: int = 15) -> str | None:
    """Fetch URL content as text. Returns None on any error."""
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        resp = urlopen_http(req, timeout=timeout)
        raw = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        return raw.decode(encoding, errors="replace")
    except (HTTPError, URLError, OSError, ValueError) as exc:
        logger.debug("HTTP fetch failed for %s: %s", url, exc)
        return None


def _strip_html(html: str) -> str:
    """Naive HTML-to-text conversion for fallback fetching."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch(framework_id: str, max_chars: int = 8000) -> str:
    """Fetch live documentation for a single framework.

    Fallback chain:
    1. llms.txt (standardized LLM-friendly doc format)
    2. HTML page fetch with tag stripping
    3. Context7 MCP server (if available)
    4. Static bundled docs

    Results are cached for 7 days.
    """
    cached = _get_cached(framework_id)
    if cached:
        logger.info("Framework doc cache hit: %s (%d chars)", framework_id, len(cached))
        return cached[:max_chars] if len(cached) > max_chars else cached

    doc_urls = _FRAMEWORK_DOC_URLS.get(framework_id)
    if not doc_urls:
        logger.debug("No live doc URLs for framework: %s", framework_id)
        return ""

    content = ""

    # 1. Try llms.txt
    llms_url = doc_urls.get("llms_txt", "")
    if llms_url:
        logger.info("Fetching llms.txt for %s: %s", framework_id, llms_url)
        raw = _http_get(llms_url, timeout=15)
        if raw and len(raw.strip()) > 100:
            content = raw.strip()
            logger.info("llms.txt success for %s: %d chars", framework_id, len(content))

    # 2. Fall back to HTML scraping
    if not content:
        docs_url = doc_urls.get("docs_url", "")
        if docs_url:
            logger.info("Fetching docs page for %s: %s", framework_id, docs_url)
            html = _http_get(docs_url, timeout=15)
            if html:
                content = _strip_html(html)
                if len(content) < 200:
                    content = ""
                    logger.debug("Stripped HTML too short for %s, discarding", framework_id)

    if content:
        if len(content) > max_chars:
            content = content[:max_chars]
        _put_cache(framework_id, content)
        return content

    # 3. Try Context7 MCP server
    try:
        from researchclaw.mcp.context7_client import Context7MCPClient
        _c7 = Context7MCPClient()
        if _c7.available:
            _fw_name_map: dict[str, str] = {
                "trl": "trl",
                "peft": "peft",
                "transformers_training": "transformers",
                "llamafactory": "llamafactory",
                "axolotl": "axolotl",
            }
            _fw_name = _fw_name_map.get(framework_id, framework_id)
            _c7_docs = _c7.query_framework_docs(_fw_name, max_chars=max_chars)
            _c7.close()
            if _c7_docs:
                _put_cache(framework_id, _c7_docs)
                return _c7_docs
    except Exception:
        logger.debug("Context7 MCP unavailable for %s", framework_id, exc_info=True)

    # 4. Try static bundled docs
    logger.debug("Live fetch failed for %s, trying static fallback", framework_id)
    info = _STATIC_REGISTRY.get(framework_id)
    if info:
        from researchclaw.data import _FRAMEWORK_DOCS_DIR

        doc_path = _FRAMEWORK_DOCS_DIR / info.get("file", "")
        if doc_path.exists():
            static_content = doc_path.read_text(encoding="utf-8")
            logger.info("Static fallback for %s: %d chars", framework_id, len(static_content))
            return static_content[:max_chars] if len(static_content) > max_chars else static_content

    return ""


def fetch_all(framework_ids: list[str], max_chars: int = 8000) -> str:
    """Fetch live docs for multiple frameworks, same interface as load_framework_docs()."""
    parts: list[str] = []
    total = 0
    for fw_id in framework_ids:
        content = fetch(fw_id, max_chars=min(max_chars, max_chars - total) if total < max_chars else 0)
        if not content:
            continue
        if total + len(content) > max_chars:
            remaining = max_chars - total
            if remaining > 500:
                content = content[:remaining] + "\n... (truncated)\n"
            else:
                break
        parts.append(content)
        total += len(content)

    if not parts:
        return ""

    header = (
        "\n## Framework API Documentation (live-fetched)\n"
        "The following API references are relevant to your experiment. "
        "Use these exact APIs and patterns — do NOT guess the API.\n\n"
    )
    return header + "\n---\n\n".join(parts)


def refresh_cache(framework_id: str | None = None) -> int:
    """Clear cached docs for one or all frameworks. Returns number cleared."""
    if framework_id:
        key = _cache_key(framework_id)
        path = _cache_dir() / f"{key}.json"
        if path.exists():
            path.unlink()
            return 1
        return 0

    count = 0
    for p in _cache_dir().glob("*.json"):
        p.unlink()
        count += 1
    return count

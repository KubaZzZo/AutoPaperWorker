"""ResearchClaw MCP Server: expose pipeline capabilities to external agents."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from researchclaw.mcp.tools import TOOL_DEFINITIONS, list_tool_names

logger = logging.getLogger(__name__)

_VALID_RUN_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")
_REQUIRED_PAPER_SECTIONS = {
    "abstract": ("abstract",),
    "introduction": ("introduction",),
    "methods": ("methods", "method", "methodology", "approach"),
    "results": ("results", "experiments", "experiment"),
    "references": ("references", "bibliography"),
}


def _normalize_heading(heading: str) -> str:
    """Normalize a markdown/LaTeX heading for section checks."""
    return re.sub(r"[^a-z0-9]+", " ", heading.lower()).strip()


def _validated_run_dir(run_id: str) -> Path:
    """Validate run_id to prevent path traversal and return the run directory."""
    if not _VALID_RUN_ID.match(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    run_dir = Path("artifacts") / run_id
    # Ensure resolved path is still under artifacts/
    if not run_dir.resolve().is_relative_to(Path("artifacts").resolve()):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    return run_dir


class ResearchClawMCPServer:
    """MCP Server that exposes AutoResearchClaw capabilities as tools.

    External agents (e.g., Claude, OpenClaw) can connect to this server
    and invoke pipeline operations via the MCP protocol.
    """

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self._handlers: dict[str, Any] = {}
        self._running = False

    def get_tools(self) -> list[dict[str, Any]]:
        """Return the list of available MCP tools."""
        return TOOL_DEFINITIONS

    async def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle an incoming MCP tool call."""
        if name not in list_tool_names():
            return {"error": f"Unknown tool: {name}", "success": False}

        logger.info("MCP tool call: %s(%s)", name, json.dumps(arguments, default=str)[:200])

        try:
            if name == "run_pipeline":
                return await self._handle_run_pipeline(arguments)
            elif name == "get_pipeline_status":
                return await self._handle_get_status(arguments)
            elif name == "get_experiment_results":
                return await self._handle_get_results(arguments)
            elif name == "search_literature":
                return await self._handle_search_literature(arguments)
            elif name == "review_paper":
                return await self._handle_review_paper(arguments)
            elif name == "get_paper":
                return await self._handle_get_paper(arguments)
            else:
                return {"error": f"Handler not implemented: {name}", "success": False}
        except Exception as exc:
            logger.error("MCP tool call %s failed: %s", name, exc)
            return {"error": str(exc), "success": False}

    async def _handle_run_pipeline(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a trackable pipeline run request."""
        topic = str(args["topic"]).strip()
        if not topic:
            return {"success": False, "error": "Topic must not be empty"}
        now = datetime.now(timezone.utc)
        topic_hash = sha256(topic.encode("utf-8")).hexdigest()[:6]
        run_id = f"rc-{now.strftime('%Y%m%d-%H%M%S')}-{topic_hash}"
        run_dir = _validated_run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "run_id": run_id,
            "topic": topic,
            "status": "queued",
            "stage": 0,
            "stage_name": "MCP_REQUEST_QUEUED",
            "start_time": now.isoformat(),
            "config_path": args.get("config_path", ""),
            "auto_approve": bool(args.get("auto_approve", False)),
        }
        progress = {
            "run_id": run_id,
            "status": "queued",
            "current_stage": 0,
            "current_stage_name": "MCP_REQUEST_QUEUED",
            "total_stages": 23,
            "elapsed_sec": 0.0,
            "stages_done": 0,
            "stages_failed": 0,
            "stages_paused": 0,
            "stages_blocked": 0,
            "cost_usd": 0.0,
            "updated_at": now.isoformat(),
        }
        (run_dir / "checkpoint.json").write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / "progress.json").write_text(
            json.dumps(progress, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "success": True,
            "message": f"Pipeline request queued for topic: {topic}",
            "run_id": run_id,
            "status": "queued",
            "output_dir": str(run_dir.resolve()),
        }

    async def _handle_get_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get pipeline status."""
        run_id = args["run_id"]
        run_dir = _validated_run_dir(run_id)
        if not run_dir.exists():
            return {"success": False, "error": f"Run not found: {run_id}"}
        # Read checkpoint if available
        checkpoint_file = run_dir / "checkpoint.json"
        if checkpoint_file.exists():
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            return {"success": True, "run_id": run_id, "checkpoint": data}
        return {"success": True, "run_id": run_id, "status": "no_checkpoint"}

    async def _handle_get_results(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get experiment results."""
        run_id = args["run_id"]
        run_dir = _validated_run_dir(run_id)
        results_file = run_dir / "experiment_results.json"
        if results_file.exists():
            data = json.loads(results_file.read_text(encoding="utf-8"))
            return {"success": True, "results": data}
        return {"success": False, "error": "No results found"}

    async def _handle_search_literature(self, args: dict[str, Any]) -> dict[str, Any]:
        """Search literature through the shared literature module."""
        from researchclaw.literature.search import search_papers

        query = str(args["query"])
        limit = max(1, min(int(args.get("limit") or 10), 50))
        papers = search_papers(query, limit=limit)
        results = [
            paper.to_dict() if hasattr(paper, "to_dict") else dict(paper)
            for paper in papers
        ]
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def _handle_review_paper(self, args: dict[str, Any]) -> dict[str, Any]:
        """Review a generated paper with lightweight offline checks."""
        paper_path = Path(str(args["paper_path"]))
        if not paper_path.exists() or not paper_path.is_file():
            return {"success": False, "error": f"Paper not found: {paper_path}"}
        content = paper_path.read_text(encoding="utf-8", errors="replace")
        headings = re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", content)
        heading_norms = [_normalize_heading(heading) for heading in headings]
        missing_sections = [
            section
            for section, aliases in _REQUIRED_PAPER_SECTIONS.items()
            if not any(alias in heading_norms for alias in aliases)
        ]
        citations = re.findall(r"\[[0-9,\-\s]+\]|\\cite[tp]?\{[^}]+\}", content)
        issues: list[str] = []
        if missing_sections:
            issues.append("Missing core sections: " + ", ".join(missing_sections))
        if not citations:
            issues.append("No bracket or LaTeX citations detected")
        return {
            "success": True,
            "paper_path": str(paper_path),
            "review": {
                "word_count": len(re.findall(r"\b\w+\b", content)),
                "section_count": len(headings),
                "citation_count": len(citations),
                "missing_sections": missing_sections,
                "issues": issues,
            },
        }

    async def _handle_get_paper(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get generated paper."""
        run_id = args["run_id"]
        fmt = args.get("format", "markdown")
        run_dir = _validated_run_dir(run_id)
        if fmt == "latex":
            paper_file = run_dir / "paper.tex"
        else:
            paper_file = run_dir / "paper_draft.md"
        if paper_file.exists():
            return {"success": True, "content": paper_file.read_text(encoding="utf-8")}
        return {"success": False, "error": f"Paper not found in {run_dir}"}

    # ── server lifecycle ──────────────────────────────────────────

    async def start(self, transport: str = "stdio") -> None:
        """Start the MCP server (stdio or SSE transport)."""
        self._running = True
        logger.info("MCP server started (transport=%s)", transport)

    async def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False
        logger.info("MCP server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

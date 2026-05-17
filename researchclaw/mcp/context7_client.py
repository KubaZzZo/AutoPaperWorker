"""Context7 MCP client for library documentation discovery (F-01 Phase 3).

Connects to a Context7 MCP server via subprocess stdio to resolve library
identifiers and query up-to-date API documentation.

Usage::

    client = Context7MCPClient()
    lib_id = client.resolve_library_id("pytorch")
    docs = client.query_docs(lib_id, "nn.Module forward method")
    # Use ``docs`` in the caller's UI, logs, or generated context.
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

from researchclaw.utils.env import minimal_subprocess_env

logger = logging.getLogger(__name__)

# Common paths for Context7 MCP server
_CONTEXT7_BINARY_CANDIDATES = [
    "context7-mcp",
    "npx",
]

_CONTEXT7_DEFAULT_ARGS = [
    "-y", "@upstash/context7-mcp",
]


def _find_context7_binary() -> list[str] | None:
    """Find the Context7 MCP server binary. Returns command list or None."""
    # Try direct binary first
    direct = shutil.which("context7-mcp")
    if direct:
        return ["context7-mcp"]

    # Try npx with the package
    npx = shutil.which("npx")
    if npx:
        return ["npx"] + _CONTEXT7_DEFAULT_ARGS

    return None


class Context7MCPClient:
    """Client for the Context7 MCP server.

    Communicates via JSON-RPC over subprocess stdio. Gracefully degrades
    if the Context7 server is not installed or unavailable.

    Parameters
    ----------
    timeout_sec:
        Max seconds to wait for a response from the MCP server.
    max_content_chars:
        Max characters of documentation to return.
    """

    def __init__(
        self,
        timeout_sec: int = 30,
        max_content_chars: int = 8000,
    ) -> None:
        self._timeout = timeout_sec
        self._max_chars = max_content_chars
        self._proc: subprocess.Popen[bytes] | None = None
        self._available: bool | None = None  # tri-state: None=unknown, True/False

    @property
    def available(self) -> bool:
        """Check whether the Context7 MCP server can be started."""
        if self._available is not None:
            return self._available
        binary = _find_context7_binary()
        self._available = binary is not None
        if not self._available:
            logger.debug("Context7 MCP server not found (install: npx @upstash/context7-mcp)")
        return self._available

    def _ensure_started(self) -> bool:
        """Start the MCP server subprocess if not already running."""
        if self._proc is not None and self._proc.poll() is None:
            return True

        binary = _find_context7_binary()
        if not binary:
            self._available = False
            return False

        try:
            self._proc = subprocess.Popen(
                binary,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=minimal_subprocess_env(),
                start_new_session=True,
            )
            self._available = True

            # Send initialize request
            init_req = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ResearchClaw", "version": "0.5.0"},
                },
            })
            self._send(init_req)
            resp = self._recv(timeout=self._timeout, expected_id=1)
            if resp and "error" not in resp:
                logger.info("Context7 MCP server initialized")
                return True

            logger.debug("Context7 MCP initialize failed: %s", resp)
            self._stop()
            return False

        except Exception as exc:
            logger.debug("Context7 MCP start failed: %s", exc)
            self._available = False
            self._stop()
            return False

    def _send(self, message: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        data = message.encode("utf-8")
        self._proc.stdin.write(data + b"\n")
        self._proc.stdin.flush()

    def _readline_with_timeout(self, timeout: float) -> bytes | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        result_queue: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                result_queue.put(self._proc.stdout.readline())
            except BaseException as exc:  # pragma: no cover - defensive pipe edge
                result_queue.put(exc)

        thread = threading.Thread(target=read_line, daemon=True)
        thread.start()
        try:
            result = result_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(result, BaseException):
            raise result
        return result

    def _recv(
        self,
        timeout: int = 30,
        expected_id: int | None = None,
    ) -> dict[str, Any] | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self._readline_with_timeout(remaining)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                lines.append(decoded)
                try:
                    message = json.loads("\n".join(lines))
                    if expected_id is not None and message.get("id") != expected_id:
                        logger.debug(
                            "Context7 MCP ignored stale response id=%r expected=%r",
                            message.get("id"),
                            expected_id,
                        )
                        lines = []
                        continue
                    return message
                except json.JSONDecodeError:
                    continue
            except Exception:
                break
        return None

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Call a Context7 MCP tool and return the text result."""
        if not self._ensure_started():
            return None

        req_id = int(time.monotonic() * 1000) % 100000
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        })

        try:
            self._send(req)
            resp = self._recv(timeout=self._timeout, expected_id=req_id)
            if resp and "result" in resp:
                result = resp["result"]
                if isinstance(result, dict):
                    content = result.get("content", [])
                    if isinstance(content, list) and content:
                        texts = []
                        for item in content:
                            if isinstance(item, dict) and "text" in item:
                                texts.append(item["text"])
                        combined = "\n".join(texts)
                        return combined[:self._max_chars] if len(combined) > self._max_chars else combined
            if resp and "error" in resp:
                logger.debug("Context7 tool error: %s", resp["error"])
                return None
            if resp is None:
                logger.debug("Context7 MCP call timed out; restarting subprocess")
                self._stop()
        except Exception as exc:
            logger.debug("Context7 MCP call failed: %s", exc)
            self._stop()

        return None

    def resolve_library_id(self, name: str) -> str | None:
        """Resolve a library name to its Context7 library ID.

        Example: "pytorch" → "/pytorch/pytorch"
        """
        result = self._call_tool("resolve-library-id", {"libraryName": name})
        if result:
            return result.strip()
        logger.debug("Context7: could not resolve library id for '%s'", name)
        return None

    def query_docs(self, library_id: str, query: str) -> str | None:
        """Query Context7 for API documentation.

        Args:
            library_id: Context7 library ID (e.g., "/pytorch/pytorch").
            query: Natural language query about the API.
        """
        return self._call_tool("query-docs", {
            "libraryId": library_id,
            "query": query,
        })

    def query_framework_docs(
        self,
        framework_name: str,
        max_chars: int = 8000,
    ) -> str:
        """Convenience method: resolve + query a framework in one call.

        Returns documentation string or empty string on failure.
        """
        lib_id = self.resolve_library_id(framework_name)
        if not lib_id:
            return ""

        docs = self.query_docs(
            lib_id,
            f"Show the main API classes, training functions, and configuration "
            f"parameters for {framework_name}. Include code examples.",
        )
        if docs:
            if len(docs) > max_chars:
                docs = docs[:max_chars]
            return (
                f"\n## Context7 API Documentation: {framework_name}\n"
                f"{docs}\n"
            )
        return ""

    def _stop(self) -> None:
        """Stop the MCP server subprocess."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception as exc:
                logger.debug("Context7 MCP terminate failed: %s", exc, exc_info=True)
                try:
                    self._proc.kill()
                except Exception as kill_exc:
                    logger.debug(
                        "Context7 MCP kill failed: %s",
                        kill_exc,
                        exc_info=True,
                    )
            self._proc = None

    def close(self) -> None:
        """Clean up resources."""
        self._stop()

    def __del__(self) -> None:
        try:
            self._stop()
        except Exception:  # noqa: BLE001
            logger.debug("Context7 MCP __del__ cleanup failed", exc_info=True)

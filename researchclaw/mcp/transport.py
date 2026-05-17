"""MCP transport layer: stdio and SSE implementations."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import deque
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class MCPTransport(Protocol):
    """Protocol for MCP message transport."""

    async def send(self, message: dict[str, Any]) -> None: ...
    async def receive(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class StdioTransport:
    """MCP transport over stdin/stdout (for CLI integration)."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        """Initialize stdin/stdout streams for async I/O."""
        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(self._reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        self._writer = asyncio.StreamWriter(w_transport, w_protocol, self._reader, loop)

    async def send(self, message: dict[str, Any]) -> None:
        """Write a JSON-RPC message to stdout."""
        if self._writer is None:
            raise RuntimeError("Transport not started")
        data = json.dumps(message, ensure_ascii=False)
        header = f"Content-Length: {len(data.encode())}\r\n\r\n"
        self._writer.write(header.encode() + data.encode())
        await self._writer.drain()

    async def receive(self) -> dict[str, Any]:
        """Read a JSON-RPC message from stdin."""
        if self._reader is None:
            raise RuntimeError("Transport not started")
        # Read headers
        content_length = 0
        while True:
            line = await self._reader.readline()
            decoded = line.decode().strip()
            if not decoded:
                break
            if decoded.lower().startswith("content-length:"):
                content_length = int(decoded.split(":")[1].strip())
        if content_length == 0:
            raise EOFError("No content-length header received")
        body = await self._reader.readexactly(content_length)
        return json.loads(body)

    async def close(self) -> None:
        """Close the transport."""
        if self._writer:
            self._writer.close()


class SSETransport:
    """MCP transport over Server-Sent Events (for web integration).

    The in-process implementation stores outbound SSE frames and accepts
    inbound JSON-RPC messages via ``inject_message()``. A web server can wrap
    these queues with HTTP/SSE endpoints without changing MCP semantics.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3000,
        max_sent_events: int = 1000,
    ) -> None:
        self.host = host
        self.port = port
        self._running = False
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent_events: deque[str] = deque(maxlen=max_sent_events)

    async def start(self) -> None:
        """Start the SSE server."""
        self._running = True
        logger.info("SSE transport started on %s:%d", self.host, self.port)

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message as an SSE data frame."""
        if not self._running:
            raise RuntimeError("Transport not started")
        payload = json.dumps(message, ensure_ascii=False, default=str)
        frame = f"data: {payload}\n\n"
        self.sent_events.append(frame)
        logger.debug("SSE send: %s", payload[:200])

    async def receive(self) -> dict[str, Any]:
        """Receive the next injected JSON-RPC message."""
        if not self._running:
            raise RuntimeError("Transport not started")
        return await self._incoming.get()

    async def inject_message(self, message: dict[str, Any] | str | bytes) -> None:
        """Queue an incoming JSON-RPC message from HTTP request handling."""
        if not self._running:
            raise RuntimeError("Transport not started")
        if isinstance(message, bytes):
            message = message.decode()
        if isinstance(message, str):
            message = json.loads(message)
        await self._incoming.put(message)

    async def close(self) -> None:
        """Stop the SSE server."""
        self._running = False
        while not self._incoming.empty():
            _ = self._incoming.get_nowait()

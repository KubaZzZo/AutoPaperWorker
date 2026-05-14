"""Lightweight in-memory rate limiting middleware."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limit state-changing API requests per client IP."""

    LIMITED_PATHS = frozenset({"/api/pipeline/start"})

    def __init__(
        self,
        app: object,
        *,
        max_requests: int = 30,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_requests = max(1, int(max_requests))
        self._window_seconds = max(1, int(window_seconds))
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        if request.url.path not in self.LIMITED_PATHS:
            return await call_next(request)

        now = time.monotonic()
        key = self._client_key(request)
        hits = self._hits[key]
        cutoff = now - self._window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self._max_requests:
            retry_after = max(1, int(self._window_seconds - (now - hits[0])))
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        return await call_next(request)

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded_for:
            return forwarded_for
        return request.client.host if request.client else "unknown"

"""Basic token authentication middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

WEBSOCKET_UNAUTHORIZED_CODE = 4001


def extract_auth_token(auth_header: str) -> str:
    """Extract a token from an Authorization header value."""
    value = (auth_header or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def token_matches(candidate: str, expected: str) -> bool:
    """Return True when a presented token matches the configured token."""
    return bool(expected) and candidate == expected


def request_token(request: Request) -> str:
    """Extract auth token from HTTP headers."""
    return extract_auth_token(request.headers.get("authorization", ""))


def websocket_token(websocket: WebSocket) -> str:
    """Extract auth token from WebSocket headers."""
    return extract_auth_token(websocket.headers.get("authorization", ""))


def websocket_expected_token(websocket: WebSocket) -> str:
    """Read the configured app auth token for a WebSocket request."""
    app = websocket.scope.get("app") if hasattr(websocket, "scope") else None
    state = getattr(app, "state", None)
    return str(getattr(state, "auth_token", "") or "")


async def require_websocket_token(websocket: WebSocket) -> bool:
    """Close unauthorized WebSockets and return whether they may continue."""
    expected = websocket_expected_token(websocket)
    if not expected:
        # Direct unit tests and intentionally unauthenticated ad hoc apps do
        # not carry app.state.auth_token.
        return True
    if token_matches(websocket_token(websocket), expected):
        return True
    await websocket.close(code=WEBSOCKET_UNAUTHORIZED_CODE)
    return False


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token authentication for HTTP requests."""

    # Paths that never require auth
    EXEMPT_PATHS = frozenset({"/api/health", "/docs", "/openapi.json"})

    def __init__(self, app: object, token: str = "") -> None:
        if not token:
            raise ValueError("TokenAuthMiddleware requires a non-empty token")
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Skip auth for exempt paths and static files
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.startswith("/static"):
            return await call_next(request)

        if not token_matches(request_token(request), self._token):
            return JSONResponse(
                {"detail": "Unauthorized"}, status_code=401
            )

        return await call_next(request)

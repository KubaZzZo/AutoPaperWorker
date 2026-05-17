"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from researchclaw import __version__
from researchclaw.config import RCConfig
from researchclaw.server.middleware.auth import TokenAuthMiddleware, require_websocket_token
from researchclaw.server.middleware.rate_limit import RateLimitMiddleware
from researchclaw.server.websocket.manager import ConnectionManager

logger = logging.getLogger(__name__)

# Shared application state accessible by routes
_app_state: dict[str, Any] = {}


def create_app(
    config: RCConfig,
    *,
    dashboard_only: bool = False,
    monitor_dir: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: ResearchClaw configuration.
        dashboard_only: If True, only mount dashboard routes.
        monitor_dir: Specific run directory to monitor.
    """
    event_manager = ConnectionManager()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        background_tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(event_manager.heartbeat_loop(interval=15.0))
        ]

        if config.dashboard.enabled:
            from researchclaw.dashboard.broadcaster import start_dashboard_loop

            background_tasks.append(
                asyncio.create_task(
                    start_dashboard_loop(
                        event_manager,
                        interval=config.dashboard.refresh_interval_sec,
                        monitor_dir=monitor_dir,
                    )
                )
            )
        logger.info("ResearchClaw Web server started")

        try:
            yield
        finally:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="ResearchClaw",
        description="Autonomous Research Pipeline — Web Interface",
        version=__version__,
        lifespan=lifespan,
    )

    # Store config in shared state
    _app_state["config"] = config
    _app_state["monitor_dir"] = monitor_dir
    app.state.auth_token = config.server.auth_token

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.server.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # --- Token auth ---
    app.add_middleware(TokenAuthMiddleware, token=config.server.auth_token)

    # --- Rate limiting for state-changing control endpoints ---
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=config.server.rate_limit_requests,
        window_seconds=config.server.rate_limit_window_sec,
        trusted_proxy_ips=config.server.trusted_proxy_ips,
    )

    # --- WebSocket manager ---
    _app_state["event_manager"] = event_manager

    # --- Health endpoint ---
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "active_connections": event_manager.active_count,
        }

    @app.get("/api/config")
    async def config_summary() -> dict[str, Any]:
        return {
            "version": __version__,
            "features": {
                "voice_enabled": config.server.voice_enabled,
                "dashboard_enabled": config.dashboard.enabled,
            },
        }

    # --- Routes ---
    from researchclaw.server.routes.pipeline import router as pipeline_router
    from researchclaw.server.routes.projects import router as projects_router

    app.include_router(pipeline_router)
    app.include_router(projects_router)

    if not dashboard_only:
        from researchclaw.server.routes.chat import router as chat_router
        from researchclaw.server.routes.chat import set_chat_manager

        set_chat_manager(event_manager)
        app.include_router(chat_router)

        if config.server.voice_enabled:
            from researchclaw.server.routes.voice import router as voice_router

            app.include_router(voice_router)

    # --- WebSocket events endpoint ---
    import uuid

    @app.websocket("/ws/events")
    async def events_ws(websocket: WebSocket) -> None:
        """Real-time event stream for dashboard."""
        if not await require_websocket_token(websocket):
            return
        client_id = f"evt-{uuid.uuid4().hex[:8]}"
        await event_manager.connect(websocket, client_id)
        try:
            while True:
                # Keep connection alive; client can send pings
                await websocket.receive_text()
        except WebSocketDisconnect:
            event_manager.disconnect(client_id)

    # --- Static files (frontend) ---
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

        # Serve index.html at root
        from fastapi.responses import FileResponse

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(str(frontend_dir / "index.html"))

    return app

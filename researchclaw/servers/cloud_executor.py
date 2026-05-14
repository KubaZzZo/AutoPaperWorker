"""Cloud executor: guarded interface for cloud GPU instance management."""

from __future__ import annotations

import logging
from typing import Any

from researchclaw.servers.registry import ServerEntry

logger = logging.getLogger(__name__)


class CloudExecutor:
    """Manage cloud GPU instances for experiment execution.

    Provider SDK backends are intentionally not imported unless a concrete
    implementation is configured. The ``host="dry-run"`` mode returns a launch
    plan without contacting a cloud API; all other modes fail explicitly until a
    provider backend is wired.
    """

    def __init__(self, server: ServerEntry) -> None:
        if server.server_type != "cloud":
            raise ValueError(f"Server {server.name} is not a cloud server")
        self.server = server
        self.provider = server.cloud_provider

    async def launch_instance(self) -> dict[str, Any]:
        """Launch a cloud GPU instance."""
        logger.info(
            "Launching %s instance (%s) for %s",
            self.provider,
            self.server.cloud_instance_type,
            self.server.name,
        )
        if self.server.host == "dry-run":
            return {
                "provider": self.provider,
                "instance_type": self.server.cloud_instance_type,
                "status": "planned",
                "instance_id": f"planned-{self.server.name}",
                "cost_per_hour": self.server.cost_per_hour,
            }
        self._raise_unsupported_backend("launch_instance")

    async def terminate_instance(self, instance_id: str) -> None:
        """Terminate a cloud instance."""
        logger.info("Terminating instance %s on %s", instance_id, self.provider)
        if self.server.host == "dry-run":
            return
        self._raise_unsupported_backend("terminate_instance")

    async def get_instance_status(self, instance_id: str) -> dict[str, Any]:
        """Check instance status."""
        if self.server.host == "dry-run":
            return {"instance_id": instance_id, "status": "planned"}
        self._raise_unsupported_backend("get_instance_status")

    def _raise_unsupported_backend(self, operation: str) -> None:
        provider = self.provider or "unknown"
        message = (
            f"Cloud provider backend is not configured for {provider!r} "
            f"operation {operation!r}; use host='dry-run' to generate a plan."
        )
        logger.warning(message)
        raise NotImplementedError(message)

"""Factory for creating sandbox backends based on experiment config."""

from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import replace
from typing import TYPE_CHECKING

from researchclaw.config import ExperimentConfig
from researchclaw.exceptions import SandboxConfigurationError
from researchclaw.experiment.sandbox import ExperimentSandbox, SandboxProtocol

if TYPE_CHECKING:
    from researchclaw.experiment.agentic_sandbox import AgenticSandbox

logger = logging.getLogger(__name__)


def create_sandbox(config: ExperimentConfig, workdir: Path) -> SandboxProtocol:
    """Return the appropriate sandbox backend for *config.mode*.

    - ``"sandbox"`` → :class:`ExperimentSandbox` (subprocess)
    - ``"docker"``  → :class:`DockerSandbox`  (Docker container)
    """
    if config.mode == "docker":
        from researchclaw.experiment.docker_sandbox import DockerSandbox

        docker_cfg = replace(config.docker, distributed=config.distributed)

        if not DockerSandbox.check_docker_available():
            logger.warning(
                "Docker daemon is not reachable — "
                "falling back to subprocess sandbox."
            )
            return ExperimentSandbox(config.sandbox, workdir)

        if not DockerSandbox.ensure_image(docker_cfg.image):
            raise SandboxConfigurationError(
                f"Docker image '{docker_cfg.image}' not found locally. "
                f"Build it: docker build -t {docker_cfg.image} researchclaw/docker/"
            )

        if docker_cfg.gpu_enabled:
            logger.info("Docker sandbox: GPU passthrough enabled")

        return DockerSandbox(docker_cfg, workdir)

    if config.mode == "ssh_remote":
        from researchclaw.experiment.ssh_sandbox import SshRemoteSandbox

        ssh_cfg = replace(config.ssh_remote, distributed=config.distributed)
        if not ssh_cfg.host:
            raise SandboxConfigurationError(
                "ssh_remote mode requires experiment.ssh_remote.host in config."
            )
        if not ssh_cfg.use_docker:
            raise SandboxConfigurationError(
                "ssh_remote mode requires Docker execution; set "
                "experiment.ssh_remote.use_docker: true."
            )

        ok, msg = SshRemoteSandbox.check_ssh_available(ssh_cfg)
        if not ok:
            raise SandboxConfigurationError(f"SSH connectivity check failed: {msg}")

        logger.info("SSH remote sandbox: %s", msg)
        return SshRemoteSandbox(ssh_cfg, workdir)

    if config.mode == "colab_drive":
        from researchclaw.experiment.colab_sandbox import ColabDriveSandbox

        colab_cfg = config.colab_drive
        ok, msg = ColabDriveSandbox.check_drive_available(colab_cfg)
        if not ok:
            raise SandboxConfigurationError(f"Colab Drive check failed: {msg}")

        logger.info("Colab Drive sandbox: %s", msg)

        # Write worker template for user convenience
        worker_path = Path(colab_cfg.drive_root).expanduser() / "colab_worker.py"
        if not worker_path.exists():
            ColabDriveSandbox.write_worker_notebook(worker_path)
            logger.info(
                "Colab worker template written to %s — "
                "upload this to Colab and run it.",
                worker_path,
            )

        return ColabDriveSandbox(colab_cfg, workdir)

    if config.mode != "sandbox":
        raise SandboxConfigurationError(
            f"Unsupported experiment mode for create_sandbox(): {config.mode}"
        )

    return ExperimentSandbox(config.sandbox, workdir)


def create_agentic_sandbox(
    config: ExperimentConfig,
    workdir: Path,
    skills_dir: Path | None = None,
) -> "AgenticSandbox":  # noqa: F821
    """Return an :class:`AgenticSandbox` for agentic experiment mode.

    Validates that Docker is available before returning.
    """
    from researchclaw.experiment.agentic_sandbox import AgenticSandbox

    if not AgenticSandbox.check_docker_available():
        raise SandboxConfigurationError(
            "Docker daemon is not reachable. "
            "Agentic mode requires Docker. Start Docker first."
        )

    agentic_cfg = config.agentic
    if agentic_cfg.gpu_enabled:
        logger.info("Agentic sandbox: GPU passthrough enabled")

    return AgenticSandbox(agentic_cfg, workdir, skills_dir=skills_dir)

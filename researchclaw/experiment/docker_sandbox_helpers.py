"""Shared helpers for Docker-based experiment sandbox execution."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from researchclaw.config import DistributedTrainingConfig

logger = logging.getLogger("researchclaw.experiment.docker_sandbox")

_CONTAINER_COUNTER = 0
_counter_lock = threading.Lock()


def _next_container_name() -> str:
    global _CONTAINER_COUNTER  # noqa: PLW0603
    with _counter_lock:
        _CONTAINER_COUNTER += 1
        return f"rc-exp-{_CONTAINER_COUNTER}-{os.getpid()}"


def _docker_mount_path(path: Path) -> str:
    """Return a Docker bind-mount path suitable for the current Windows mode."""

    raw = str(path)
    if sys.platform != "win32":
        return raw
    if not (os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME")):
        return raw

    resolved = str(Path(raw))
    drive, tail = os.path.splitdrive(resolved)
    if not drive:
        return resolved.replace("\\", "/")
    drive_letter = drive.rstrip(":").lower()
    tail = tail.replace("\\", "/").lstrip("/")
    return f"/mnt/{drive_letter}/{tail}"


def _decode_subprocess_output(value: bytes | str | None) -> str:
    """Decode subprocess output consistently with other sandbox backends."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


_SENSITIVE_ENV_NAMES = frozenset({
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
})


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def _redact_docker_command(cmd: list[str]) -> list[str]:
    """Return a log-safe copy of a Docker command."""
    redacted: list[str] = []
    for part in cmd:
        if "=" in part:
            name, value = part.split("=", 1)
            if name in _SENSITIVE_ENV_NAMES:
                redacted.append(f"{name}={_mask_secret(value)}")
                continue
        redacted.append(part)
    return redacted


_BUILTIN_PACKAGES = {
    "torch", "torchvision", "torchaudio", "torchdiffeq",
    "numpy", "scipy", "sklearn", "pandas", "matplotlib", "seaborn",
    "tqdm", "gymnasium", "networkx",
    "timm", "einops", "torchmetrics", "albumentations", "kornia",
    "h5py", "tensorboard",
    "transformers", "datasets", "accelerate", "peft", "trl",
    "bitsandbytes", "sentencepiece", "protobuf", "tokenizers",
    "safetensors", "evaluate",
    "yaml", "PIL", "mujoco",
    "os", "sys", "math", "random", "json", "csv", "re", "time",
    "collections", "itertools", "functools", "pathlib", "typing",
    "dataclasses", "abc", "copy", "io", "logging", "argparse",
    "datetime", "hashlib", "pickle", "subprocess", "shutil",
    "tempfile", "warnings", "unittest", "contextlib", "operator",
    "string", "textwrap", "struct", "statistics", "glob",
    "urllib", "http", "email", "html", "xml",
}


_IMPORT_TO_PIP = {
    "torchdiffeq": "torchdiffeq",
    "torch_geometric": "torch-geometric",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "gym": "gymnasium",
    "ogb": "ogb",
    "dgl": "dgl",
    "lightning": "lightning",
    "pytorch_lightning": "pytorch-lightning",
    "wandb": "wandb",
    "optuna": "optuna",
}


def _distributed_launcher_args(
    distributed: DistributedTrainingConfig,
    entry_point: str,
) -> list[str]:
    """Return argv for the configured distributed launcher."""
    if not distributed.enabled:
        return [entry_point]

    launcher = distributed.launcher.strip().lower()
    num_nodes = max(1, int(distributed.num_nodes))
    gpus_per_node = max(1, int(distributed.gpus_per_node))

    if launcher == "torchrun":
        return [
            "torchrun",
            f"--nnodes={num_nodes}",
            f"--nproc_per_node={gpus_per_node}",
            entry_point,
        ]
    if launcher == "accelerate":
        return [
            "accelerate",
            "launch",
            f"--num_processes={num_nodes * gpus_per_node}",
            entry_point,
        ]
    if launcher == "deepspeed":
        return ["deepspeed", f"--num_gpus={gpus_per_node}", entry_point]

    logger.warning(
        "Unsupported distributed launcher %r; falling back to python entry point.",
        distributed.launcher,
    )
    return [entry_point]


__all__ = [
    "_BUILTIN_PACKAGES",
    "_IMPORT_TO_PIP",
    "_decode_subprocess_output",
    "_distributed_launcher_args",
    "_docker_mount_path",
    "_next_container_name",
    "_redact_docker_command",
]

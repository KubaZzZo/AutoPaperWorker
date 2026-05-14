from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from researchclaw.adapters import AdapterBundle
from researchclaw.pipeline.progress import utcnow_iso
from researchclaw.pipeline.stages import STAGE_SEQUENCE, Stage

logger = logging.getLogger(__name__)


def write_checkpoint(
    run_dir: Path,
    stage: Stage,
    run_id: str,
    adapters: AdapterBundle | None = None,
) -> None:
    """Write checkpoint atomically via temp file + rename to prevent corruption."""
    checkpoint: dict[str, object] = {
        "last_completed_stage": int(stage),
        "last_completed_name": stage.name,
        "run_id": run_id,
        "timestamp": utcnow_iso(),
    }

    if adapters is not None:
        hitl_session = getattr(adapters, "hitl", None)
        if hitl_session is not None:
            try:
                checkpoint["hitl"] = hitl_session.hitl_checkpoint_data()
            except Exception:
                logger.warning("HITL checkpoint data collection failed", exc_info=True)

    target = run_dir / "checkpoint.json"
    fd, tmp_path = tempfile.mkstemp(dir=run_dir, suffix=".tmp", prefix="checkpoint_")
    os.close(fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(checkpoint, indent=2))
        os.chmod(tmp_path, 0o644)
        Path(tmp_path).replace(target)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def write_heartbeat(run_dir: Path, stage: Stage, run_id: str) -> None:
    """Write heartbeat file for sentinel watchdog monitoring."""
    heartbeat = {
        "pid": os.getpid(),
        "last_stage": int(stage),
        "last_stage_name": stage.name,
        "run_id": run_id,
        "timestamp": utcnow_iso(),
    }
    (run_dir / "heartbeat.json").write_text(
        json.dumps(heartbeat, indent=2), encoding="utf-8"
    )


def read_checkpoint(run_dir: Path) -> Stage | None:
    """Read checkpoint and return the NEXT stage to execute, or None if no checkpoint."""
    cp_path = run_dir / "checkpoint.json"
    if not cp_path.exists():
        return None
    try:
        data = json.loads(cp_path.read_text(encoding="utf-8"))
        last_num = data.get("last_completed_stage")
        if last_num is None:
            return None
        for i, stage in enumerate(STAGE_SEQUENCE):
            if int(stage) == last_num:
                if i + 1 < len(STAGE_SEQUENCE):
                    return STAGE_SEQUENCE[i + 1]
                return None
        return None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def resume_from_checkpoint(
    run_dir: Path, default_stage: Stage = Stage.TOPIC_INIT
) -> Stage:
    """Resolve the stage to resume from using checkpoint metadata."""
    next_stage = read_checkpoint(run_dir)
    return next_stage or default_stage

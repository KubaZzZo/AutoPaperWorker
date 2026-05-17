"""Resource and import-surface safety checks."""

from __future__ import annotations

import inspect
from pathlib import Path

from researchclaw.llm.acp_client import ACPClient
from researchclaw.mcp.context7_client import Context7MCPClient


def test_acp_live_instances_are_guarded_by_lock() -> None:
    source = inspect.getsource(ACPClient.__init__)
    assert "_live_instances_lock" in source
    assert "with ACPClient._live_instances_lock:" in source
    assert hasattr(ACPClient, "_live_instances_lock")


def test_context7_del_swallows_and_logs_cleanup_errors() -> None:
    source = inspect.getsource(Context7MCPClient.__del__)
    assert "try:" in source
    assert "except Exception" in source
    assert "logger.debug" in source


def test_review_publish_stage_modules_do_not_use_f401_reexport_comments() -> None:
    offenders = []
    for path in [
        Path("researchclaw/pipeline/stage_impls/_publish.py"),
        Path("researchclaw/pipeline/stage_impls/_review.py"),
        Path("researchclaw/pipeline/stage_impls/_revision.py"),
    ]:
        text = path.read_text(encoding="utf-8")
        if "noqa: F401" in text:
            offenders.append(str(path))
    assert offenders == []

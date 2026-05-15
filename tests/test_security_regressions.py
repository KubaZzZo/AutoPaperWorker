"""Security regression tests for cross-cutting hardening rules."""

from __future__ import annotations

from pathlib import Path


def test_ssh_code_does_not_disable_host_key_checking() -> None:
    production_sources = [
        path
        for path in Path("researchclaw").rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    offenders: list[str] = []
    for path in production_sources:
        text = path.read_text(encoding="utf-8")
        if "StrictHostKeyChecking=no" in text or "AutoAddPolicy" in text:
            offenders.append(str(path))

    assert offenders == []


def test_docker_sandbox_does_not_grant_net_admin() -> None:
    text = Path("researchclaw/experiment/docker_sandbox.py").read_text(encoding="utf-8")

    assert "NET_ADMIN" not in text
    assert "--cap-add" not in text


def test_web_docs_do_not_embed_tavily_api_key_examples() -> None:
    offenders: list[str] = []
    for path in Path("researchclaw/web").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "tvly-" in text:
            offenders.append(str(path))

    assert offenders == []

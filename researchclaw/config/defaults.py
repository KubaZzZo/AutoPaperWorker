"""Default values and validation constants for ResearchClaw configuration."""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_PYTHON_PATH = (
    ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python3"
)

CONFIG_SEARCH_ORDER: tuple[str, ...] = ("config.arc.yaml", "config.yaml")
DEFAULT_ARTIFACTS_DIR = Path("artifacts")
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:8080",
    "http://localhost:8080",
)
MAX_CONFIG_NESTING_DEPTH = 64
EXAMPLE_CONFIG = "config.researchclaw.example.yaml"

REQUIRED_FIELDS = (
    "project.name",
    "research.topic",
    "runtime.timezone",
    "notifications.channel",
    "knowledge_base.root",
    "llm.base_url",
    "llm.api_key_env",
)
KB_SUBDIRS = (
    "questions",
    "literature",
    "experiments",
    "findings",
    "decisions",
    "reviews",
)
PROJECT_MODES = {"docs-first", "semi-auto", "full-auto"}
KB_BACKENDS = {"markdown", "obsidian"}
EXPERIMENT_MODES = {
    "simulated",
    "sandbox",
    "docker",
    "ssh_remote",
    "colab_drive",
    "agentic",
}
CLI_AGENT_PROVIDERS = {"llm", "claude_code", "codex"}
VALID_NETWORK_POLICIES = {"none", "setup_only", "pip_only", "full"}

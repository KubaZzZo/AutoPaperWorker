# -*- coding: utf-8 -*-
"""Computer-science graduation project helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectAnalysis:
    root: Path
    project_type: str
    languages: tuple[str, ...]
    file_count: int
    suggested_sections: tuple[str, ...]


def classify_graduation_project(topic: str) -> str:
    """Classify a CS graduation project topic into a coarse workflow type."""
    text = topic.lower()
    algorithm_terms = (
        "深度学习",
        "机器学习",
        "图像识别",
        "目标检测",
        "算法",
        "模型",
        "推荐",
        "nlp",
        "llm",
        "aigc",
    )
    system_terms = (
        "系统",
        "平台",
        "管理",
        "网站",
        "小程序",
        "app",
        "设计与实现",
        "开发与实现",
        "软件",
    )
    if any(term in text for term in algorithm_terms):
        return "algorithm"
    if any(term in text for term in system_terms):
        return "system"
    return "tool"


def analyze_project(root: str | Path) -> ProjectAnalysis:
    """Read-only analysis of an existing code project."""
    project_root = Path(root)
    files = [p for p in project_root.rglob("*") if p.is_file() and _is_interesting(p)]
    languages = tuple(sorted({_language_for(p) for p in files if _language_for(p)}))
    return ProjectAnalysis(
        root=project_root,
        project_type=_project_type_from_files(files),
        languages=languages,
        file_count=len(files),
        suggested_sections=(
            "需求分析",
            "系统总体设计",
            "核心模块实现",
            "测试与结果分析",
            "部署与运行说明",
        ),
    )


def create_project_plan(topic: str) -> dict[str, object]:
    """Return a small, runnable-first project plan for a CS graduation topic."""
    project_type = classify_graduation_project(topic)
    if project_type == "algorithm":
        modules = ("data", "model", "train", "evaluate", "reports")
    elif project_type == "system":
        modules = ("frontend", "backend", "database", "api", "tests")
    else:
        modules = ("core", "cli", "docs", "tests")
    return {
        "topic": topic,
        "project_type": project_type,
        "modules": modules,
        "principle": "先生成最小可运行版本，再扩展功能和论文素材。",
    }


def _is_interesting(path: Path) -> bool:
    return path.suffix.lower() in {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".md",
        ".yaml",
        ".yml",
        ".json",
    }


def _language_for(path: Path) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
    }.get(path.suffix.lower(), "")


def _project_type_from_files(files: list[Path]) -> str:
    names = {p.name.lower() for p in files}
    if {"package.json", "vite.config.ts", "next.config.js"} & names:
        return "system"
    if any(p.name.lower() in {"train.py", "model.py", "evaluate.py"} for p in files):
        return "algorithm"
    return "tool"

"""Parsing and loading helpers for ResearchClaw configuration."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import yaml

from researchclaw.config.defaults import (
    CONFIG_SEARCH_ORDER,
    DEFAULT_CORS_ORIGINS,
    DEFAULT_PYTHON_PATH,
    VALID_NETWORK_POLICIES,
)
from researchclaw.config.schema import (
    AcpConfig,
    AgenticConfig,
    BenchmarkAgentConfig,
    CalendarConfig,
    CliAgentConfig,
    CodeAgentConfig,
    ColabDriveConfig,
    CoPilotConfig,
    DashboardConfig,
    DistributedTrainingConfig,
    DockerSandboxConfig,
    ExperimentConfig,
    ExperimentRepairConfig,
    ExportConfig,
    FigureAgentConfig,
    KnowledgeBaseConfig,
    KnowledgeGraphConfig,
    LlmConfig,
    MCPIntegrationConfig,
    MemoryConfig,
    MetaClawBridgeConfig,
    MetaClawLessonToSkillConfig,
    MetaClawPRMConfig,
    MultiProjectConfig,
    NotificationsConfig,
    OpenClawBridgeConfig,
    OpenCodeConfig,
    OverleafConfig,
    ParallelHypothesesConfig,
    ProjectConfig,
    PromptsConfig,
    QualityAssessorConfig,
    RCConfig,
    ResearchConfig,
    RuntimeConfig,
    SandboxConfig,
    SecurityConfig,
    ServerConfig,
    ServerEntryConfig,
    ServersConfig,
    SkillsConfig,
    SshRemoteConfig,
    TrendsConfig,
    WebSearchConfig,
)
from researchclaw.config.validation import validate_config
from researchclaw.exceptions import ConfigValidationError


def _safe_int(val: Any, default: int) -> int:
    """Convert value to int, handling None/null YAML values."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float) -> float:
    """Convert value to float, handling None/null YAML values.

    BUG-DA8-11: Also rejects NaN/Inf which YAML can produce via .nan/.inf.
    """
    if val is None:
        return default
    try:
        import math

        result = float(val)
        if not math.isfinite(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def _validate_network_policy(val: object, default: str = "setup_only") -> str:
    """Validate network_policy, falling back to *default* on bad values."""
    s = str(val).strip().lower() if val else default
    if s not in VALID_NETWORK_POLICIES:
        import logging as _cfg_log

        _cfg_log.getLogger(__name__).warning(
            "Invalid network_policy %r, using %r",
            val,
            default,
        )
        return default
    return s


def resolve_config_path(explicit: str | None) -> Path | None:
    """Return first existing config from search order, or explicit path if given."""
    if explicit is not None:
        return Path(explicit)
    for name in CONFIG_SEARCH_ORDER:
        candidate = Path(name)
        if candidate.exists():
            return candidate
    return None


def build_config_from_dict(
    config_cls: type[RCConfig],
    data: dict[str, Any],
    *,
    project_root: Path | None = None,
    check_paths: bool = True,
) -> RCConfig:
    result = validate_config(
        data, project_root=project_root, check_paths=check_paths
    )
    if not result.ok:
        raise ConfigValidationError("; ".join(result.errors))

    project = data["project"]
    research = data["research"]
    runtime = data["runtime"]
    notifications = data["notifications"]
    knowledge_base = data["knowledge_base"]
    bridge = data.get("openclaw_bridge") or {}
    llm = data["llm"]
    security = data.get("security") or {}
    experiment = data.get("experiment") or {}
    export = data.get("export") or {}
    prompts = data.get("prompts") or {}
    web_search = data.get("web_search") or {}
    metaclaw = data.get("metaclaw_bridge") or {}
    memory_data = data.get("memory") or {}
    skills_data = data.get("skills") or {}
    knowledge_graph_data = data.get("knowledge_graph") or {}
    multi_project = data.get("multi_project") or {}
    compute_servers = data.get("compute_servers") or {}
    mcp_data = data.get("mcp") or {}
    overleaf = data.get("overleaf") or {}
    server = data.get("server") or {}
    dashboard_data = data.get("dashboard") or {}
    trends_data = data.get("trends") or {}
    copilot_data = data.get("copilot") or {}
    quality_assessor_data = data.get("quality_assessor") or {}
    calendar_data = data.get("calendar") or {}
    hitl_data = data.get("hitl") or {}

    return config_cls(
        project=ProjectConfig(
            name=project["name"], mode=project.get("mode", "docs-first")
        ),
        research=ResearchConfig(
            topic=research["topic"],
            domains=tuple(research.get("domains") or ()),
            daily_paper_count=int(research.get("daily_paper_count", 0)),
            quality_threshold=float(research.get("quality_threshold", 0.0)),
            graceful_degradation=bool(research.get("graceful_degradation", True)),
        ),
        runtime=RuntimeConfig(
            timezone=runtime["timezone"],
            max_parallel_tasks=int(runtime.get("max_parallel_tasks", 1)),
            approval_timeout_hours=int(runtime.get("approval_timeout_hours", 12)),
            retry_limit=int(runtime.get("retry_limit", 0)),
        ),
        notifications=NotificationsConfig(
            channel=notifications["channel"],
            target=notifications.get("target", ""),
            on_stage_start=bool(notifications.get("on_stage_start", False)),
            on_stage_fail=bool(notifications.get("on_stage_fail", False)),
            on_gate_required=bool(notifications.get("on_gate_required", True)),
        ),
        knowledge_base=KnowledgeBaseConfig(
            backend=knowledge_base.get("backend", "markdown"),
            root=knowledge_base["root"],
            obsidian_vault=knowledge_base.get("obsidian_vault", ""),
        ),
        openclaw_bridge=OpenClawBridgeConfig(
            use_cron=bool(bridge.get("use_cron", False)),
            use_message=bool(bridge.get("use_message", False)),
            use_memory=bool(bridge.get("use_memory", False)),
            use_sessions_spawn=bool(bridge.get("use_sessions_spawn", False)),
            use_web_fetch=bool(bridge.get("use_web_fetch", False)),
            use_browser=bool(bridge.get("use_browser", False)),
        ),
        llm=_parse_llm_config(llm),
        security=SecurityConfig(
            hitl_required_stages=tuple(
                int(s) for s in security.get("hitl_required_stages", (5, 9, 20))
            ),
            allow_publish_without_approval=bool(
                security.get("allow_publish_without_approval", False)
            ),
            redact_sensitive_logs=bool(security.get("redact_sensitive_logs", True)),
        ),
        experiment=_parse_experiment_config(experiment),
        export=ExportConfig(
            target_conference=export.get("target_conference", "neurips_2025"),
            authors=export.get("authors", "Anonymous"),
            paper_language=export.get("paper_language", "English"),
            bib_file=export.get("bib_file", "references"),
        ),
        prompts=PromptsConfig(
            custom_file=prompts.get("custom_file", ""),
        ),
        web_search=WebSearchConfig(
            enabled=bool(web_search.get("enabled", True)),
            tavily_api_key=str(web_search.get("tavily_api_key", "")),
            tavily_api_key_env=str(
                web_search.get("tavily_api_key_env", "TAVILY_API_KEY")
            ),
            enable_scholar=bool(web_search.get("enable_scholar", True)),
            enable_crawling=bool(web_search.get("enable_crawling", True)),
            enable_pdf_extraction=bool(
                web_search.get("enable_pdf_extraction", True)
            ),
            max_web_results=int(web_search.get("max_web_results", 10)),
            max_scholar_results=int(web_search.get("max_scholar_results", 10)),
            max_crawl_urls=int(web_search.get("max_crawl_urls", 5)),
        ),
        metaclaw_bridge=_parse_metaclaw_bridge_config(metaclaw),
        memory=_parse_memory_config(memory_data),
        skills=_parse_skills_config(skills_data),
        knowledge_graph=_parse_knowledge_graph_config(knowledge_graph_data),
        multi_project=_parse_multi_project_config(multi_project),
        compute_servers=_parse_servers_config(compute_servers),
        mcp=_parse_mcp_config(mcp_data),
        overleaf=_parse_overleaf_config(overleaf),
        server=_parse_server_config(server),
        dashboard=_parse_dashboard_config(dashboard_data),
        trends=_parse_trends_config(trends_data),
        copilot=_parse_copilot_config(copilot_data),
        quality_assessor=_parse_quality_assessor_config(quality_assessor_data),
        calendar=_parse_calendar_config(calendar_data),
        hitl=_parse_hitl_config(hitl_data),
    )



def _parse_llm_config(data: dict[str, Any]) -> LlmConfig:
    acp_data = data.get("acp") or {}
    return LlmConfig(
        provider=data.get("provider", "openai-compatible"),
        base_url=data.get("base_url", ""),
        wire_api=data.get("wire_api", "chat_completions"),
        api_key_env=data.get("api_key_env", ""),
        api_key="",
        primary_model=data.get("primary_model", ""),
        fallback_models=tuple(data.get("fallback_models") or ()),
        s2_api_key=data.get("s2_api_key", ""),
        notes=data.get("notes", ""),
        timeout_sec=_safe_int(data.get("timeout_sec"), 600),
        acp=AcpConfig(
            agent=acp_data.get("agent", "claude"),
            cwd=acp_data.get("cwd", "."),
            acpx_command=acp_data.get("acpx_command", ""),
            session_name=acp_data.get("session_name", "researchclaw"),
            timeout_sec=int(acp_data.get("timeout_sec", 1800)),
        ),
    )


def _parse_agentic_config(data: dict[str, Any]) -> AgenticConfig:
    if not data:
        return AgenticConfig()
    return AgenticConfig(
        image=data.get("image", "researchclaw/experiment:latest"),
        agent_cli=data.get("agent_cli", "claude"),
        agent_install_cmd=data.get(
            "agent_install_cmd", "npm install -g @anthropic-ai/claude-code"
        ),
        network_policy=data.get("network_policy", "full"),
        timeout_sec=int(data.get("timeout_sec", 1800)),
        memory_limit_mb=int(data.get("memory_limit_mb", 8192)),
        gpu_enabled=bool(data.get("gpu_enabled", False)),
        mount_skills=bool(data.get("mount_skills", True)),
        allow_shell_commands=bool(data.get("allow_shell_commands", True)),
        max_turns=int(data.get("max_turns", 50)),
    )


def _parse_experiment_config(data: dict[str, Any]) -> ExperimentConfig:
    sandbox_data = data.get("sandbox") or {}
    docker_data = data.get("docker") or {}
    distributed_data = data.get("distributed") or {}
    parallel_hypotheses_data = data.get("parallel_hypotheses") or {}
    ssh_data = data.get("ssh_remote") or {}
    colab_data = data.get("colab_drive") or {}
    return ExperimentConfig(
        mode=data.get("mode", "simulated"),
        time_budget_sec=_safe_int(data.get("time_budget_sec"), 300),
        max_iterations=_safe_int(data.get("max_iterations"), 10),
        max_refine_duration_sec=_safe_int(data.get("max_refine_duration_sec"), 0),
        metric_key=data.get("metric_key", "primary_metric"),
        metric_direction=data.get("metric_direction", "minimize"),
        keep_threshold=_safe_float(data.get("keep_threshold"), 0.0),
        sandbox=SandboxConfig(
            python_path=sandbox_data.get("python_path", DEFAULT_PYTHON_PATH),
            gpu_required=bool(sandbox_data.get("gpu_required", False)),
            allowed_imports=tuple(
                sandbox_data.get("allowed_imports", SandboxConfig.allowed_imports)
            ),
            max_memory_mb=_safe_int(sandbox_data.get("max_memory_mb"), 4096),
        ),
        docker=DockerSandboxConfig(
            image=docker_data.get("image", "researchclaw/experiment:latest"),
            gpu_enabled=bool(docker_data.get("gpu_enabled", True)),
            gpu_device_ids=tuple(int(g) for g in docker_data.get("gpu_device_ids", ())),
            memory_limit_mb=_safe_int(docker_data.get("memory_limit_mb"), 8192),
            network_policy=_validate_network_policy(
                docker_data.get("network_policy", "setup_only"),
            ),
            pip_pre_install=tuple(docker_data.get("pip_pre_install", ())),
            auto_install_deps=bool(docker_data.get("auto_install_deps", True)),
            shm_size_mb=_safe_int(docker_data.get("shm_size_mb"), 2048),
            container_python=docker_data.get("container_python", "/usr/bin/python3"),
            keep_containers=bool(docker_data.get("keep_containers", False)),
            forward_hf_token=bool(docker_data.get("forward_hf_token", False)),
        ),
        distributed=DistributedTrainingConfig(
            enabled=bool(distributed_data.get("enabled", False)),
            strategy=distributed_data.get("strategy", "ddp"),
            launcher=distributed_data.get("launcher", "torchrun"),
            num_nodes=_safe_int(distributed_data.get("num_nodes"), 1),
            gpus_per_node=_safe_int(distributed_data.get("gpus_per_node"), 1),
            zero_stage=_safe_int(distributed_data.get("zero_stage"), 2),
            mixed_precision=distributed_data.get("mixed_precision", "bf16"),
            gradient_checkpointing=bool(
                distributed_data.get("gradient_checkpointing", True)
            ),
        ),
        parallel_hypotheses=ParallelHypothesesConfig(
            enabled=bool(parallel_hypotheses_data.get("enabled", False)),
            max_branches=max(
                1, _safe_int(parallel_hypotheses_data.get("max_branches"), 3)
            ),
            selection_metric=parallel_hypotheses_data.get(
                "selection_metric", "primary_metric"
            ),
        ),
        ssh_remote=SshRemoteConfig(
            host=ssh_data.get("host", ""),
            user=ssh_data.get("user", ""),
            port=_safe_int(ssh_data.get("port"), 22),
            key_path=ssh_data.get("key_path", ""),
            gpu_ids=tuple(int(g) for g in ssh_data.get("gpu_ids", ())),
            remote_workdir=ssh_data.get(
                # Default path on rented Linux GPU hosts.
                "remote_workdir", "/tmp/researchclaw_experiments"  # nosec B108
            ),
            remote_python=ssh_data.get("remote_python", "python3"),
            setup_commands=tuple(ssh_data.get("setup_commands") or ()),
            use_docker=bool(ssh_data.get("use_docker", True)),
            docker_image=ssh_data.get("docker_image", "researchclaw/experiment:latest"),
            docker_network_policy=_validate_network_policy(
                ssh_data.get("docker_network_policy", "none"),
            ),
            docker_memory_limit_mb=_safe_int(
                ssh_data.get("docker_memory_limit_mb"), 8192
            ),
            docker_shm_size_mb=_safe_int(ssh_data.get("docker_shm_size_mb"), 2048),
            timeout_sec=_safe_int(ssh_data.get("timeout_sec"), 600),
            scp_timeout_sec=_safe_int(ssh_data.get("scp_timeout_sec"), 300),
            setup_timeout_sec=_safe_int(ssh_data.get("setup_timeout_sec"), 300),
        ),
        colab_drive=ColabDriveConfig(
            drive_root=colab_data.get("drive_root", ""),
            poll_interval_sec=_safe_int(colab_data.get("poll_interval_sec"), 30),
            timeout_sec=_safe_int(colab_data.get("timeout_sec"), 3600),
            setup_script=colab_data.get("setup_script", ""),
        ),
        agentic=_parse_agentic_config(data.get("agentic") or {}),
        code_agent=_parse_code_agent_config(data.get("code_agent") or {}),
        opencode=_parse_opencode_config(data.get("opencode") or {}),
        benchmark_agent=_parse_benchmark_agent_config(
            data.get("benchmark_agent") or {}
        ),
        figure_agent=_parse_figure_agent_config(data.get("figure_agent") or {}),
        repair=_parse_experiment_repair_config(data.get("repair") or {}),
        cli_agent=_parse_cli_agent_config(data.get("cli_agent") or {}),
    )


def _parse_benchmark_agent_config(data: dict[str, Any]) -> BenchmarkAgentConfig:
    if not data:
        return BenchmarkAgentConfig()
    return BenchmarkAgentConfig(
        enabled=bool(data.get("enabled", True)),
        enable_hf_search=bool(data.get("enable_hf_search", True)),
        max_hf_results=_safe_int(data.get("max_hf_results"), 10),
        enable_web_search=bool(data.get("enable_web_search", True)),
        max_web_results=_safe_int(data.get("max_web_results"), 5),
        web_search_min_local=_safe_int(data.get("web_search_min_local"), 3),
        tier_limit=_safe_int(data.get("tier_limit"), 2),
        min_benchmarks=_safe_int(data.get("min_benchmarks"), 1),
        min_baselines=_safe_int(data.get("min_baselines"), 2),
        prefer_cached=bool(data.get("prefer_cached", True)),
        max_iterations=_safe_int(data.get("max_iterations"), 2),
    )


def _parse_figure_agent_config(data: dict[str, Any]) -> FigureAgentConfig:
    if not data:
        return FigureAgentConfig()
    use_docker_raw = data.get("use_docker")
    return FigureAgentConfig(
        enabled=bool(data.get("enabled", True)),
        min_figures=_safe_int(data.get("min_figures"), 3),
        max_figures=_safe_int(data.get("max_figures"), 8),
        max_iterations=_safe_int(data.get("max_iterations"), 3),
        render_timeout_sec=_safe_int(data.get("render_timeout_sec"), 30),
        use_docker=(None if use_docker_raw is None else bool(use_docker_raw)),
        docker_image=data.get("docker_image", "researchclaw/experiment:latest"),
        allow_local_execution=bool(data.get("allow_local_execution", False)),
        output_format=data.get("output_format", "python"),
        gemini_api_key=data.get("gemini_api_key", ""),
        gemini_model=data.get("gemini_model", "gemini-2.5-flash-image"),
        nano_banana_enabled=bool(data.get("nano_banana_enabled", True)),
        strict_mode=bool(data.get("strict_mode", False)),
        dpi=_safe_int(data.get("dpi"), 300),
    )


def _parse_experiment_repair_config(data: dict[str, Any]) -> ExperimentRepairConfig:
    if not data:
        return ExperimentRepairConfig()
    return ExperimentRepairConfig(
        enabled=bool(data.get("enabled", True)),
        max_cycles=_safe_int(data.get("max_cycles"), 3),
        min_completion_rate=_safe_float(data.get("min_completion_rate"), 0.5),
        min_conditions=_safe_int(data.get("min_conditions"), 2),
        use_opencode=bool(data.get("use_opencode", True)),
        timeout_sec_per_cycle=_safe_int(data.get("timeout_sec_per_cycle"), 600),
    )


def _parse_cli_agent_config(data: dict[str, Any]) -> CliAgentConfig:
    if not data:
        return CliAgentConfig()
    return CliAgentConfig(
        provider=data.get("provider", "llm"),
        binary_path=data.get("binary_path", ""),
        model=data.get("model", ""),
        max_budget_usd=_safe_float(data.get("max_budget_usd"), 5.0),
        timeout_sec=_safe_int(data.get("timeout_sec"), 600),
        extra_args=tuple(data.get("extra_args") or ()),
    )


def _parse_code_agent_config(data: dict[str, Any]) -> CodeAgentConfig:
    if not data:
        return CodeAgentConfig()
    return CodeAgentConfig(
        enabled=bool(data.get("enabled", True)),
        architecture_planning=bool(data.get("architecture_planning", True)),
        sequential_generation=bool(data.get("sequential_generation", True)),
        hard_validation=bool(data.get("hard_validation", True)),
        hard_validation_max_repairs=_safe_int(
            data.get("hard_validation_max_repairs"), 4
        ),
        exec_fix_max_iterations=_safe_int(data.get("exec_fix_max_iterations"), 3),
        exec_fix_timeout_sec=_safe_int(data.get("exec_fix_timeout_sec"), 60),
        tree_search_enabled=bool(data.get("tree_search_enabled", False)),
        tree_search_candidates=_safe_int(data.get("tree_search_candidates"), 3),
        tree_search_max_depth=_safe_int(data.get("tree_search_max_depth"), 2),
        tree_search_eval_timeout_sec=_safe_int(
            data.get("tree_search_eval_timeout_sec"), 120
        ),
        review_max_rounds=_safe_int(data.get("review_max_rounds"), 2),
    )


def _parse_opencode_config(data: dict[str, Any]) -> OpenCodeConfig:
    if not data:
        return OpenCodeConfig()
    return OpenCodeConfig(
        enabled=bool(data.get("enabled", True)),
        auto=bool(data.get("auto", True)),
        complexity_threshold=_safe_float(data.get("complexity_threshold"), 0.2),
        model=str(data.get("model", "")),
        timeout_sec=_safe_int(data.get("timeout_sec"), 600),
        max_retries=_safe_int(data.get("max_retries"), 1),
        workspace_cleanup=bool(data.get("workspace_cleanup", True)),
        forward_api_key_env=bool(data.get("forward_api_key_env", False)),
    )


def _parse_metaclaw_bridge_config(data: dict[str, Any]) -> MetaClawBridgeConfig:
    prm_data = data.get("prm") or {}
    l2s_data = data.get("lesson_to_skill") or {}
    return MetaClawBridgeConfig(
        enabled=bool(data.get("enabled", False)),
        proxy_url=data.get("proxy_url", "http://localhost:30000"),
        skills_dir=data.get("skills_dir", "~/.metaclaw/skills"),
        fallback_url=data.get("fallback_url", ""),
        fallback_api_key=data.get("fallback_api_key", ""),
        prm=MetaClawPRMConfig(
            enabled=bool(prm_data.get("enabled", False)),
            api_base=prm_data.get("api_base", ""),
            api_key_env=prm_data.get("api_key_env", ""),
            api_key=prm_data.get("api_key", ""),
            model=prm_data.get("model", "gpt-5.4"),
            votes=_safe_int(prm_data.get("votes"), 3),
            temperature=_safe_float(prm_data.get("temperature"), 0.6),
            gate_stages=tuple(
                int(s) for s in prm_data.get("gate_stages", (5, 9, 15, 20))
            ),
        ),
        lesson_to_skill=MetaClawLessonToSkillConfig(
            enabled=bool(l2s_data.get("enabled", True)),
            min_severity=l2s_data.get("min_severity", "warning"),
            max_skills_per_run=_safe_int(l2s_data.get("max_skills_per_run"), 3),
        ),
    )


def _parse_memory_config(data: dict[str, Any]) -> MemoryConfig:
    if not data:
        return MemoryConfig()
    stages = data.get("inject_at_stages", (1, 9, 10, 17))
    return MemoryConfig(
        enabled=bool(data.get("enabled", True)),
        store_dir=str(data.get("store_dir", ".researchclaw/memory")),
        embedding_model=str(data.get("embedding_model", "text-embedding-3-small")),
        max_entries_per_category=int(data.get("max_entries_per_category", 500)),
        decay_half_life_days=int(data.get("decay_half_life_days", 90)),
        confidence_threshold=float(data.get("confidence_threshold", 0.3)),
        inject_at_stages=tuple(int(s) for s in stages),
    )


def _parse_skills_config(data: dict[str, Any]) -> SkillsConfig:
    if not data:
        return SkillsConfig()
    return SkillsConfig(
        enabled=bool(data.get("enabled", True)),
        builtin_dir=str(data.get("builtin_dir", "")),
        custom_dirs=tuple(str(d) for d in (data.get("custom_dirs") or ())),
        external_dirs=tuple(str(d) for d in (data.get("external_dirs") or ())),
        auto_match=bool(data.get("auto_match", True)),
        max_skills_per_stage=int(data.get("max_skills_per_stage", 3)),
        fallback_matching=bool(data.get("fallback_matching", True)),
    )


def _parse_knowledge_graph_config(data: dict[str, Any]) -> KnowledgeGraphConfig:
    if not data:
        return KnowledgeGraphConfig()
    return KnowledgeGraphConfig(
        enabled=bool(data.get("enabled", False)),
        store_path=str(data.get("store_path", ".researchclaw/knowledge_graph")),
        max_entities=int(data.get("max_entities", 10000)),
        auto_update=bool(data.get("auto_update", True)),
    )


def _parse_multi_project_config(data: dict[str, Any]) -> MultiProjectConfig:
    if not data:
        return MultiProjectConfig()
    return MultiProjectConfig(
        enabled=bool(data.get("enabled", False)),
        projects_dir=data.get("projects_dir", ".researchclaw/projects"),
        max_concurrent=int(data.get("max_concurrent", 2)),
        shared_knowledge=bool(data.get("shared_knowledge", True)),
    )


def _parse_servers_config(data: dict[str, Any]) -> ServersConfig:
    if not data:
        return ServersConfig()
    raw_servers = data.get("servers") or ()
    servers = tuple(
        ServerEntryConfig(
            name=s.get("name", ""),
            host=s.get("host", ""),
            server_type=s.get("server_type", "ssh"),
            gpu=s.get("gpu", ""),
            vram_gb=int(s.get("vram_gb", 0)),
            priority=int(s.get("priority", 1)),
            cost_per_hour=float(s.get("cost_per_hour", 0.0)),
            scheduler=s.get("scheduler", ""),
            cloud_provider=s.get("cloud_provider", ""),
        )
        for s in raw_servers
    )
    return ServersConfig(
        enabled=bool(data.get("enabled", False)),
        servers=servers,
        prefer_free=bool(data.get("prefer_free", True)),
        failover=bool(data.get("failover", True)),
        monitor_interval_sec=int(data.get("monitor_interval_sec", 60)),
    )


def _parse_mcp_config(data: dict[str, Any]) -> MCPIntegrationConfig:
    if not data:
        return MCPIntegrationConfig()
    return MCPIntegrationConfig(
        server_enabled=bool(data.get("server_enabled", False)),
        server_port=int(data.get("server_port", 3000)),
        server_transport=data.get("server_transport", "stdio"),
        external_servers=tuple(data.get("external_servers") or ()),
    )


def _parse_overleaf_config(data: dict[str, Any]) -> OverleafConfig:
    if not data:
        return OverleafConfig()
    return OverleafConfig(
        enabled=bool(data.get("enabled", False)),
        git_url=data.get("git_url", ""),
        branch=data.get("branch", "main"),
        auto_push=bool(data.get("auto_push", True)),
        auto_pull=bool(data.get("auto_pull", False)),
        poll_interval_sec=int(data.get("poll_interval_sec", 300)),
    )


def _parse_server_config(data: dict[str, Any]) -> ServerConfig:
    if not data:
        return ServerConfig()
    cors = data.get("cors_origins")
    if isinstance(cors, list):
        cors = tuple(cors)
    elif cors is None:
        cors = DEFAULT_CORS_ORIGINS
    else:
        cors = (str(cors),)
    trusted_proxy_ips = data.get("trusted_proxy_ips", ())
    if isinstance(trusted_proxy_ips, str):
        trusted_proxy_ips = (trusted_proxy_ips,)
    elif isinstance(trusted_proxy_ips, list):
        trusted_proxy_ips = tuple(trusted_proxy_ips)
    elif trusted_proxy_ips is None:
        trusted_proxy_ips = ()
    else:
        trusted_proxy_ips = tuple(trusted_proxy_ips)
    trusted_proxy_ips = tuple(str(ip).strip() for ip in trusted_proxy_ips if str(ip).strip())
    auth_token = str(data.get("auth_token", "")).strip() or secrets.token_urlsafe(32)
    return ServerConfig(
        enabled=bool(data.get("enabled", False)),
        # Web server bind host is explicit configuration.
        host=data.get("host", "0.0.0.0"),  # nosec B104
        port=int(data.get("port", 8080)),
        cors_origins=cors,
        auth_token=auth_token,
        voice_enabled=bool(data.get("voice_enabled", False)),
        whisper_model=data.get("whisper_model", "whisper-1"),
        whisper_api_url=data.get("whisper_api_url", ""),
        rate_limit_requests=int(data.get("rate_limit_requests", 30)),
        rate_limit_window_sec=int(data.get("rate_limit_window_sec", 60)),
        trusted_proxy_ips=trusted_proxy_ips,
    )


def _parse_dashboard_config(data: dict[str, Any]) -> DashboardConfig:
    if not data:
        return DashboardConfig()
    return DashboardConfig(
        enabled=bool(data.get("enabled", True)),
        refresh_interval_sec=int(data.get("refresh_interval_sec", 5)),
        max_log_lines=int(data.get("max_log_lines", 1000)),
        browser_notifications=bool(data.get("browser_notifications", True)),
    )


def _parse_trends_config(data: dict[str, Any]) -> TrendsConfig:
    if not data:
        return TrendsConfig()
    sources = data.get("sources", ("arxiv", "semantic_scholar"))
    if isinstance(sources, list):
        sources = tuple(sources)
    domains = data.get("domains", ())
    if isinstance(domains, list):
        domains = tuple(domains)
    return TrendsConfig(
        enabled=bool(data.get("enabled", False)),
        domains=domains,
        daily_digest=bool(data.get("daily_digest", True)),
        digest_time=data.get("digest_time", "08:00"),
        max_papers_per_day=int(data.get("max_papers_per_day", 20)),
        trend_window_days=int(data.get("trend_window_days", 30)),
        sources=sources,
    )


def _parse_copilot_config(data: dict[str, Any]) -> CoPilotConfig:
    if not data:
        return CoPilotConfig()
    return CoPilotConfig(
        mode=data.get("mode", "auto-pilot"),
        pause_at_gates=bool(data.get("pause_at_gates", True)),
        pause_at_every_stage=bool(data.get("pause_at_every_stage", False)),
        feedback_timeout_sec=int(data.get("feedback_timeout_sec", 3600)),
        allow_branching=bool(data.get("allow_branching", True)),
        max_branches=int(data.get("max_branches", 3)),
    )


def _parse_quality_assessor_config(data: dict[str, Any]) -> QualityAssessorConfig:
    if not data:
        return QualityAssessorConfig()
    dimensions = data.get(
        "dimensions", ("novelty", "rigor", "clarity", "impact", "experiments")
    )
    if isinstance(dimensions, list):
        dimensions = tuple(dimensions)
    return QualityAssessorConfig(
        enabled=bool(data.get("enabled", True)),
        dimensions=dimensions,
        venue_recommendation=bool(data.get("venue_recommendation", True)),
        score_history=bool(data.get("score_history", True)),
    )


def _parse_calendar_config(data: dict[str, Any]) -> CalendarConfig:
    if not data:
        return CalendarConfig()
    venues = data.get("target_venues", ())
    if isinstance(venues, list):
        venues = tuple(venues)
    reminder = data.get("reminder_days_before", (30, 14, 7, 3, 1))
    if isinstance(reminder, list):
        reminder = tuple(reminder)
    return CalendarConfig(
        enabled=bool(data.get("enabled", False)),
        target_venues=venues,
        reminder_days_before=reminder,
        auto_plan=bool(data.get("auto_plan", True)),
    )


def _parse_hitl_config(data: dict[str, Any]) -> object:
    """Parse HITL config section. Returns HITLConfig or None."""
    if not data:
        return None
    try:
        from researchclaw.hitl.config import HITLConfig

        return HITLConfig.from_dict(data)
    except Exception:
        return None


def load_config(
    path: str | Path,
    *,
    config_cls: type[RCConfig] = RCConfig,
    project_root: str | Path | None = None,
    check_paths: bool = True,
) -> RCConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigValidationError(
            f"Config root must be a mapping, got {type(data).__name__}. "
            f"Check that {config_path} is valid YAML."
        )
    resolved_root = (
        Path(project_root).expanduser().resolve()
        if project_root
        else config_path.parent
    )
    return build_config_from_dict(
        config_cls, data, project_root=resolved_root, check_paths=check_paths
    )

"""Dataclass schema for ResearchClaw configuration."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from researchclaw.config.defaults import DEFAULT_CORS_ORIGINS, DEFAULT_PYTHON_PATH

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    mode: str = "docs-first"


@dataclass(frozen=True)
class ResearchConfig:
    topic: str
    domains: tuple[str, ...] = ()
    daily_paper_count: int = 0
    quality_threshold: float = 0.0
    graceful_degradation: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    timezone: str
    max_parallel_tasks: int = 1
    approval_timeout_hours: int = 12
    retry_limit: int = 0


@dataclass(frozen=True)
class NotificationsConfig:
    channel: str
    target: str = ""
    on_stage_start: bool = False
    on_stage_fail: bool = False
    on_gate_required: bool = True


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    backend: str
    root: str
    obsidian_vault: str = ""


@dataclass(frozen=True)
class OpenClawBridgeConfig:
    use_cron: bool = False
    use_message: bool = False
    use_memory: bool = False
    use_sessions_spawn: bool = False
    use_web_fetch: bool = False
    use_browser: bool = False


@dataclass(frozen=True)
class AcpConfig:
    """ACP (Agent Client Protocol) settings."""

    agent: str = "claude"
    cwd: str = "."
    acpx_command: str = ""
    session_name: str = "researchclaw"
    timeout_sec: int = 1800


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    base_url: str = ""
    wire_api: str = "chat_completions"
    api_key_env: str = ""
    # Deprecated: kept for backward-compatible attribute access only.
    # YAML parsing rejects and ignores llm.api_key; use api_key_env.
    api_key: str = ""
    primary_model: str = ""
    fallback_models: tuple[str, ...] = ()
    s2_api_key: str = ""
    notes: str = ""
    timeout_sec: int = 600
    acp: AcpConfig = field(default_factory=AcpConfig)


@dataclass(frozen=True)
class SecurityConfig:
    hitl_required_stages: tuple[int, ...] = (5, 9, 20)
    allow_publish_without_approval: bool = False
    redact_sensitive_logs: bool = True


@dataclass(frozen=True)
class SandboxConfig:
    python_path: str = DEFAULT_PYTHON_PATH
    gpu_required: bool = False
    allowed_imports: tuple[str, ...] = (
        "math",
        "random",
        "json",
        "csv",
        "numpy",
        "torch",
        "sklearn",
    )
    max_memory_mb: int = 4096


@dataclass(frozen=True)
class DistributedTrainingConfig:
    """Configuration hints for generated and executed multi-GPU experiments."""

    enabled: bool = False
    strategy: str = "ddp"  # ddp | fsdp | deepspeed
    launcher: str = "torchrun"  # torchrun | accelerate | deepspeed
    num_nodes: int = 1
    gpus_per_node: int = 1
    zero_stage: int = 2
    mixed_precision: str = "bf16"
    gradient_checkpointing: bool = True


@dataclass(frozen=True)
class ParallelHypothesesConfig:
    """Configuration for planning parallel hypothesis exploration branches."""

    enabled: bool = False
    max_branches: int = 3
    selection_metric: str = "primary_metric"


@dataclass(frozen=True)
class SshRemoteConfig:
    host: str = ""
    user: str = ""
    port: int = 22
    key_path: str = ""
    gpu_ids: tuple[int, ...] = ()
    remote_workdir: str = "/tmp/researchclaw_experiments"
    remote_python: str = "python3"
    setup_commands: tuple[str, ...] = ()
    use_docker: bool = True
    docker_image: str = "researchclaw/experiment:latest"
    docker_network_policy: str = "none"
    docker_memory_limit_mb: int = 8192
    docker_shm_size_mb: int = 2048
    timeout_sec: int = 600  # default 10 min for experiment execution
    scp_timeout_sec: int = 300  # default 5 min for file uploads
    setup_timeout_sec: int = 300  # default 5 min for setup commands
    distributed: DistributedTrainingConfig = field(default_factory=DistributedTrainingConfig)


@dataclass(frozen=True)
class ColabDriveConfig:
    """Configuration for Google Drive-based async Colab execution."""

    drive_root: str = ""  # local mount path, e.g. ~/Google Drive/MyDrive/researchclaw
    poll_interval_sec: int = 30
    timeout_sec: int = 3600
    setup_script: str = ""  # commands to run before experiment, written to setup.sh


@dataclass(frozen=True)
class DockerSandboxConfig:
    """Configuration for Docker-based experiment sandbox."""

    image: str = "researchclaw/experiment:latest"
    gpu_enabled: bool = True
    gpu_device_ids: tuple[int, ...] = ()
    memory_limit_mb: int = 8192
    network_policy: str = "setup_only"  # none | setup_only | pip_only | full
    pip_pre_install: tuple[str, ...] = ()
    auto_install_deps: bool = True
    shm_size_mb: int = 2048
    container_python: str = "/usr/bin/python3"
    keep_containers: bool = False
    forward_hf_token: bool = False
    distributed: DistributedTrainingConfig = field(default_factory=DistributedTrainingConfig)


@dataclass(frozen=True)
class AgenticConfig:
    """Configuration for the agentic experiment mode.

    Launches a coding agent (e.g. Claude Code) inside a Docker container
    with full shell access so it can run arbitrary CLI commands, write code,
    and iteratively complete the experiment.
    """

    image: str = "researchclaw/experiment:latest"
    agent_cli: str = "claude"
    agent_install_cmd: str = "npm install -g @anthropic-ai/claude-code"
    network_policy: str = "full"  # Agent needs network access
    timeout_sec: int = 1800  # 30 min per session
    memory_limit_mb: int = 8192
    gpu_enabled: bool = False
    mount_skills: bool = True
    allow_shell_commands: bool = True
    max_turns: int = 50


@dataclass(frozen=True)
class CodeAgentConfig:
    """Configuration for the advanced multi-phase code generation agent."""

    enabled: bool = True
    # Phase 1: Blueprint planning (deep implementation blueprint)
    architecture_planning: bool = True
    # Phase 2: Sequential file generation (one-by-one following blueprint)
    sequential_generation: bool = True
    # Phase 2.5: Hard validation gates (AST-based)
    hard_validation: bool = True
    hard_validation_max_repairs: int = 4
    # Phase 3: Execution-in-the-loop (run → parse error → fix)
    exec_fix_max_iterations: int = 3
    exec_fix_timeout_sec: int = 60
    # Phase 4: Solution tree search (off by default — higher cost)
    tree_search_enabled: bool = False
    tree_search_candidates: int = 3
    tree_search_max_depth: int = 2
    tree_search_eval_timeout_sec: int = 120
    # Phase 5: Multi-agent review dialog
    review_max_rounds: int = 2


@dataclass(frozen=True)
class OpenCodeConfig:
    """OpenCode 'Beast Mode' — external AI coding agent for complex experiments.

    Requires: npm i -g opencode-ai@latest
    """

    enabled: bool = True
    auto: bool = True  # Auto-trigger without user confirmation
    complexity_threshold: float = 0.2  # 0.0-1.0
    model: str = ""  # Empty = use llm.primary_model
    timeout_sec: int = 600  # Max seconds for opencode run
    max_retries: int = 1
    workspace_cleanup: bool = True
    forward_api_key_env: bool = False


@dataclass(frozen=True)
class BenchmarkAgentConfig:
    """Configuration for the BenchmarkAgent multi-agent system."""

    enabled: bool = True
    # Surveyor
    enable_hf_search: bool = True
    max_hf_results: int = 10
    # Surveyor — web search
    enable_web_search: bool = True
    max_web_results: int = 5
    web_search_min_local: int = 3  # skip web search when local benchmarks >= this
    # Selector
    tier_limit: int = 2
    min_benchmarks: int = 1
    min_baselines: int = 2
    prefer_cached: bool = True
    # Orchestrator
    max_iterations: int = 2


@dataclass(frozen=True)
class FigureAgentConfig:
    """Configuration for the FigureAgent multi-agent system."""

    enabled: bool = True
    # Planner
    min_figures: int = 3
    max_figures: int = 8
    # Orchestrator
    max_iterations: int = 3  # max CodeGen→Renderer→Critic retry loops
    # Renderer security
    render_timeout_sec: int = 30
    use_docker: bool | None = None  # None = auto-detect, True/False to force
    docker_image: str = "researchclaw/experiment:latest"
    allow_local_execution: bool = False
    # Code generation output format
    output_format: str = "python"  # "python" (matplotlib) or "latex" (TikZ/PGFPlots)
    # Nano Banana (Gemini image generation)
    gemini_api_key: str = ""  # or set GEMINI_API_KEY / GOOGLE_API_KEY env var
    gemini_model: str = "gemini-2.5-flash-image"
    nano_banana_enabled: bool = True  # enable/disable Gemini image generation
    # Critic
    strict_mode: bool = False
    # Output
    dpi: int = 300


@dataclass(frozen=True)
class ExperimentRepairConfig:
    """Experiment repair loop — diagnose and fix failed experiments before paper writing.

    When enabled, after Stage 14 (result_analysis) the pipeline:
    1. Diagnoses experiment failures (missing deps, crashes, OOM, time guard, etc.)
    2. Assesses experiment quality (full_paper / preliminary_study / technical_report)
    3. If quality is insufficient, generates targeted repair prompts
    4. Re-runs experiment with fixes, up to ``max_cycles`` times
    5. Selects best results across all cycles for paper writing
    """

    enabled: bool = True
    max_cycles: int = 3
    min_completion_rate: float = 0.5  # At least 50% conditions must complete
    min_conditions: int = 2  # At least 2 conditions for a valid experiment
    use_opencode: bool = True  # Use OpenCode agent for repairs (vs LLM prompt)
    timeout_sec_per_cycle: int = 600  # Max time per repair cycle


@dataclass(frozen=True)
class CliAgentConfig:
    """CLI-based code generation backend for Stages 10 & 13.

    provider: "llm"          — use existing LLM chat API (default, backward-compatible)
              "claude_code"  — Claude Code CLI (``claude -p``)
              "codex"        — OpenAI Codex CLI (``codex exec``)

    Auth for claude_code: ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL env vars.
    Auth for codex:       OPENAI_API_KEY env var.
    """

    provider: str = "llm"
    binary_path: str = ""  # auto-detected via PATH if empty
    model: str = ""  # model override for the CLI agent
    max_budget_usd: float = 5.0
    timeout_sec: int = 600
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperimentConfig:
    mode: str = "simulated"
    time_budget_sec: int = 300
    max_iterations: int = 10
    max_refine_duration_sec: int = 0  # 0 = auto (3× time_budget_sec)
    metric_key: str = "primary_metric"
    metric_direction: str = "minimize"
    keep_threshold: float = 0.0
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    docker: DockerSandboxConfig = field(default_factory=DockerSandboxConfig)
    distributed: DistributedTrainingConfig = field(default_factory=DistributedTrainingConfig)
    parallel_hypotheses: ParallelHypothesesConfig = field(default_factory=ParallelHypothesesConfig)
    agentic: AgenticConfig = field(default_factory=AgenticConfig)
    ssh_remote: SshRemoteConfig = field(default_factory=SshRemoteConfig)
    colab_drive: ColabDriveConfig = field(default_factory=ColabDriveConfig)
    code_agent: CodeAgentConfig = field(default_factory=CodeAgentConfig)
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    benchmark_agent: BenchmarkAgentConfig = field(default_factory=BenchmarkAgentConfig)
    figure_agent: FigureAgentConfig = field(default_factory=FigureAgentConfig)
    repair: ExperimentRepairConfig = field(default_factory=ExperimentRepairConfig)
    cli_agent: CliAgentConfig = field(default_factory=CliAgentConfig)
    # F-01 Phase 2: live framework doc fetching (llms.txt → web crawl → static)
    framework_doc_fetch: bool = False


@dataclass(frozen=True)
class MetaClawPRMConfig:
    """PRM quality gate settings for MetaClaw bridge."""

    enabled: bool = False
    api_base: str = ""
    api_key_env: str = ""
    api_key: str = ""
    model: str = "gpt-5.4"
    votes: int = 3
    temperature: float = 0.6
    gate_stages: tuple[int, ...] = (5, 9, 15, 20)


@dataclass(frozen=True)
class MetaClawLessonToSkillConfig:
    """Settings for converting lessons into MetaClaw skills."""

    enabled: bool = True
    min_severity: str = "warning"
    max_skills_per_run: int = 3


@dataclass(frozen=True)
class MetaClawBridgeConfig:
    """MetaClaw integration bridge configuration."""

    enabled: bool = False
    proxy_url: str = "http://localhost:30000"
    skills_dir: str = "~/.metaclaw/skills"
    fallback_url: str = ""
    fallback_api_key: str = ""
    prm: MetaClawPRMConfig = field(default_factory=MetaClawPRMConfig)
    lesson_to_skill: MetaClawLessonToSkillConfig = field(
        default_factory=MetaClawLessonToSkillConfig
    )


@dataclass(frozen=True)
class WebSearchConfig:
    """Configuration for web search and crawling capabilities."""

    enabled: bool = True
    tavily_api_key: str = ""
    tavily_api_key_env: str = "TAVILY_API_KEY"
    enable_scholar: bool = True
    enable_crawling: bool = True
    enable_pdf_extraction: bool = True
    max_web_results: int = 10
    max_scholar_results: int = 10
    max_crawl_urls: int = 5


@dataclass(frozen=True)
class ExportConfig:
    """Configuration for paper export and LaTeX generation."""

    target_conference: str = "neurips_2025"
    authors: str = "Anonymous"
    paper_language: str = "English"
    bib_file: str = "references"


@dataclass(frozen=True)
class PromptsConfig:
    """Configuration for prompt externalization."""

    custom_file: str = ""  # Path to custom prompts YAML (empty = use defaults)


# ── Agent B: Intelligence & Memory configs ────────────────────────


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration for the persistent evolutionary memory system."""

    enabled: bool = True
    store_dir: str = ".researchclaw/memory"
    embedding_model: str = "text-embedding-3-small"
    max_entries_per_category: int = 500
    decay_half_life_days: int = 90
    confidence_threshold: float = 0.3
    inject_at_stages: tuple[int, ...] = (1, 9, 10, 17)


@dataclass(frozen=True)
class SkillsConfig:
    """Configuration for the dynamic skills library."""

    enabled: bool = True
    builtin_dir: str = ""  # empty = use package default
    custom_dirs: tuple[str, ...] = ()
    external_dirs: tuple[str, ...] = ()
    auto_match: bool = True
    max_skills_per_stage: int = 3
    fallback_matching: bool = True


@dataclass(frozen=True)
class KnowledgeGraphConfig:
    """Configuration for the research knowledge graph."""

    enabled: bool = False
    store_path: str = ".researchclaw/knowledge_graph"
    max_entities: int = 10000
    auto_update: bool = True


# ── Web platform configs (Agent A) ──────────────────────────────


@dataclass(frozen=True)
class ServerConfig:
    """Web server configuration."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    auth_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    voice_enabled: bool = False
    whisper_model: str = "whisper-1"
    whisper_api_url: str = ""  # empty = use OpenAI default
    rate_limit_requests: int = 30
    rate_limit_window_sec: int = 60
    trusted_proxy_ips: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard configuration."""

    enabled: bool = True
    refresh_interval_sec: int = 5
    max_log_lines: int = 1000
    browser_notifications: bool = True


# ── Agent C: Infrastructure configs ────────────────────────────────


@dataclass(frozen=True)
class MultiProjectConfig:
    """C1: Multi-project parallel management."""

    enabled: bool = False
    projects_dir: str = ".researchclaw/projects"
    max_concurrent: int = 2
    shared_knowledge: bool = True


@dataclass(frozen=True)
class ServerEntryConfig:
    """Single compute server entry for C2."""

    name: str = ""
    host: str = ""
    server_type: str = "ssh"
    gpu: str = ""
    vram_gb: int = 0
    priority: int = 1
    cost_per_hour: float = 0.0
    scheduler: str = ""
    cloud_provider: str = ""


@dataclass(frozen=True)
class ServersConfig:
    """C2: Multi-server resource scheduling."""

    enabled: bool = False
    servers: tuple[ServerEntryConfig, ...] = ()
    prefer_free: bool = True
    failover: bool = True
    monitor_interval_sec: int = 60


@dataclass(frozen=True)
class MCPIntegrationConfig:
    """C3: MCP standardized integration."""

    server_enabled: bool = False
    server_port: int = 3000
    server_transport: str = "stdio"
    external_servers: tuple[dict, ...] = ()


@dataclass(frozen=True)
class OverleafConfig:
    """C4: Overleaf bidirectional sync."""

    enabled: bool = False
    git_url: str = ""
    branch: str = "main"
    auto_push: bool = True
    auto_pull: bool = False
    poll_interval_sec: int = 300


COPILOT_MODES = ("co-pilot", "auto-pilot", "zero-touch")


@dataclass(frozen=True)
class TrendsConfig:
    """D1: Research trend tracking."""

    enabled: bool = False
    domains: tuple[str, ...] = ()
    daily_digest: bool = True
    digest_time: str = "08:00"
    max_papers_per_day: int = 20
    trend_window_days: int = 30
    sources: tuple[str, ...] = ("arxiv", "semantic_scholar")


@dataclass(frozen=True)
class CoPilotConfig:
    """D2: Interactive co-pilot mode."""

    mode: str = "auto-pilot"
    pause_at_gates: bool = True
    pause_at_every_stage: bool = False
    feedback_timeout_sec: int = 3600
    allow_branching: bool = True
    max_branches: int = 3


@dataclass(frozen=True)
class QualityAssessorConfig:
    """D3: Paper quality assessor."""

    enabled: bool = True
    dimensions: tuple[str, ...] = (
        "novelty",
        "rigor",
        "clarity",
        "impact",
        "experiments",
    )
    venue_recommendation: bool = True
    score_history: bool = True


@dataclass(frozen=True)
class CalendarConfig:
    """D4: Conference deadline calendar."""

    enabled: bool = False
    target_venues: tuple[str, ...] = ()
    reminder_days_before: tuple[int, ...] = (30, 14, 7, 3, 1)
    auto_plan: bool = True


@dataclass(frozen=True)
class RCConfig:
    project: ProjectConfig
    research: ResearchConfig
    runtime: RuntimeConfig
    notifications: NotificationsConfig
    knowledge_base: KnowledgeBaseConfig
    openclaw_bridge: OpenClawBridgeConfig
    llm: LlmConfig
    security: SecurityConfig = field(default_factory=SecurityConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    metaclaw_bridge: MetaClawBridgeConfig = field(default_factory=MetaClawBridgeConfig)
    # Agent B: Intelligence & Memory
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    knowledge_graph: KnowledgeGraphConfig = field(default_factory=KnowledgeGraphConfig)
    # Agent C: Infrastructure
    multi_project: MultiProjectConfig = field(default_factory=MultiProjectConfig)
    compute_servers: ServersConfig = field(default_factory=ServersConfig)
    mcp: MCPIntegrationConfig = field(default_factory=MCPIntegrationConfig)
    overleaf: OverleafConfig = field(default_factory=OverleafConfig)
    # Agent A: Web platform
    server: ServerConfig = field(default_factory=ServerConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    # Agent D: Research Enhancement
    trends: TrendsConfig = field(default_factory=TrendsConfig)
    copilot: CoPilotConfig = field(default_factory=CoPilotConfig)
    quality_assessor: QualityAssessorConfig = field(
        default_factory=QualityAssessorConfig
    )
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    # HITL Co-Pilot System
    hitl: object = field(default=None)  # HITLConfig (lazy import avoids circular dep)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_research_overrides(self, **overrides: Any) -> RCConfig:
        """Return a copy with selected research config fields replaced."""
        return replace(self, research=replace(self.research, **overrides))

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        project_root: Path | None = None,
        check_paths: bool = True,
    ) -> RCConfig:
        from researchclaw.config.parsing import build_config_from_dict

        return build_config_from_dict(
            cls, data, project_root=project_root, check_paths=check_paths
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
        check_paths: bool = True,
    ) -> RCConfig:
        from researchclaw.config.parsing import load_config

        return load_config(
            path, config_cls=cls, project_root=project_root, check_paths=check_paths
        )



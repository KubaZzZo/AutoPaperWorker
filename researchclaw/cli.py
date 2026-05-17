"""ResearchClaw CLI — run the 23-stage autonomous research pipeline."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from researchclaw.adapters import AdapterBundle
from researchclaw.config import (
    CONFIG_SEARCH_ORDER,
    DEFAULT_ARTIFACTS_DIR,
    EXAMPLE_CONFIG,
    RCConfig,
    resolve_config_path,
)
from researchclaw.health import print_doctor_report, run_doctor, write_doctor_report
from researchclaw.llm import (
    cli_provider_choices,
    cli_provider_menu_lines,
    provider_base_urls,
    provider_model_defaults,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenCode installation helpers
# ---------------------------------------------------------------------------

def _is_opencode_installed() -> bool:
    """Check if the ``opencode`` CLI is available on PATH."""
    opencode_cmd = shutil.which("opencode")
    if opencode_cmd is None:
        return False
    try:
        r = subprocess.run(
            [opencode_cmd, "--version"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        logger.warning("OpenCode version probe failed", exc_info=True)
        return False


def _is_npm_installed() -> bool:
    """Check if ``npm`` is available on PATH."""
    return shutil.which("npm") is not None


def _install_opencode() -> bool:
    """Install OpenCode globally via npm.  Returns True on success."""
    print("  Installing opencode-ai (this may take a minute)...")
    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        print("  npm is not installed. Cannot install OpenCode.")
        return False
    try:
        r = subprocess.run(
            [npm_cmd, "i", "-g", "opencode-ai@latest"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            print("  OpenCode installed successfully!")
            return True
        else:
            print(f"  Installation failed (exit {r.returncode}):")
            if r.stderr:
                for line in r.stderr.strip().splitlines()[:5]:
                    print(f"    {line}")
            return False
    except subprocess.TimeoutExpired:
        print("  Installation timed out.")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  Installation failed: {exc}")
        return False


def _prompt_opencode_install() -> bool:
    """Interactively prompt the user to install OpenCode.

    Returns True if OpenCode is now available (already installed or
    just installed successfully).  Returns False otherwise.
    """
    if _is_opencode_installed():
        return True

    if not sys.stdin.isatty():
        return False

    print()
    print("=" * 60)
    print("  OpenCode Beast Mode  (Recommended)")
    print("=" * 60)
    print()
    print("  OpenCode is an AI coding agent that dramatically improves")
    print("  experiment code generation for complex research tasks.")
    print()
    print("  With OpenCode enabled, ResearchClaw can generate multi-file")
    print("  experiment projects with custom architectures, training")
    print("  loops, and ablation studies — far beyond single-file limits.")
    print()

    if not _is_npm_installed():
        print("  Node.js/npm is required but not installed.")
        print("  To install OpenCode later:")
        print("    1. Install Node.js: https://nodejs.org/")
        print("    2. Run: npm i -g opencode-ai@latest")
        print("    — or: researchclaw setup")
        print()
        return False

    try:
        answer = input("  Install OpenCode now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in ("", "y", "yes"):
        success = _install_opencode()
        if not success:
            print("  You can retry later with: researchclaw setup")
        return success
    else:
        print("  Skipped. You can install later with: researchclaw setup")
        return False


def _resolve_config_or_exit(args: argparse.Namespace) -> Path | None:
    """Resolve config path from args, printing helpful errors on failure.

    Returns the resolved Path on success, or None if the config cannot be found
    (after printing an error message to stderr).
    """
    path = resolve_config_path(getattr(args, "config", None))
    if path is not None and not path.exists():
        print(f"Error: config file not found: {path}", file=sys.stderr)
        return None
    if path is None:
        search_list = ", ".join(CONFIG_SEARCH_ORDER)
        print(
            f"Error: no config file found (searched: {search_list}).\n"
            f"Run 'researchclaw init' to create one from the example template.",
            file=sys.stderr,
        )
        return None
    return path


def _generate_run_id(topic: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    topic_hash = hashlib.sha256(topic.encode()).hexdigest()[:6]
    return f"rc-{ts}-{topic_hash}"


def cmd_run(args: argparse.Namespace) -> int:
    resolved = _resolve_config_or_exit(args)
    if resolved is None:
        return 1
    config_path = resolved
    topic = cast(str | None, args.topic)
    output = cast(str | None, args.output)
    from_stage_name = cast(str | None, args.from_stage)
    to_stage_name = cast(str | None, getattr(args, "to_stage", None))
    auto_approve = cast(bool, args.auto_approve)
    skip_preflight = cast(bool, args.skip_preflight)
    resume = cast(bool, args.resume)
    skip_noncritical = cast(bool, args.skip_noncritical_stage)
    no_graceful_degradation = cast(bool, args.no_graceful_degradation)
    hitl_mode = cast(str | None, getattr(args, "mode", None))

    kb_root_path = None
    config = RCConfig.load(config_path, check_paths=False)

    # Override graceful_degradation if CLI flag is set
    if no_graceful_degradation:
        config = config.with_research_overrides(graceful_degradation=False)

    # Derive gate behavior from project.mode (CLI --auto-approve overrides)
    mode = config.project.mode.lower()
    if auto_approve:
        # Explicit CLI flag takes precedence over config mode
        stop_on_gate = False
    elif mode == "full-auto":
        auto_approve = True
        stop_on_gate = False
    else:
        # "semi-auto" and "docs-first" should block on gates
        stop_on_gate = True

    if topic:
        config = config.with_research_overrides(topic=topic)

    # --- LLM Preflight ---
    if not skip_preflight:
        from researchclaw.llm import create_llm_client

        client = create_llm_client(config)
        print("Preflight check...", end=" ", flush=True)
        ok, msg = client.preflight()
        if ok:
            print(msg)
        else:
            print(f"FAILED — {msg}", file=sys.stderr)
            return 1

    run_id = _generate_run_id(config.research.topic)
    run_dir = Path(output) if output else DEFAULT_ARTIFACTS_DIR / run_id

    # BUG-119 / #216: When --resume or --from-stage is used without --output,
    # search for the most recent existing run directory that matches the topic.
    # Without this, --from-stage creates a fresh empty directory and the
    # StageContract input_files check fails immediately.
    if (resume or from_stage_name) and not output:
        topic_hash = hashlib.sha256(config.research.topic.encode()).hexdigest()[:6]
        artifacts_root = DEFAULT_ARTIFACTS_DIR
        if artifacts_root.is_dir():
            candidates = sorted(
                (
                    d for d in artifacts_root.iterdir()
                    if d.is_dir()
                    and d.name.startswith("rc-")
                    and d.name.endswith(f"-{topic_hash}")
                    and (d / "checkpoint.json").exists()
                ),
                key=lambda d: d.name,
                reverse=True,  # newest first (timestamp in name)
            )
            if candidates:
                run_dir = candidates[0]
                run_id = run_dir.name
                print(f"Found existing run: {run_dir}")
            elif from_stage_name:
                print(
                    f"Error: --from-stage {from_stage_name} requires prior "
                    f"stage artifacts, but no existing run found for topic "
                    f"hash '{topic_hash}'. Use --output to specify the run "
                    f"directory containing prior artifacts.",
                    file=sys.stderr,
                )
                return 1
            else:
                print(
                    "Warning: --resume specified but no checkpoint found "
                    f"for topic hash '{topic_hash}'. Starting new run.",
                    file=sys.stderr,
                )

    run_dir.mkdir(parents=True, exist_ok=True)

    if config.knowledge_base.root:
        kb_root_path = Path(config.knowledge_base.root)
        kb_root_path.mkdir(parents=True, exist_ok=True)

    adapters = AdapterBundle()

    # --- HITL session setup ---
    hitl_session = None
    try:
        from researchclaw.hitl.config import HITLConfig
        from researchclaw.hitl.presets import get_preset
        from researchclaw.hitl.session import HITLSession

        hitl_config = None
        if hitl_mode:
            # CLI --mode flag takes precedence
            hitl_config = get_preset(hitl_mode)
            if hitl_config is None:
                hitl_config = HITLConfig(enabled=True, mode=hitl_mode)
        elif hasattr(config, "hitl") and config.hitl is not None:
            hitl_config = config.hitl
        # If HITL is enabled, auto_approve should be False
        if hitl_config and hitl_config.enabled:
            auto_approve = False
            stop_on_gate = False  # HITL handles gates directly
    except ImportError:
        hitl_config = None

    from researchclaw.pipeline.runner import execute_pipeline, read_checkpoint
    from researchclaw.pipeline.stages import Stage

    # --- Determine start stage ---
    from_stage = Stage.TOPIC_INIT
    if from_stage_name:
        try:
            from_stage = Stage[from_stage_name.upper()]
        except KeyError:
            valid = ", ".join(s.name for s in Stage)
            print(
                f"Error: unknown stage '{from_stage_name}'. "
                f"Valid stages: {valid}",
                file=sys.stderr,
            )
            return 1
    elif resume:
        resumed = read_checkpoint(run_dir)
        if resumed is not None:
            from_stage = resumed
            print(f"Resuming from checkpoint: Stage {int(from_stage)}: {from_stage.name}")

    # --- Determine stop stage ---
    to_stage: Stage | None = None
    if to_stage_name:
        try:
            to_stage = Stage[to_stage_name.upper()]
        except KeyError:
            valid = ", ".join(s.name for s in Stage)
            print(
                f"Error: unknown stage '{to_stage_name}'. "
                f"Valid stages: {valid}",
                file=sys.stderr,
            )
            return 1
        if int(to_stage) < int(from_stage):
            print(
                f"Error: --to-stage {to_stage.name} (stage {int(to_stage)}) "
                f"must be >= --from-stage {from_stage.name} (stage {int(from_stage)})",
                file=sys.stderr,
            )
            return 1

    # --- Create HITL session and wire to adapters ---
    if hitl_config and hitl_config.enabled:
        try:
            hitl_session = HITLSession(
                run_id=run_id,
                config=hitl_config,
                run_dir=run_dir,
            )
            # Check for scripted intervention file (env var or CLI flag)
            interventions_file = os.environ.get("HITL_INTERVENTIONS_FILE", "")
            if not interventions_file:
                interventions_file = getattr(args, "interventions", None) or ""
            if interventions_file and Path(interventions_file).is_file():
                from researchclaw.hitl.adapters.scripted_adapter import (
                    ScriptedHITLAdapter,
                )

                scripted = ScriptedHITLAdapter.from_file(interventions_file)
                hitl_session.set_input_callback(scripted.collect_input)
                print(f"  HITL:    scripted ({len(scripted.pending_stages)} interventions)")
            else:
                # Wire CLI adapter for interactive input
                from researchclaw.hitl.adapters.cli_adapter import CLIAdapter

                cli_adapter = CLIAdapter(run_dir=run_dir)
                hitl_session.set_input_callback(cli_adapter.collect_input)
            adapters.hitl = hitl_session
        except Exception as _hitl_exc:
            logging.getLogger(__name__).warning(
                "HITL session setup failed: %s", _hitl_exc
            )

    from researchclaw import __version__
    print(f"ResearchClaw v{__version__} — Starting pipeline")
    print(f"  Run ID:  {run_id}")
    print(f"  Topic:   {config.research.topic}")
    print(f"  Output:  {run_dir}")
    print(f"  Mode:    {config.project.mode}")
    if hitl_config and hitl_config.enabled:
        print(f"  HITL:    {hitl_config.mode}")
    print(f"  From:    Stage {int(from_stage)}: {from_stage.name}")
    if to_stage:
        print(f"  To:      Stage {int(to_stage)}: {to_stage.name}")

    # Hint: OpenCode beast mode
    exp_cfg = getattr(config, "experiment", None)
    oc_cfg = getattr(exp_cfg, "opencode", None)
    if oc_cfg and getattr(oc_cfg, "enabled", False) and not _is_opencode_installed():
        print()
        print("  Hint: OpenCode beast mode is enabled but not installed.")
        print("        Run 'researchclaw setup' to install for better code generation.")

    print()

    results = execute_pipeline(
        run_dir=run_dir,
        run_id=run_id,
        config=config,
        adapters=adapters,
        from_stage=from_stage,
        to_stage=to_stage,
        auto_approve_gates=auto_approve,
        stop_on_gate=stop_on_gate,
        skip_noncritical=skip_noncritical,
        kb_root=kb_root_path,
        progress_reporter=print,
    )

    done = sum(1 for r in results if r.status.value == "done")
    paused = sum(1 for r in results if r.status.value == "paused")
    failed = sum(1 for r in results if r.status.value == "failed")

    # --- Complete HITL session ---
    if hitl_session is not None:
        hitl_session.complete()
        if hitl_session.interventions_count > 0:
            print(
                f"  HITL: {hitl_session.interventions_count} interventions, "
                f"{hitl_session.total_human_time_sec:.0f}s human time"
            )

    if paused:
        print(
            f"\nPipeline paused: {done}/{len(results)} stages done, "
            f"{paused} paused, {failed} failed"
        )
    else:
        print(f"\nPipeline complete: {done}/{len(results)} stages done, {failed} failed")
    return 0 if failed == 0 else 1


def cmd_validate(args: argparse.Namespace) -> int:
    import yaml

    from researchclaw.config import validate_config

    resolved = _resolve_config_or_exit(args)
    if resolved is None:
        return 1
    config_path = resolved
    no_check_paths = cast(bool, args.no_check_paths)

    with config_path.open(encoding="utf-8") as f:
        loaded = cast(object, yaml.safe_load(f))

    if loaded is None:
        data: dict[str, object] = {}
    elif isinstance(loaded, dict):
        loaded_map = cast(Mapping[object, object], loaded)
        data = {str(key): value for key, value in loaded_map.items()}
    else:
        print("Config validation FAILED:")
        print("  Error: Config root must be a mapping")
        return 1

    result = validate_config(data, check_paths=not no_check_paths)
    if result.ok:
        print("Config validation passed")
        for w in result.warnings:
            print(f"  Warning: {w}")
        return 0
    else:
        print("Config validation FAILED:")
        for e in result.errors:
            print(f"  Error: {e}")
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    resolved = _resolve_config_or_exit(args)
    if resolved is None:
        return 1
    config_path = resolved
    output = cast(str | None, args.output)

    report = run_doctor(config_path)
    print_doctor_report(report)
    if output:
        write_doctor_report(report, Path(output))
    return 0 if report.overall == "pass" else 1













_PROVIDER_CHOICES = cli_provider_choices()
_PROVIDER_URLS = provider_base_urls()
_PROVIDER_MODELS = provider_model_defaults()

from researchclaw.cli_commands import (
    cmd_attach,
    cmd_calendar,
    cmd_dashboard,
    cmd_gui,
    cmd_hitl_approve,
    cmd_hitl_guide,
    cmd_hitl_reject,
    cmd_init,
    cmd_mcp,
    cmd_overleaf,
    cmd_project,
    cmd_report,
    cmd_serve,
    cmd_setup,
    cmd_skills,
    cmd_status,
    cmd_trends,
    cmd_wizard,
    cmd_workbench,
)








# ── Research Enhancement commands (Agent D) ───────────────────────












def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="researchclaw",
        description="ResearchClaw — Autonomous Research Pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run the 23-stage research pipeline")
    _ = run_p.add_argument("--topic", "-t", help="Override research topic")
    _ = run_p.add_argument(
        "--config", "-c", default=None,
        help="Config file (default: auto-detect config.arc.yaml or config.yaml)",
    )
    _ = run_p.add_argument("--output", "-o", help="Output directory")
    _ = run_p.add_argument(
        "--from-stage", help="Start from a specific stage (e.g. PAPER_OUTLINE)"
    )
    _ = run_p.add_argument(
        "--to-stage", help="Stop after this stage completes (e.g. EXPERIMENT_DESIGN)"
    )
    _ = run_p.add_argument(
        "--auto-approve", action="store_true", help="Auto-approve gate stages"
    )
    _ = run_p.add_argument(
        "--mode", "-m",
        choices=["full-auto", "gate-only", "checkpoint", "step-by-step",
                 "co-pilot", "express", "thorough", "learning"],
        default=None,
        help="HITL intervention mode (overrides config)",
    )
    _ = run_p.add_argument(
        "--skip-preflight", action="store_true", help="Skip LLM preflight check"
    )
    _ = run_p.add_argument(
        "--resume", action="store_true", help="Resume from last checkpoint"
    )
    _ = run_p.add_argument(
        "--skip-noncritical-stage", action="store_true",
        help="Skip noncritical stages on failure instead of aborting"
    )
    _ = run_p.add_argument(
        "--no-graceful-degradation", action="store_true",
        help="Disable graceful degradation: fail pipeline on quality gate failure"
    )
    _ = run_p.add_argument(
        "--interventions",
        default=None,
        help="Path to scripted HITL interventions JSON file",
    )
    val_p = sub.add_parser("validate", help="Validate config file")
    _ = val_p.add_argument(
        "--config", "-c", default=None,
        help="Config file (default: auto-detect config.arc.yaml or config.yaml)",
    )
    _ = val_p.add_argument(
        "--no-check-paths", action="store_true", help="Skip path existence checks"
    )

    doc_p = sub.add_parser("doctor", help="Check environment and configuration health")
    _ = doc_p.add_argument(
        "--config", "-c", default=None,
        help="Config file (default: auto-detect config.arc.yaml or config.yaml)",
    )
    _ = doc_p.add_argument("--output", "-o", help="Write JSON report to file")

    init_p = sub.add_parser("init", help="Create config.arc.yaml from example template")
    _ = init_p.add_argument(
        "--force", action="store_true", help="Overwrite existing config.arc.yaml"
    )

    _ = sub.add_parser("setup", help="Check and install optional tools (OpenCode, etc.)")

    workbench_p = sub.add_parser("workbench", help="Lightweight workbench commands")
    workbench_sub = workbench_p.add_subparsers(dest="workbench_action")
    wb_search = workbench_sub.add_parser("search", help="Preview literature search")
    _ = wb_search.add_argument("--topic", "-t", required=True, help="Research topic")
    _ = wb_search.add_argument("--limit", type=int, default=10, help="Max results")
    wb_run = workbench_sub.add_parser("run", help="Run pipeline from workbench options")
    _ = wb_run.add_argument("--topic", "-t", required=True, help="Research topic")
    _ = wb_run.add_argument("--output", "-o", default=None, help="Output directory")
    _ = wb_run.add_argument("--provider", default="openai", help="Cloud provider")
    _ = wb_run.add_argument("--model", default="", help="Primary model")
    _ = wb_run.add_argument("--api-key-env", default="", help="API key environment variable")
    _ = wb_run.add_argument("--base-url", default="", help="OpenAI-compatible base URL")
    _ = wb_run.add_argument(
        "--model-mode",
        choices=["cloud", "local"],
        default="cloud",
        help="Use cloud API or local OpenAI-compatible endpoint",
    )
    _ = wb_run.add_argument(
        "--experiment-mode",
        choices=["simulated", "sandbox", "docker", "ssh_remote"],
        default="simulated",
        help="Experiment execution mode",
    )

    _ = sub.add_parser("gui", help="Start the desktop workbench GUI")

    rpt_p = sub.add_parser("report", help="Generate human-readable run report")
    _ = rpt_p.add_argument(
        "--run-dir", required=True, help="Path to run artifacts directory"
    )
    _ = rpt_p.add_argument("--output", "-o", help="Write report to file")

    # A: Web platform
    srv_p = sub.add_parser("serve", help="Start the web server")
    _ = srv_p.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    _ = srv_p.add_argument("--host", default="", help="Host to bind (default from config)")
    _ = srv_p.add_argument("--port", type=int, default=0, help="Port (default from config)")
    _ = srv_p.add_argument("--monitor-dir", help="Artifacts dir to monitor")

    dash_p = sub.add_parser("dashboard", help="Start dashboard-only server")
    _ = dash_p.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    _ = dash_p.add_argument("--host", default="", help="Host to bind")
    _ = dash_p.add_argument("--port", type=int, default=0, help="Port")
    _ = dash_p.add_argument("--monitor-dir", help="Artifacts dir to monitor")

    wiz_p = sub.add_parser("wizard", help="Run the setup wizard")
    _ = wiz_p.add_argument("--output", "-o", help="Write config to file")

    # C1: Multi-project management
    proj_p = sub.add_parser("project", help="Multi-project management")
    _ = proj_p.add_argument(
        "project_action",
        choices=["list", "status", "create", "switch", "compare"],
        help="Project action",
    )
    _ = proj_p.add_argument("--name", "-n", help="Project name")
    _ = proj_p.add_argument("--names", nargs="*", help="Project names (for compare)")
    _ = proj_p.add_argument("--topic", "-t", help="Research topic")
    _ = proj_p.add_argument(
        "--config", "-c", default="config.yaml", help="Config file path"
    )

    # C3: MCP integration
    mcp_p = sub.add_parser("mcp", help="MCP integration")
    _ = mcp_p.add_argument(
        "--start", action="store_true", help="Start MCP server"
    )

    # C4: Overleaf sync
    ovl_p = sub.add_parser("overleaf", help="Overleaf bidirectional sync")
    _ = ovl_p.add_argument("--sync", action="store_true", help="Run sync")
    _ = ovl_p.add_argument("--status", action="store_true", help="Show status")
    _ = ovl_p.add_argument("--run-dir", help="Run artifacts directory")
    _ = ovl_p.add_argument(
        "--config", "-c", default="config.yaml", help="Config file path"
    )

    # D1: Research trend tracking
    trends_p = sub.add_parser("trends", help="Research trend tracking")
    _ = trends_p.add_argument("--digest", action="store_true", help="Generate daily digest")
    _ = trends_p.add_argument("--analyze", action="store_true", help="Analyze trends")
    _ = trends_p.add_argument(
        "--suggest-topics", action="store_true", help="Suggest research topics"
    )
    _ = trends_p.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    _ = trends_p.add_argument("--domains", nargs="+", help="Override domains")

    # Skills management
    sk_p = sub.add_parser("skills", help="List, install, or validate skills")
    _ = sk_p.add_argument("skills_action", nargs="?", default="list",
                          choices=["list", "install", "validate"],
                          help="Action to perform (default: list)")
    _ = sk_p.add_argument("source", nargs="?", default=None,
                          help="Path for install/validate")

    # D4: Conference deadline calendar
    cal_p = sub.add_parser("calendar", help="Conference deadline calendar")
    _ = cal_p.add_argument("--upcoming", action="store_true", help="Show upcoming deadlines")
    _ = cal_p.add_argument("--plan", help="Generate submission timeline for a venue")
    _ = cal_p.add_argument("--domains", nargs="+", help="Filter by domain")

    # HITL: Attach to running pipeline
    attach_p = sub.add_parser("attach", help="Attach to a running/paused pipeline for HITL interaction")
    _ = attach_p.add_argument("run_dir", help="Path to run artifacts directory")

    # HITL: Check pipeline status
    status_p = sub.add_parser("status", help="Show pipeline and HITL status")
    _ = status_p.add_argument("run_dir", help="Path to run artifacts directory")

    # HITL: Approve a gate
    approve_p = sub.add_parser("approve", help="Approve the current HITL gate")
    _ = approve_p.add_argument("run_dir", help="Path to run artifacts directory")
    _ = approve_p.add_argument("--message", "-m", default="", help="Approval note")

    # HITL: Reject a gate
    reject_p = sub.add_parser("reject", help="Reject the current HITL gate")
    _ = reject_p.add_argument("run_dir", help="Path to run artifacts directory")
    _ = reject_p.add_argument("--reason", "-r", default="", help="Rejection reason")

    # HITL: Inject guidance
    guide_p = sub.add_parser("guide", help="Inject guidance for a pipeline stage")
    _ = guide_p.add_argument("run_dir", help="Path to run artifacts directory")
    _ = guide_p.add_argument("--stage", "-s", type=int, required=True, help="Target stage number")
    _ = guide_p.add_argument("--message", "-m", required=True, help="Guidance text")

    args = parser.parse_args(argv)

    command = cast(str | None, args.command)

    if command == "run":
        return cmd_run(args)
    elif command == "validate":
        return cmd_validate(args)
    elif command == "doctor":
        return cmd_doctor(args)
    elif command == "init":
        return cmd_init(args)
    elif command == "setup":
        return cmd_setup(args)
    elif command == "workbench":
        return cmd_workbench(args)
    elif command == "gui":
        return cmd_gui(args)
    elif command == "report":
        return cmd_report(args)
    elif command == "serve":
        return cmd_serve(args)
    elif command == "dashboard":
        return cmd_dashboard(args)
    elif command == "wizard":
        return cmd_wizard(args)
    elif command == "project":
        return cmd_project(args)
    elif command == "mcp":
        return cmd_mcp(args)
    elif command == "overleaf":
        return cmd_overleaf(args)
    elif command == "trends":
        return cmd_trends(args)
    elif command == "calendar":
        return cmd_calendar(args)
    elif command == "skills":
        return cmd_skills(args)
    elif command == "attach":
        return cmd_attach(args)
    elif command == "status":
        return cmd_status(args)
    elif command == "approve":
        return cmd_hitl_approve(args)
    elif command == "reject":
        return cmd_hitl_reject(args)
    elif command == "guide":
        return cmd_hitl_guide(args)
    else:
        parser.print_help()
        return 0


# ---------------------------------------------------------------------------
# HITL subcommands
# ---------------------------------------------------------------------------












if __name__ == "__main__":
    sys.exit(main())

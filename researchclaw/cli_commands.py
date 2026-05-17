"""Command implementations for the ResearchClaw CLI."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from researchclaw.config import EXAMPLE_CONFIG, RCConfig
from researchclaw.llm import cli_provider_menu_lines


def _cli_globals() -> object:
    import researchclaw.cli as cli

    return cli


def cmd_project(args: argparse.Namespace) -> int:
    """C1: Multi-project management commands."""
    from researchclaw.project.manager import ProjectManager

    action = cast(str, args.project_action)
    config_path = Path(cast(str, args.config))
    config = RCConfig.load(config_path, check_paths=False)
    pm = ProjectManager(Path(config.multi_project.projects_dir))

    if action == "list":
        projects = pm.list_all()
        if not projects:
            print("No projects found.")
        for p in projects:
            marker = " *" if pm.active and pm.active.name == p.name else ""
            print(f"  {p.name} [{p.status}]{marker}")
        return 0
    elif action == "status":
        status = pm.get_status()
        print(f"Total projects: {status['total']}")
        print(f"Active: {status.get('active', 'none')}")
        return 0
    elif action == "create":
        name = cast(str, args.name)
        topic = cast(str | None, getattr(args, "topic", None))
        proj = pm.create(name, str(config_path), topic=topic or "")
        print(f"Created project: {proj.name}")
        return 0
    elif action == "switch":
        name = cast(str, args.name)
        pm.switch(name)
        print(f"Switched to project: {name}")
        return 0
    elif action == "compare":
        names = cast(list[str], args.names)
        if len(names) != 2:
            print("Error: compare requires exactly 2 project names", file=sys.stderr)
            return 1
        result = pm.compare(names[0], names[1])
        print(f"Comparing {names[0]} vs {names[1]}:")
        for k, v in result.get("metric_diff", {}).items():
            print(f"  {k}: delta={v['delta']:.4f}")
        return 0
    else:
        print(f"Unknown project action: {action}", file=sys.stderr)
        return 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """C3: MCP integration commands."""
    import asyncio

    start = cast(bool, args.start)
    if start:
        from researchclaw.mcp.server import ResearchClawMCPServer

        server = ResearchClawMCPServer()
        print("Starting MCP server...")
        asyncio.run(server.start())
        return 0
    else:
        from researchclaw.mcp.tools import list_tool_names

        names = list_tool_names()
        print("Available MCP tools:")
        for name in names:
            print(f"  {name}")
        return 0


def cmd_overleaf(args: argparse.Namespace) -> int:
    """C4: Overleaf sync commands."""
    config_path = Path(cast(str, args.config))
    config = RCConfig.load(config_path, check_paths=False)

    if not config.overleaf.enabled:
        print("Overleaf sync is not enabled in config.", file=sys.stderr)
        return 1

    from researchclaw.overleaf.sync import OverleafSync

    sync = OverleafSync(
        git_url=config.overleaf.git_url,
        branch=config.overleaf.branch,
    )

    do_sync = cast(bool, args.sync)
    do_status = cast(bool, args.status)

    if do_status:
        status = sync.get_status()
        for k, v in status.items():
            print(f"  {k}: {v}")
        return 0
    elif do_sync:
        run_dir = Path(cast(str, args.run_dir))
        if not run_dir.exists():
            print(f"Error: run_dir not found: {run_dir}", file=sys.stderr)
            return 1
        sync.setup(run_dir)
        sync.pull_changes()
        print("Overleaf sync complete.")
        return 0
    else:
        print("Use --sync or --status", file=sys.stderr)
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the FastAPI web server."""
    config_path = Path(cast(str, args.config))
    if not config_path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        return 1

    config = RCConfig.load(config_path, check_paths=False)
    host = cast(str, args.host) or config.server.host
    port = int(cast(int, args.port) or config.server.port)

    try:
        import uvicorn

        from researchclaw.server.app import create_app
    except ImportError as exc:
        print(
            f"Error: web dependencies not installed — pip install researchclaw[web]\n{exc}",
            file=sys.stderr,
        )
        return 1

    app = create_app(config, monitor_dir=args.monitor_dir)
    print(f"Open: http://{host}:{port}/")
    print("Auth: use the configured server.auth_token as a Bearer token.")
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start dashboard-only server (no pipeline control)."""
    config_path = Path(cast(str, args.config))
    if not config_path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        return 1

    config = RCConfig.load(config_path, check_paths=False)
    host = cast(str, args.host) or config.server.host
    port = int(cast(int, args.port) or config.server.port)

    try:
        import uvicorn

        from researchclaw.server.app import create_app
    except ImportError as exc:
        print(
            f"Error: web dependencies not installed — pip install researchclaw[web]\n{exc}",
            file=sys.stderr,
        )
        return 1

    app = create_app(config, dashboard_only=True, monitor_dir=args.monitor_dir)
    print(f"Open: http://{host}:{port}/")
    print("Auth: use the configured server.auth_token as a Bearer token.")
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_wizard(args: argparse.Namespace) -> int:
    """Run the interactive setup wizard."""
    from researchclaw.wizard.quickstart import QuickStartWizard

    wizard = QuickStartWizard()
    output = cast(str | None, args.output)

    import yaml

    config = wizard.run_interactive()
    dumped = yaml.safe_dump(config, default_flow_style=False, sort_keys=False)
    if output:
        Path(output).write_text(dumped, encoding="utf-8")
        print(f"Config written to {output}")
    else:
        print(dumped)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    force = cast(bool, args.force)
    dest = Path("config.arc.yaml")

    if dest.exists() and not force:
        print(f"{dest} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    # Look for the example config: first in repo root (relative to package),
    # then in CWD (for development), then bundled in the package data dir.
    _candidates = [
        Path(__file__).resolve().parent.parent / EXAMPLE_CONFIG,  # repo root
        Path.cwd() / EXAMPLE_CONFIG,                              # cwd fallback
        Path(__file__).resolve().parent / "data" / EXAMPLE_CONFIG, # packaged
    ]
    example = next((p for p in _candidates if p.exists()), None)
    if example is None:
        print(
            f"Error: example config not found.\n"
            f"Searched: {', '.join(str(c) for c in _candidates)}",
            file=sys.stderr,
        )
        return 1

    # Interactive provider prompt (TTY only, else default to openai)
    choice = "1"
    if sys.stdin.isatty():
        print("Select LLM provider:")
        for line in cli_provider_menu_lines():
            print(line)
        try:
            raw = input("Choice [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        if raw in _cli_globals()._PROVIDER_CHOICES:
            choice = raw

    provider, api_key_env = _cli_globals()._PROVIDER_CHOICES[choice]

    content = example.read_text(encoding="utf-8")

    # String-based replacement to preserve YAML comments
    content = content.replace(
        'provider: "openai-compatible"', f'provider: "{provider}"'
    )

    if provider == "acp":
        # ACP doesn't need base_url or api_key
        content = content.replace(
            'base_url: "https://api.openai.com/v1"', 'base_url: ""'
        )
        content = content.replace('api_key_env: "OPENAI_API_KEY"', 'api_key_env: ""')
    else:
        base_url = _cli_globals()._PROVIDER_URLS.get(provider, _cli_globals()._PROVIDER_URLS["openai"])
        content = content.replace(
            'base_url: "https://api.openai.com/v1"', f'base_url: "{base_url}"'
        )
        if api_key_env:
            content = content.replace(
                'api_key_env: "OPENAI_API_KEY"', f'api_key_env: "{api_key_env}"'
            )

    if provider in _cli_globals()._PROVIDER_MODELS:
        primary, fallbacks = _cli_globals()._PROVIDER_MODELS[provider]
        content = content.replace('primary_model: "gpt-4o"', f'primary_model: "{primary}"')
        # Replace fallback models block
        old_fallbacks = '  fallback_models:\n    - "gpt-4.1"\n    - "gpt-4o-mini"'
        new_fallbacks = "  fallback_models:\n" + "".join(
            f'    - "{m}"\n' for m in fallbacks
        )
        content = content.replace(old_fallbacks, new_fallbacks.rstrip("\n"))

    dest.write_text(content, encoding="utf-8")
    print(f"Created {dest} (provider: {provider})")

    if provider == "acp":
        print("\nNext steps:")
        print("  1. Ensure your ACP agent is installed and on PATH")
        print("  2. Edit config.arc.yaml to set llm.acp.agent if needed")
        print("  3. Run: researchclaw doctor")
    else:
        env_var = api_key_env or "OPENAI_API_KEY"
        print("\nNext steps:")
        print(f"  1. Export your API key: export {env_var}=sk-...")
        print("  2. Edit config.arc.yaml to customize your settings")
        print("  3. Run: researchclaw doctor")

    # Offer OpenCode installation
    _cli_globals()._prompt_opencode_install()

    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Post-install setup — check and install optional tools."""
    print("ResearchClaw — Environment Setup\n")

    # 1. OpenCode
    if _cli_globals()._is_opencode_installed():
        try:
            opencode_cmd = shutil.which("opencode") or "opencode"
            r = subprocess.run(
                [opencode_cmd, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            ver = r.stdout.strip() or "unknown"
        except Exception:  # noqa: BLE001
            ver = "unknown"
        print(f"  [OK] OpenCode is installed (version: {ver})")
    else:
        installed = _cli_globals()._prompt_opencode_install()
        if installed:
            print("  [OK] OpenCode is now available")
        else:
            print("  [--] OpenCode not installed (beast mode will be unavailable)")

    # 2. Docker (informational)
    print()
    if shutil.which("docker"):
        print("  [OK] Docker is available (sandbox execution enabled)")
    else:
        print("  [--] Docker not found (experiment sandbox unavailable)")
        print("       Install: https://docs.docker.com/get-docker/")

    # 3. LaTeX (informational)
    if shutil.which("pdflatex"):
        print("  [OK] LaTeX is available (PDF paper compilation enabled)")
    else:
        print("  [--] LaTeX not found (paper will be exported as .tex only)")
        print("       Install: sudo apt install texlive-full  (or equivalent)")

    print()
    print("Run 'researchclaw doctor' for a full environment health check.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from researchclaw.report import generate_report, write_report

    run_dir = Path(cast(str, args.run_dir))
    output = cast(str | None, args.output)

    try:
        report = generate_report(run_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(report)
    if output:
        write_report(run_dir, Path(output))
        print(f"\nReport written to {output}")
    return 0


def cmd_trends(args: argparse.Namespace) -> int:
    """Research trend tracking commands."""
    config_path = Path(cast(str, args.config))
    if not config_path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        return 1

    config = RCConfig.load(config_path, check_paths=False)

    import asyncio

    from researchclaw.trends.feeds import FeedManager
    from researchclaw.trends.trend_analyzer import TrendAnalyzer

    domains = cast(list[str] | None, args.domains) or list(config.research.domains)
    if not domains:
        domains = ["machine learning"]

    feed_manager = FeedManager(
        sources=config.trends.sources,
        s2_api_key=os.environ.get(config.llm.s2_api_key_env, ""),
    )

    if cast(bool, args.digest):
        from researchclaw.trends.daily_digest import DailyDigest

        digest = DailyDigest(feed_manager)
        result = asyncio.run(digest.generate(domains, config.trends.max_papers_per_day))
        print(result)
        return 0

    if cast(bool, args.analyze):
        papers = feed_manager.fetch_recent_papers(domains, max_papers=50)
        analyzer = TrendAnalyzer()
        analysis = analyzer.analyze(papers, config.trends.trend_window_days)
        print(analyzer.generate_trend_report(analysis))
        return 0

    if cast(bool, args.suggest_topics):
        from researchclaw.trends.auto_topic import AutoTopicGenerator
        from researchclaw.trends.opportunity_finder import OpportunityFinder

        papers = feed_manager.fetch_recent_papers(domains, max_papers=50)
        analyzer = TrendAnalyzer()
        finder = OpportunityFinder()
        generator = AutoTopicGenerator(analyzer, finder)
        candidates = asyncio.run(generator.generate_candidates(domains, papers))
        print(generator.format_candidates(candidates))
        return 0

    print("Usage: researchclaw trends --digest|--analyze|--suggest-topics")
    return 0


def cmd_calendar(args: argparse.Namespace) -> int:
    """Conference deadline calendar commands."""
    from researchclaw.calendar.deadlines import ConferenceCalendar
    from researchclaw.calendar.planner import SubmissionPlanner

    calendar = ConferenceCalendar.load_builtin()
    domains = cast(list[str] | None, args.domains)

    if cast(bool, args.upcoming):
        print(calendar.format_upcoming(domains=domains))
        return 0

    plan_venue = cast(str | None, args.plan)
    if plan_venue:
        planner = SubmissionPlanner(calendar)
        print(planner.format_plan(plan_venue))
        return 0

    print("Usage: researchclaw calendar --upcoming|--plan <venue>")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    """List, validate, or install skills."""
    from researchclaw.skills.loader import load_skill_from_skillmd
    from researchclaw.skills.registry import SkillRegistry

    action = args.skills_action or "list"
    user_dir = Path.home() / ".researchclaw" / "skills"

    if action == "list":
        # Build full registry to show all available skills
        custom_dirs: list[str] = []
        if user_dir.is_dir():
            custom_dirs.append(str(user_dir))
        project_skills = Path.cwd() / ".claude" / "skills"
        if project_skills.is_dir():
            custom_dirs.append(str(project_skills))

        registry = SkillRegistry(custom_dirs=custom_dirs)
        skills = registry.list_all()
        if not skills:
            print("No skills loaded.")
            return 0

        # Group by category
        by_cat: dict[str, list] = {}
        for s in skills:
            by_cat.setdefault(s.category, []).append(s)
        for cat in sorted(by_cat):
            print(f"\n[{cat}]")
            for s in sorted(by_cat[cat], key=lambda x: x.name):
                stages = ",".join(str(x) for x in s.applicable_stages) if s.applicable_stages else "all"
                src = "builtin"
                if s.source_dir:
                    sd = str(s.source_dir)
                    if ".researchclaw" in sd:
                        src = "user"
                    elif ".claude" in sd:
                        src = "project"
                    elif ".metaclaw" in sd:
                        src = "metaclaw"
                print(f"  {s.name:<35} stages={stages:<12} ({src})")

        print(f"\nTotal: {len(skills)} skills")
        print("\nSkill directories:")
        print("  builtin:  researchclaw/skills/builtin/")
        print(f"  user:     {user_dir}/")
        print("  project:  .claude/skills/")
        return 0

    elif action == "install":
        # Install a skill from a directory or URL
        source = getattr(args, "source", None)
        if not source:
            print("Usage: researchclaw skills install <path-to-skill-dir>")
            return 1
        source_path = Path(source).expanduser().resolve()
        skill_md = source_path / "SKILL.md"
        if not skill_md.exists():
            # Maybe the path IS the SKILL.md
            if source_path.name == "SKILL.md" and source_path.exists():
                source_path = source_path.parent
                skill_md = source_path / "SKILL.md"
            else:
                print(f"Error: no SKILL.md found in {source_path}")
                return 1

        skill = load_skill_from_skillmd(skill_md)
        if not skill:
            print(f"Error: failed to parse {skill_md}")
            return 1

        # Copy to user skills directory
        target = user_dir / skill.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, target, dirs_exist_ok=True)
        print(f"Installed skill '{skill.name}' -> {target}")
        return 0

    elif action == "validate":
        source = getattr(args, "source", None)
        if not source:
            print("Usage: researchclaw skills validate <path-to-SKILL.md>")
            return 1
        path = Path(source).expanduser().resolve()
        if path.is_dir():
            path = path / "SKILL.md"
        if not path.exists():
            print(f"Error: {path} not found")
            return 1
        skill = load_skill_from_skillmd(path)
        if not skill:
            print(f"FAIL: Could not parse {path}")
            return 1
        print(f"OK: {skill.name}")
        print(f"  description: {skill.description[:80]}")
        print(f"  category:    {skill.category}")
        print(f"  stages:      {skill.applicable_stages or 'all'}")
        print(f"  keywords:    {skill.trigger_keywords[:5]}")
        print(f"  body:        {len(skill.body)} chars")
        return 0

    print("Usage: researchclaw skills [list|install|validate]")
    return 1


def cmd_workbench(args: argparse.Namespace) -> int:
    """Run lightweight workbench backend commands."""
    action = cast(str | None, getattr(args, "workbench_action", None))
    if action == "search":
        from researchclaw.workbench.search import search_papers_for_workbench

        topic = cast(str, args.topic)
        limit = cast(int, args.limit)
        papers = search_papers_for_workbench(topic, limit=limit)
        for idx, paper in enumerate(papers, start=1):
            print(f"{idx}. {paper.title}")
            meta = " | ".join(
                str(x)
                for x in (paper.year or "", paper.source, paper.url)
                if str(x).strip()
            )
            if meta:
                print(f"   {meta}")
        return 0

    if action == "run":
        from researchclaw.workbench.run import run_workbench_pipeline

        run_dir = run_workbench_pipeline(
            topic=cast(str, args.topic),
            output=cast(str | None, args.output),
            provider=cast(str, args.provider),
            model=cast(str, args.model),
            api_key_env=cast(str, args.api_key_env),
            base_url=cast(str, args.base_url),
            model_mode=cast(str, args.model_mode),
            experiment_mode=cast(str, args.experiment_mode),
            progress_reporter=print,
        )
        print(f"Run directory: {run_dir}")
        return 0

    print("Usage: researchclaw workbench [search|run]")
    return 1


def cmd_gui(_args: argparse.Namespace) -> int:
    """Start the desktop workbench GUI."""
    from researchclaw.gui.app import main as gui_main

    return int(gui_main())


def cmd_attach(args: argparse.Namespace) -> int:
    """Attach to a running/paused pipeline for interactive HITL."""
    run_dir = Path(cast(str, args.run_dir))
    if not run_dir.is_dir():
        print(f"Error: run directory not found: {run_dir}", file=sys.stderr)
        return 1

    from researchclaw.hitl.store import HITLStore
    from researchclaw.hitl.tui.panel import show_pipeline_status

    store = HITLStore(run_dir)
    waiting = store.load_waiting()

    # Show current status
    session_data = store.load_session()
    mode = session_data.get("mode", "unknown") if session_data else "unknown"
    show_pipeline_status(run_dir, mode=mode)
    print()

    if waiting is None:
        print("  Pipeline is not waiting for input.")
        print("  Use 'researchclaw status' for full details.")
        return 0

    # Pipeline is waiting — enter interactive mode
    print(f"  Pipeline is paused at Stage {waiting['stage']} ({waiting.get('stage_name', '?')})")
    print(f"  Reason: {waiting.get('reason', '?')}")
    print()

    from researchclaw.hitl.adapters.cli_adapter import CLIAdapter
    from researchclaw.hitl.intervention import WaitingState

    ws = WaitingState.from_dict(waiting)
    adapter = CLIAdapter(run_dir=run_dir)
    human_input = adapter.collect_input(ws)

    # Write response for the pipeline process to pick up
    import json

    response_path = run_dir / "hitl" / "response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(human_input.to_dict(), indent=2), encoding="utf-8"
    )
    print("\n  Response saved. Pipeline will pick it up automatically.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show pipeline and HITL status."""
    run_dir = Path(cast(str, args.run_dir))
    if not run_dir.is_dir():
        print(f"Error: run directory not found: {run_dir}", file=sys.stderr)
        return 1

    from researchclaw.hitl.store import HITLStore
    from researchclaw.hitl.tui.panel import show_intervention_log, show_pipeline_status

    store = HITLStore(run_dir)
    session_data = store.load_session()
    mode = session_data.get("mode", "unknown") if session_data else "N/A"

    show_pipeline_status(run_dir, mode=mode)
    print()

    summary = store.get_summary()
    print(f"  HITL interventions: {summary['intervention_count']}")
    print(f"  Chat sessions: stages {summary['chat_stages']}")
    print(f"  Guidance injected: stages {summary['guidance_stages']}")
    print(f"  Snapshots: {summary['snapshot_count']}")

    if store.is_waiting():
        waiting = store.load_waiting()
        if waiting:
            print(f"\n  ⚠ WAITING for input at Stage {waiting['stage']}")
            print(f"    Reason: {waiting.get('reason', '?')}")
            print(f"    Since: {waiting.get('since', '?')}")
            print(f"    Use 'researchclaw attach {run_dir}' to respond.")

    print()
    show_intervention_log(run_dir)
    return 0


def cmd_hitl_approve(args: argparse.Namespace) -> int:
    """Approve the current HITL gate (non-interactive)."""
    run_dir = Path(cast(str, args.run_dir))
    message = cast(str, args.message)

    import json

    response = {"action": "approve", "message": message}
    response_path = run_dir / "hitl" / "response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(response, indent=2), encoding="utf-8"
    )
    print(f"  Approved. Response saved to {response_path}")
    return 0


def cmd_hitl_reject(args: argparse.Namespace) -> int:
    """Reject the current HITL gate (non-interactive)."""
    run_dir = Path(cast(str, args.run_dir))
    reason = cast(str, args.reason)

    import json

    response = {"action": "reject", "message": reason}
    response_path = run_dir / "hitl" / "response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(response, indent=2), encoding="utf-8"
    )
    print(f"  Rejected. Response saved to {response_path}")
    return 0


def cmd_hitl_guide(args: argparse.Namespace) -> int:
    """Inject guidance for a pipeline stage."""
    run_dir = Path(cast(str, args.run_dir))
    stage = cast(int, args.stage)
    message = cast(str, args.message)

    from researchclaw.hitl.store import HITLStore

    store = HITLStore(run_dir)
    store.save_guidance(stage, message)

    # Also write to stage dir
    stage_dir = run_dir / f"stage-{stage:02d}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "hitl_guidance.md").write_text(message, encoding="utf-8")

    print(f"  Guidance saved for Stage {stage} ({len(message)} chars)")
    return 0


__all__ = ['cmd_project', 'cmd_mcp', 'cmd_overleaf', 'cmd_serve', 'cmd_dashboard', 'cmd_wizard', 'cmd_init', 'cmd_setup', 'cmd_report', 'cmd_trends', 'cmd_calendar', 'cmd_skills', 'cmd_workbench', 'cmd_gui', 'cmd_attach', 'cmd_status', 'cmd_hitl_approve', 'cmd_hitl_reject', 'cmd_hitl_guide']

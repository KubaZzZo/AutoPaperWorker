"""HITL collaboration loop helpers for pipeline execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from researchclaw.adapters import AdapterBundle
from researchclaw.config import RCConfig
from researchclaw.pipeline.contracts import CONTRACTS
from researchclaw.pipeline._helpers import StageResult
from researchclaw.pipeline.stages import Stage

OutputFunc = Callable[[str], None]
InputFunc = Callable[[str], str]


def _run_collaboration_loop(
    stage: Stage,
    result: StageResult,
    run_dir: Path,
    adapters: AdapterBundle,
    session: Any,
    *,
    config: RCConfig | None = None,
    output: OutputFunc = print,
    input_func: InputFunc = input,
) -> StageResult:
    """Run an interactive collaboration loop for a stage."""
    from researchclaw.hitl.collaboration import CollaborationSession

    stage_num = int(stage)
    contract = CONTRACTS.get(stage)
    output_files = tuple(contract.output_files) if contract else ()

    collab = CollaborationSession(run_dir=run_dir)

    llm_client = None
    topic = ""
    try:
        if config is not None:
            from researchclaw.llm import create_llm_client

            llm_client = create_llm_client(config)
            topic_obj = getattr(config, "research", None)
            topic = topic_obj.topic if topic_obj else "Research"
        else:
            topic = "Research"
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        topic = "Research"

    collab.initialize(
        stage_num, stage.name, topic, run_dir, artifacts=output_files,
    )

    output(f"\n  Entering collaboration mode for Stage {stage_num} ({stage.name})")
    output("  Commands: 'done' finalize | 'abort' cancel | 'show <file>' view | 'edit <file>' edit | 'files' list")
    output("  Or type a message to chat with AI.\n")

    while True:
        try:
            user_input = input_func("  You > ").strip()
        except (EOFError, KeyboardInterrupt):
            output("")
            break

        if not user_input:
            continue

        lower = user_input.lower()

        if lower in ("done", "approve", "finalize"):
            collab.finalize()
            output("  Collaboration finalized.")
            break

        if lower in ("abort", "quit", "cancel"):
            output("  Collaboration cancelled.")
            break

        if lower == "files":
            for fname in collab.shared_artifacts:
                mod = " [modified]" if fname in collab._modified_artifacts else ""
                output(f"    {fname}{mod}")
            continue

        if lower.startswith("show "):
            fname = user_input[5:].strip()
            if fname in collab.shared_artifacts:
                content = collab.shared_artifacts[fname]
                output(f"\n  --- {fname} ({len(content)} chars) ---")
                output(content[:3000])
                if len(content) > 3000:
                    output(f"  ... ({len(content) - 3000} chars truncated)")
                output(f"  --- end {fname} ---\n")
            else:
                output(f"  File not found: {fname}. Use 'files' to list available artifacts.")
            continue

        if lower.startswith("edit "):
            fname = user_input[5:].strip()
            if fname not in collab.shared_artifacts:
                output(f"  File not found: {fname}. Use 'files' to list available artifacts.")
                continue
            output(f"  Editing {fname}. Paste new content, then type <<<END>>> on its own line:")
            lines = []
            while True:
                try:
                    line = input_func("")
                except (EOFError, KeyboardInterrupt):
                    break
                if line.strip() == "<<<END>>>":
                    break
                lines.append(line)
            new_content = "\n".join(lines)
            collab.human_edits_artifact(fname, new_content)
            output(f"  [{fname} updated - {len(new_content)} chars written]")
            continue

        collab.human_says(user_input)

        if llm_client is not None:
            rev_before = len(collab.revision_history)
            response = collab.ai_responds(llm_client)
            output(f"\n  AI > {response}\n")
            for rev in collab.revision_history[rev_before:]:
                if rev.get("action") == "ai_proposal":
                    output(f"  [AI edited: {rev['file']}]")
        else:
            output("  AI > [LLM not available for chat - your input is recorded]\n")

    return result


__all__ = ["_run_collaboration_loop"]

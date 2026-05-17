"""Application controller for the workbench GUI and CLI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from researchclaw.workbench.cnki_import import CNKIRecord, import_cnki_files
from researchclaw.workbench.cs_project import ProjectAnalysis, analyze_project, create_project_plan
from researchclaw.workbench.models import build_model_config
from researchclaw.workbench.remote import (
    RemoteCommandResult,
    RemoteExecutor,
    RemoteProfile,
    parse_ssh_command,
    save_profile_dict,
)
from researchclaw.workbench.run import build_workbench_config, run_workbench_pipeline
from researchclaw.workbench.search import (
    WorkbenchPaper,
    cnki_search_url,
    search_papers_for_workbench,
)


class WorkbenchController:
    """Thin orchestration layer used by the GUI and CLI."""

    def search(self, topic: str, limit: int = 10) -> list[WorkbenchPaper]:
        return search_papers_for_workbench(topic, limit=limit)

    def cnki_url(self, topic: str) -> str:
        _ = topic
        return cnki_search_url(topic)

    def import_cnki(self, paths: list[str | Path]) -> list[CNKIRecord]:
        return import_cnki_files(paths)

    def analyze_project(self, root: str | Path) -> ProjectAnalysis:
        return analyze_project(root)

    def create_project_plan(self, topic: str) -> dict[str, object]:
        return create_project_plan(topic)

    def build_model_config(
        self,
        *,
        mode: str,
        provider: str = "openai",
        model: str = "",
        base_url: str = "",
        api_key_env: str = "",
    ):
        return build_model_config(
            mode=mode,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
        )

    def build_workbench_config(
        self,
        *,
        topic: str,
        provider: str = "openai",
        model: str = "",
        api_key_env: str = "",
        base_url: str = "",
        model_mode: str = "cloud",
        experiment_mode: str = "simulated",
    ):
        return build_workbench_config(
            topic=topic,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            base_url=base_url,
            model_mode=model_mode,
            experiment_mode=experiment_mode,
        )

    def run_pipeline(
        self,
        *,
        topic: str,
        output: str | Path | None = None,
        provider: str = "openai",
        model: str = "",
        api_key_env: str = "",
        base_url: str = "",
        model_mode: str = "cloud",
        experiment_mode: str = "simulated",
        progress_reporter: Callable[[str], None] | None = None,
    ) -> Path:
        return run_workbench_pipeline(
            topic=topic,
            output=output,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            base_url=base_url,
            model_mode=model_mode,
            experiment_mode=experiment_mode,
            progress_reporter=progress_reporter,
        )

    def parse_remote_profile(
        self,
        command: str,
        *,
        platform: str = "custom",
        password: str = "",
        key_path: str = "",
        # Default path on rented Linux GPU hosts.
        remote_workdir: str = "/tmp/researchclaw_experiments",  # nosec B108
    ) -> RemoteProfile:
        return parse_ssh_command(
            command,
            platform=platform,
            password=password,
            key_path=key_path,
            remote_workdir=remote_workdir,
        )

    def remote_profile_dict(self, profile: RemoteProfile) -> dict[str, object]:
        return save_profile_dict(profile)

    def remote_executor(self, profile: RemoteProfile) -> RemoteExecutor:
        return RemoteExecutor(profile)

    def test_remote(self, profile: RemoteProfile) -> RemoteCommandResult:
        return self.remote_executor(profile).test_connection()

    def upload_remote(
        self,
        profile: RemoteProfile,
        local_dir: str | Path,
        remote_dir: str,
    ) -> RemoteCommandResult:
        return self.remote_executor(profile).upload_code(local_dir, remote_dir)

    def run_remote(
        self,
        profile: RemoteProfile,
        command: str,
        *,
        remote_dir: str = "",
    ) -> RemoteCommandResult:
        return self.remote_executor(profile).run_command(command, remote_dir=remote_dir)

    def download_remote(
        self,
        profile: RemoteProfile,
        remote_dir: str,
        local_dir: str | Path,
    ) -> RemoteCommandResult:
        return self.remote_executor(profile).download_results(remote_dir, local_dir)

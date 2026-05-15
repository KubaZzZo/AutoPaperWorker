"""Remote compute profile helpers for rented GPU machines."""

from __future__ import annotations

import re
import subprocess
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


_AUTO_PARAMIKO = object()


@dataclass(frozen=True)
class RemoteProfile:
    platform: str
    host: str
    user: str = "root"
    port: int = 22
    remote_workdir: str = "/tmp/researchclaw_experiments"
    gpu: str = ""
    vram_gb: int = 0
    key_path: str = ""
    password: str = ""

    @property
    def auth_method(self) -> str:
        return "key" if self.key_path else ("password" if self.password else "agent")


def parse_ssh_command(
    command: str,
    *,
    platform: str = "custom",
    password: str = "",
    key_path: str = "",
    remote_workdir: str = "/tmp/researchclaw_experiments",
) -> RemoteProfile:
    """Parse common SSH command shapes from AutoDL/GPUHome rental pages."""
    parts = command.strip()
    port = 22
    port_match = re.search(r"(?:^|\s)-p\s+(\d+)", parts)
    if port_match:
        port = int(port_match.group(1))
    host_match = re.search(r"([\w.\-]+)@([\w.\-]+)", parts)
    if host_match:
        user, host = host_match.group(1), host_match.group(2)
    else:
        bare = parts.split()[-1]
        user, host = ("root", bare)
    return RemoteProfile(
        platform=platform,
        host=host,
        user=user,
        port=port,
        remote_workdir=remote_workdir,
        key_path=key_path,
        password=password,
    )


def save_profile_dict(profile: RemoteProfile) -> dict[str, object]:
    """Return a redacted dict safe to persist or log."""
    data = asdict(profile)
    data["password"] = ""
    data["auth_method"] = profile.auth_method
    return data


@dataclass(frozen=True)
class RemoteCommandResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class CommandRunner(Protocol):
    def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str, str]: ...


class RemoteExecutor:
    """Small remote compute adapter used by the workbench.

    Key/agent authentication uses system ``ssh`` and ``scp``. Password
    authentication is intentionally isolated behind a Paramiko factory so
    secrets do not enter shell command lines.
    """

    def __init__(
        self,
        profile: RemoteProfile,
        *,
        runner: CommandRunner | None = None,
        paramiko_client_factory: object | None = _AUTO_PARAMIKO,
    ) -> None:
        self.profile = profile
        self._runner = runner or _run_subprocess
        self._paramiko_client_factory = paramiko_client_factory

    def test_connection(self, timeout: int = 10) -> RemoteCommandResult:
        if self.profile.password and not self.profile.key_path:
            return self._paramiko_exec("echo researchclaw-ok", timeout)
        return self._run(self._ssh_base() + ["echo", "researchclaw-ok"], timeout)

    def upload_code(
        self,
        local_dir: str | Path,
        remote_dir: str,
        *,
        timeout: int = 300,
    ) -> RemoteCommandResult:
        if self.profile.password and not self.profile.key_path:
            return self._paramiko_upload(Path(local_dir), remote_dir, timeout)
        local = str(Path(local_dir).resolve())
        remote = f"{self.profile.user}@{self.profile.host}:{remote_dir}"
        return self._run(self._scp_base() + ["-r", local, remote], timeout)

    def run_command(
        self,
        command: str,
        *,
        remote_dir: str = "",
        timeout: int = 3600,
    ) -> RemoteCommandResult:
        if self.profile.password and not self.profile.key_path:
            remote_cmd = f"cd {remote_dir} && {command}" if remote_dir else command
            return self._paramiko_exec(remote_cmd, timeout)
        remote_cmd = f"cd {remote_dir} && {command}" if remote_dir else command
        return self._run(self._ssh_base() + [remote_cmd], timeout)

    def download_results(
        self,
        remote_dir: str,
        local_dir: str | Path,
        *,
        timeout: int = 300,
    ) -> RemoteCommandResult:
        if self.profile.password and not self.profile.key_path:
            return self._paramiko_download(remote_dir, Path(local_dir), timeout)
        local = str(Path(local_dir).resolve())
        remote = f"{self.profile.user}@{self.profile.host}:{remote_dir}"
        return self._run(self._scp_base() + ["-r", remote, local], timeout)

    def _ssh_base(self) -> list[str]:
        cmd = ["ssh", "-p", str(self.profile.port)]
        if self.profile.key_path:
            cmd.extend(["-i", self.profile.key_path])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new", f"{self.profile.user}@{self.profile.host}"])
        return cmd

    def _scp_base(self) -> list[str]:
        cmd = ["scp", "-P", str(self.profile.port)]
        if self.profile.key_path:
            cmd.extend(["-i", self.profile.key_path])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
        return cmd

    def _run(self, cmd: list[str], timeout: int) -> RemoteCommandResult:
        returncode, stdout, stderr = self._runner(cmd, timeout)
        return RemoteCommandResult(
            success=returncode == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )

    def _paramiko_client(self) -> object:
        factory = self._paramiko_client_factory
        if factory is _AUTO_PARAMIKO:
            try:
                import paramiko  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "Paramiko is required for password SSH fallback; use SSH key "
                    "authentication or install `researchclaw[remote]`."
                ) from exc
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif factory is None:
            raise RuntimeError(
                "Paramiko is required for password SSH fallback; use SSH key "
                "authentication or install/configure Paramiko support."
            )
        else:
            client = factory()  # type: ignore[operator]
        client.connect(
            hostname=self.profile.host,
            port=self.profile.port,
            username=self.profile.user,
            password=self.profile.password,
            timeout=10,
        )
        return client

    def _paramiko_exec(self, command: str, timeout: int) -> RemoteCommandResult:
        client = self._paramiko_client()
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)  # type: ignore[attr-defined]
            out_text = _decode_stream(stdout)
            err_text = _decode_stream(stderr)
            return RemoteCommandResult(
                success=not err_text.strip(),
                stdout=out_text,
                stderr=err_text,
                returncode=0 if not err_text.strip() else 1,
            )
        finally:
            client.close()  # type: ignore[attr-defined]

    def _paramiko_upload(
        self,
        local_dir: Path,
        remote_dir: str,
        _timeout: int,
    ) -> RemoteCommandResult:
        client = self._paramiko_client()
        sftp = client.open_sftp()  # type: ignore[attr-defined]
        try:
            _mkdir_p(sftp, remote_dir)
            if local_dir.is_file():
                sftp.put(str(local_dir), f"{remote_dir.rstrip('/')}/{local_dir.name}")
            else:
                for path in local_dir.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(local_dir).as_posix()
                    target = f"{remote_dir.rstrip('/')}/{rel}"
                    _mkdir_p(sftp, str(Path(target).parent).replace("\\", "/"))
                    sftp.put(str(path), target)
            return RemoteCommandResult(success=True)
        finally:
            sftp.close()
            client.close()  # type: ignore[attr-defined]

    def _paramiko_download(
        self,
        remote_dir: str,
        local_dir: Path,
        _timeout: int,
    ) -> RemoteCommandResult:
        client = self._paramiko_client()
        sftp = client.open_sftp()  # type: ignore[attr-defined]
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            _download_tree(sftp, remote_dir, local_dir)
            return RemoteCommandResult(success=True)
        finally:
            sftp.close()
            client.close()  # type: ignore[attr-defined]


def _run_subprocess(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _decode_stream(stream: object) -> str:
    data = stream.read()  # type: ignore[attr-defined]
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return str(data)


def _mkdir_p(sftp: object, path: str) -> None:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    cur = "/" if path.startswith("/") else ""
    for part in parts:
        cur = f"{cur.rstrip('/')}/{part}" if cur else part
        try:
            sftp.mkdir(cur)  # type: ignore[attr-defined]
        except OSError:
            pass


def _download_tree(sftp: object, remote_dir: str, local_dir: Path) -> None:
    try:
        entries = sftp.listdir_attr(remote_dir)  # type: ignore[attr-defined]
    except OSError:
        target = local_dir / Path(remote_dir).name
        sftp.get(remote_dir, str(target))  # type: ignore[attr-defined]
        return

    for entry in entries:
        name = entry.filename
        remote_path = f"{remote_dir.rstrip('/')}/{name}"
        local_path = local_dir / name
        if stat.S_ISDIR(entry.st_mode):
            local_path.mkdir(parents=True, exist_ok=True)
            _download_tree(sftp, remote_path, local_path)
        else:
            sftp.get(remote_path, str(local_path))  # type: ignore[attr-defined]

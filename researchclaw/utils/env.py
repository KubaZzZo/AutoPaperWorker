"""Environment helpers for subprocess isolation."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

_DEFAULT_ALLOWED_ENV_NAMES = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PWD",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)

_SECRET_NAME_MARKERS = (
    "APIKEY",
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def _is_secret_like(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_NAME_MARKERS)


def minimal_subprocess_env(
    *,
    source: Mapping[str, str] | None = None,
    allow_names: Iterable[str] = (),
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a small subprocess environment without ambient credentials.

    The default allow-list keeps OS and PATH variables required for executable
    discovery and temporary-file behavior. Secret-like variable names are
    filtered even when they appear in the default environment.
    """

    env_source = source or os.environ
    allowed = {name.upper() for name in _DEFAULT_ALLOWED_ENV_NAMES}
    allowed.update(name.upper() for name in allow_names)

    result: dict[str, str] = {}
    for name, value in env_source.items():
        if name.upper() not in allowed or _is_secret_like(name):
            continue
        result[name] = value

    if extra:
        for name, value in extra.items():
            result[name] = value

    return result

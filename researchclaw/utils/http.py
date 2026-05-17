"""Small HTTP helpers shared by stdlib urllib clients."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager
from typing import Any, cast


def urlopen_http(
    req: str | urllib.request.Request,
    *,
    timeout: int | float,
) -> AbstractContextManager[Any]:
    """Open only HTTP(S) URLs with ``urllib``.

    ``urllib.request.urlopen`` also supports local files and custom schemes.
    Most ResearchClaw clients expect network-only API calls, so reject anything
    except ``http`` and ``https`` before delegating.
    """
    target = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        raise urllib.error.URLError(f"Unsupported URL scheme: {parsed.scheme}")
    return cast(
        AbstractContextManager[Any],
        urllib.request.urlopen(req, timeout=timeout),  # nosec B310
    )

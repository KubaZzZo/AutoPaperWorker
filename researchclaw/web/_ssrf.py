"""SSRF validation for URLs fetched by the web layer."""

from __future__ import annotations

import ipaddress
import socket
from http.client import HTTPConnection, HTTPSConnection
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPHandler, HTTPSHandler, Request, build_opener


class SSRFBlockedError(URLError):
    """Raised when a URL or connected socket targets a blocked address."""


def _is_blocked_address(value: str) -> bool:
    addr = ipaddress.ip_address(value)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or not addr.is_global
    )


def _blocked_url_error(hostname: str) -> str:
    return f"Blocked internal/private URL: {hostname}"


def validate_connected_peer(sock: Any, hostname: str) -> None:
    """Validate the actual connected peer to close DNS rebinding windows."""
    peer = sock.getpeername()
    peer_host = peer[0] if isinstance(peer, tuple) and peer else ""
    if peer_host and _is_blocked_address(peer_host):
        try:
            sock.close()
        finally:
            raise SSRFBlockedError(_blocked_url_error(hostname))


class _SSRFHTTPConnection(HTTPConnection):
    def connect(self) -> None:
        super().connect()
        validate_connected_peer(self.sock, self.host)


class _SSRFHTTPSConnection(HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        validate_connected_peer(self.sock, self.host)


class _SSRFHTTPHandler(HTTPHandler):
    def http_open(self, req: Request) -> Any:
        err = check_url_ssrf(req.full_url)
        if err:
            raise SSRFBlockedError(err)
        return self.do_open(_SSRFHTTPConnection, req)


class _SSRFHTTPSHandler(HTTPSHandler):
    def https_open(self, req: Request) -> Any:
        err = check_url_ssrf(req.full_url)
        if err:
            raise SSRFBlockedError(err)

        def connection_factory(host: str, **kwargs: Any) -> _SSRFHTTPSConnection:
            return _SSRFHTTPSConnection(
                host,
                context=self._context,
                check_hostname=self._check_hostname,
                **kwargs,
            )

        return self.do_open(connection_factory, req)


_SSRF_OPENER = build_opener(_SSRFHTTPHandler, _SSRFHTTPSHandler)


def check_url_ssrf(url: str) -> str | None:
    """Return an error message if *url* targets a private/internal host.

    The preflight validates scheme, hostname, literal IP addresses, and every
    resolved DNS address. Fetch helpers should still use :func:`ssrf_urlopen`
    so the connected socket peer is checked again after DNS resolution.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme: {parsed.scheme}"
    hostname = parsed.hostname or ""
    if not hostname:
        return "URL has no hostname"

    try:
        if _is_blocked_address(hostname):
            return _blocked_url_error(hostname)
    except ValueError:
        try:
            info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError):
            return None
        for item in info:
            try:
                if _is_blocked_address(item[4][0]):
                    return _blocked_url_error(hostname)
            except (IndexError, ValueError):
                return _blocked_url_error(hostname)
    return None


def ssrf_urlopen(url: str | Request, *args: Any, **kwargs: Any) -> Any:
    """Open a URL with SSRF preflight and connected-peer validation."""
    target = url.full_url if isinstance(url, Request) else str(url)
    err = check_url_ssrf(target)
    if err:
        raise SSRFBlockedError(err)
    return _SSRF_OPENER.open(url, *args, **kwargs)

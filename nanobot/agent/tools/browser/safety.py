"""Safety helpers for browser automation."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
}

_ALLOWED_REQUEST_SCHEMES = {"http", "https", "about", "blob", "data"}


def validate_state_key(state_key: str) -> tuple[bool, str]:
    """Validate state key format used for persisted browser state files."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", state_key):
        return True, ""
    return False, "stateKey must match [A-Za-z0-9_-]{1,64}"


def resolve_path_in_workspace(workspace: Path, raw_path: str, label: str) -> Path:
    """Resolve a path and ensure it stays inside workspace."""
    if not raw_path.strip():
        raise ValueError(f"{label} must not be empty")

    path = Path(raw_path)
    target = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
    workspace_resolved = workspace.resolve()

    if target != workspace_resolved and workspace_resolved not in target.parents:
        raise ValueError(f"{label} must stay within workspace")
    return target


def validate_navigation_url(
    url: str,
    *,
    allow_private_network: bool,
    block_file_scheme: bool,
) -> tuple[bool, str]:
    """Validate top-level navigation URL."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme == "file" and block_file_scheme:
        return False, "file:// URLs are blocked"

    if scheme not in {"http", "https"}:
        return False, f"Only http/https URLs are allowed, got '{scheme or 'none'}'"

    host = parsed.hostname
    if not host:
        return False, "URL host is required"

    if not allow_private_network and is_private_or_local_host(host):
        return False, f"Private/local host blocked: {host}"

    return True, ""


def request_url_block_reason(
    url: str,
    *,
    allow_private_network: bool,
    block_file_scheme: bool,
) -> str | None:
    """Return blocking reason for a network request URL, or None if allowed."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme == "file" and block_file_scheme:
        return "file:// requests are blocked"

    if scheme not in _ALLOWED_REQUEST_SCHEMES:
        return f"Unsupported URL scheme: {scheme or 'none'}"

    if scheme not in {"http", "https"}:
        return None

    host = parsed.hostname
    if not host:
        return "Missing host"

    if not allow_private_network and is_private_or_local_host(host):
        return f"Private/local host blocked: {host}"

    return None


def is_private_or_local_host(host: str) -> bool:
    """Check whether a host is local/private based on hostname or literal IP."""
    normalized = host.rstrip(".").lower()

    if normalized in _LOCAL_HOSTNAMES or normalized.endswith(".local"):
        return True

    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )

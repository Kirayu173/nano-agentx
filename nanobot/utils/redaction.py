"""Utilities for redacting sensitive information from model outputs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


class SensitiveOutputRedactor:
    """Redact sensitive values from text before it is shown to users."""

    PATH_PLACEHOLDER = "[REDACTED_PATH]"
    ENDPOINT_PLACEHOLDER = "[REDACTED_ENDPOINT]"
    SECRET_PLACEHOLDER = "[REDACTED_SECRET]"
    CHAT_ID_PLACEHOLDER = "[REDACTED_CHAT_ID]"

    _CHAT_ID_LINE_RE = re.compile(r"(?im)^(\s*Chat ID:\s*).+$")
    _CHAT_ID_FIELD_RE = re.compile(
        r'(?i)(\bchat[_\s-]?id\b\s*[:=]\s*["\']?)([^"\'\s,}\]]+)'
    )
    _WORKSPACE_LINE_RE = re.compile(r"(?im)^(\s*Your workspace is at:\s*).+$")
    _SESSION_KEY_RE = re.compile(
        r"\b(cli|telegram|discord|whatsapp|feishu|dingtalk|slack|email|qq):([A-Za-z0-9_.@+\-]+)\b"
    )

    _KV_SECRET_RE = re.compile(
        r'(?i)(["\']?(?:api[_-]?key|token|secret|password|client[_-]?secret|authorization)["\']?\s*[:=]\s*["\']?)([^"\'\s,}\]]+)'
    )
    _BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}\b")
    _GENERIC_SK_RE = re.compile(r"\bsk-[A-Za-z0-9._=\-]{8,}\b")
    _SLACK_TOKEN_RE = re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{8,}\b|\bxapp-[A-Za-z0-9\-]{8,}\b")

    _PRIVATE_ENDPOINT_RE = re.compile(
        r"""(?ix)
        \b(?:https?|wss?|socks5)://
        (?:
            localhost |
            127(?:\.\d{1,3}){3} |
            0\.0\.0\.0 |
            10(?:\.\d{1,3}){3} |
            192\.168(?:\.\d{1,3}){2} |
            172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}
        )
        (?::\d{1,5})?
        (?:/[^\s"'`)]*)?
        """
    )
    _PRIVATE_HOSTPORT_RE = re.compile(
        r"""(?ix)
        \b(?:
            localhost |
            127(?:\.\d{1,3}){3} |
            0\.0\.0\.0 |
            10(?:\.\d{1,3}){3} |
            192\.168(?:\.\d{1,3}){2} |
            172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}
        ):\d{1,5}\b
        """
    )

    _TILDE_NANOBOT_PATH_RE = re.compile(r"(?i)~[\\/]\.nanobot(?:[\\/][^\s\"'`]+)*")
    _WINDOWS_ABS_PATH_RE = re.compile(
        r"""(?ix)
        (?<![A-Za-z0-9])
        [A-Z]:[\\/]
        (?:[^\\/\r\n:*?"<>|\s]+[\\/])*
        [^\\/\r\n:*?"<>|\s]*
        """
    )
    _UNIX_ABS_PATH_RE = re.compile(
        r"""(?x)
        (?<![:\w])
        /(?:home|Users|root|etc|var|opt|tmp)
        (?:/[^\s"'`]+)+
        """
    )

    _URL_RE = re.compile(r"(?i)\b(?:https?|wss?|socks5)://[^\s\"'`]+")

    def __init__(
        self,
        enabled: bool = True,
        workspace: Path | None = None,
        config_path: Path | None = None,
        extra_secrets: Iterable[str] | None = None,
    ):
        self.enabled = enabled
        self._literal_paths: set[str] = set()
        self._literal_endpoints: set[str] = set()
        self._literal_secrets: set[str] = set()

        self._add_default_paths(workspace, config_path)
        self._add_extra_secrets(extra_secrets)

    def redact(self, text: str) -> str:
        """Redact sensitive values from text."""
        if not self.enabled or not text:
            return text

        sanitized = text

        sanitized = self._WORKSPACE_LINE_RE.sub(
            rf"\1{self.PATH_PLACEHOLDER}", sanitized
        )
        sanitized = self._CHAT_ID_LINE_RE.sub(
            rf"\1{self.CHAT_ID_PLACEHOLDER}", sanitized
        )
        sanitized = self._CHAT_ID_FIELD_RE.sub(
            rf"\1{self.CHAT_ID_PLACEHOLDER}", sanitized
        )
        sanitized = self._SESSION_KEY_RE.sub(
            lambda m: f"{m.group(1)}:{self.CHAT_ID_PLACEHOLDER}", sanitized
        )

        sanitized = self._replace_literals(
            sanitized, self._literal_secrets, self.SECRET_PLACEHOLDER
        )
        sanitized = self._replace_literals(
            sanitized, self._literal_endpoints, self.ENDPOINT_PLACEHOLDER
        )
        sanitized = self._replace_literals(
            sanitized, self._literal_paths, self.PATH_PLACEHOLDER
        )

        sanitized = self._KV_SECRET_RE.sub(rf"\1{self.SECRET_PLACEHOLDER}", sanitized)
        sanitized = self._BEARER_RE.sub(
            f"Bearer {self.SECRET_PLACEHOLDER}", sanitized
        )
        sanitized = self._GENERIC_SK_RE.sub(self.SECRET_PLACEHOLDER, sanitized)
        sanitized = self._SLACK_TOKEN_RE.sub(self.SECRET_PLACEHOLDER, sanitized)

        sanitized = self._PRIVATE_ENDPOINT_RE.sub(self.ENDPOINT_PLACEHOLDER, sanitized)
        sanitized = self._PRIVATE_HOSTPORT_RE.sub(self.ENDPOINT_PLACEHOLDER, sanitized)
        sanitized = self._URL_RE.sub(self._replace_url_if_private, sanitized)

        sanitized = self._TILDE_NANOBOT_PATH_RE.sub(self.PATH_PLACEHOLDER, sanitized)
        sanitized = self._WINDOWS_ABS_PATH_RE.sub(
            self.PATH_PLACEHOLDER, sanitized
        )
        sanitized = self._UNIX_ABS_PATH_RE.sub(self.PATH_PLACEHOLDER, sanitized)

        return sanitized

    def _add_default_paths(self, workspace: Path | None, config_path: Path | None) -> None:
        home = Path.home().expanduser()
        data_dir = home / ".nanobot"
        self._add_path_literal(data_dir)

        cfg = config_path.expanduser() if config_path else (data_dir / "config.json")
        self._add_path_literal(cfg)

        if workspace is not None:
            self._add_path_literal(workspace.expanduser())

    def _add_extra_secrets(self, values: Iterable[str] | None) -> None:
        if not values:
            return
        for raw in values:
            if not raw:
                continue
            value = str(raw).strip()
            if not value:
                continue
            if self._looks_like_endpoint(value):
                self._literal_endpoints.add(value)
            elif len(value) >= 6:
                self._literal_secrets.add(value)

    def _add_path_literal(self, path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)

        if not resolved:
            return

        self._literal_paths.add(resolved)
        self._literal_paths.add(resolved.replace("\\", "/"))

    @staticmethod
    def _replace_literals(text: str, values: Iterable[str], placeholder: str) -> str:
        sanitized = text
        for value in sorted(set(values), key=len, reverse=True):
            if not value:
                continue
            sanitized = sanitized.replace(value, placeholder)
            if "\\" in value:
                sanitized = sanitized.replace(value.replace("\\", "\\\\"), placeholder)
        return sanitized

    @staticmethod
    def _looks_like_endpoint(value: str) -> bool:
        lower = value.lower()
        if "://" in lower:
            return True
        if any(lower.startswith(prefix) for prefix in ("localhost", "127.", "0.0.0.0")):
            return True
        return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?", value))

    def _replace_url_if_private(self, match: re.Match[str]) -> str:
        url = match.group(0)
        if self._PRIVATE_ENDPOINT_RE.fullmatch(url):
            return self.ENDPOINT_PLACEHOLDER
        if url in self._literal_endpoints:
            return self.ENDPOINT_PLACEHOLDER
        return url

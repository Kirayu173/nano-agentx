"""Codex CLI client."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from nanobot.config.schema import CodexToolConfig

MODES = ("exec", "review")
SANDBOXES = ("read-only", "workspace-write", "danger-full-access")


class CodexClient:
    """Execute Codex CLI in non-interactive mode and normalize outputs."""

    def __init__(
        self,
        workspace: Path,
        codex_config: CodexToolConfig | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.workspace = workspace.resolve()
        self.config = codex_config or CodexToolConfig()
        self.restrict_to_workspace = restrict_to_workspace

    async def run(
        self,
        *,
        prompt: str,
        mode: str = "exec",
        working_dir: str | None = None,
        sandbox: str | None = None,
        model: str | None = None,
        timeout_sec: int | None = None,
    ) -> dict[str, Any]:
        selected_mode = (mode or "exec").strip().lower()
        if selected_mode not in MODES:
            return self._error("invalid_mode", f"mode must be one of {MODES}")

        task = (prompt or "").strip()
        if not task:
            return self._error("invalid_prompt", "prompt must not be empty")

        try:
            cwd = self._resolve_working_dir(working_dir)
        except (ValueError, PermissionError) as exc:
            return self._error("invalid_working_dir", str(exc))

        selected_sandbox = (sandbox or self.config.default_sandbox).strip().lower()
        if selected_sandbox not in SANDBOXES:
            return self._error("invalid_sandbox", f"sandbox must be one of {SANDBOXES}")

        full_access = bool(self.config.allow_dangerous_full_access)
        effective_sandbox = "danger-full-access" if full_access else selected_sandbox

        if selected_sandbox == "danger-full-access" and not full_access:
            return self._error(
                "dangerous_full_access_not_allowed",
                "danger-full-access requires tools.codex.allowDangerousFullAccess=true",
            )

        if effective_sandbox == "workspace-write" and not self.config.allow_workspace_write:
            return self._error(
                "workspace_write_not_allowed",
                "workspace-write sandbox is disabled by tools.codex.allowWorkspaceWrite",
            )

        timeout = timeout_sec if timeout_sec is not None else self.config.timeout
        if timeout <= 0:
            return self._error("invalid_timeout", "timeout_sec must be >= 1")

        command = self._resolve_command()
        if not command:
            return self._error("command_not_found", f"Codex command not found: {self.config.command}")

        cmd = self._build_command(
            command=command,
            mode=selected_mode,
            prompt=task,
            sandbox=effective_sandbox,
            full_access=full_access,
            cwd=cwd,
            model=model,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        except FileNotFoundError:
            return self._error("command_not_found", f"Codex command not found: {self.config.command}")
        except Exception as exc:  # pragma: no cover - defensive path
            return self._error("spawn_failed", str(exc))

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await process.communicate()
            except Exception:
                pass
            return self._error("timeout", f"codex_run timed out after {timeout} seconds")

        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace").strip()
        parsed = self._parse_jsonl(stdout)

        message, message_truncated = self._truncate(parsed["message"] or "")
        stderr_text, stderr_truncated = self._truncate(stderr)

        if process.returncode != 0:
            return self._error(
                "codex_failed",
                message or stderr_text or f"Codex exited with code {process.returncode}",
                exit_code=process.returncode,
                thread_id=parsed["thread_id"],
                usage=parsed["usage"],
                stderr=stderr_text or None,
                stderr_truncated=stderr_truncated if stderr_text else None,
            )

        if not message:
            error_msg = "No final agent_message found in Codex output"
            if parsed["parse_errors"] > 0:
                error_msg = "Failed to parse Codex JSON output"
            return self._error(
                "invalid_output",
                error_msg,
                thread_id=parsed["thread_id"],
                usage=parsed["usage"],
                stderr=stderr_text or None,
                stderr_truncated=stderr_truncated if stderr_text else None,
            )

        payload: dict[str, Any] = {
            "ok": True,
            "mode": selected_mode,
            "sandbox": effective_sandbox,
            "thread_id": parsed["thread_id"],
            "message": message,
            "usage": parsed["usage"] or {},
            "message_truncated": message_truncated,
        }
        if stderr_text:
            payload["stderr"] = stderr_text
            payload["stderr_truncated"] = stderr_truncated
        return payload

    def _resolve_working_dir(self, working_dir: str | None) -> Path:
        if not working_dir:
            return self.workspace

        raw = Path(working_dir).expanduser()
        path = (self.workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()

        if self.restrict_to_workspace and self.workspace not in path.parents and path != self.workspace:
            raise PermissionError(f"working_dir {path} is outside workspace {self.workspace}")

        if not path.exists():
            raise ValueError(f"working_dir does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"working_dir is not a directory: {path}")
        return path

    def _resolve_command(self) -> str | None:
        command = (self.config.command or "").strip()
        if not command:
            return None

        resolved = shutil.which(command)
        if resolved:
            return resolved

        command_path = Path(command).expanduser()
        if command_path.exists():
            return str(command_path.resolve())
        return None

    def _build_command(
        self,
        *,
        command: str,
        mode: str,
        prompt: str,
        sandbox: str,
        full_access: bool,
        cwd: Path,
        model: str | None,
    ) -> list[str]:
        cmd = [command, "exec"]
        if mode == "review":
            cmd.append("review")

        cmd.extend(["--json", "-c", 'approval_policy="never"'])

        if full_access:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(["--sandbox", sandbox])

        cmd.extend(["-C", str(cwd)])
        if mode == "exec":
            cmd.append("--skip-git-repo-check")
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        return cmd

    def _parse_jsonl(self, text: str) -> dict[str, Any]:
        thread_id: str | None = None
        message: str | None = None
        usage: dict[str, Any] | None = None
        parse_errors = 0

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            if not isinstance(event, dict):
                continue

            event_type = event.get("type")
            if event_type == "thread.started" and isinstance(event.get("thread_id"), str):
                thread_id = event["thread_id"]
                continue

            if event_type == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        message = text_value
                continue

            if event_type == "turn.completed":
                event_usage = event.get("usage")
                if isinstance(event_usage, dict):
                    usage = event_usage

        return {
            "thread_id": thread_id,
            "message": message,
            "usage": usage,
            "parse_errors": parse_errors,
        }

    def _truncate(self, text: str) -> tuple[str, bool]:
        if not text:
            return "", False
        limit = max(1, int(self.config.max_output_chars))
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    @staticmethod
    def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        }
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
        return payload

"""Codex run tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.codex.client import MODES, SANDBOXES, CodexClient
from nanobot.config.schema import CodexToolConfig


class CodexRunTool(Tool):
    """Execute Codex CLI tasks in non-interactive mode."""

    name = "codex_run"
    description = (
        "Run Codex CLI non-interactively for coding tasks. "
        "Supports exec and review mode. "
        "When allowDangerousFullAccess is enabled, full access is applied automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Task instructions for Codex",
                "minLength": 1,
            },
            "mode": {
                "type": "string",
                "enum": list(MODES),
                "description": "Run mode: exec for general tasks, review for code review",
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory for Codex (relative paths are under workspace)",
            },
            "sandbox": {
                "type": "string",
                "enum": list(SANDBOXES),
                "description": "Codex sandbox mode",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for Codex",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7200,
                "description": "Optional timeout override in seconds",
            },
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        workspace: Path,
        codex_config: CodexToolConfig | None = None,
        restrict_to_workspace: bool = False,
        client: CodexClient | None = None,
    ):
        self._client = client or CodexClient(
            workspace=workspace,
            codex_config=codex_config,
            restrict_to_workspace=restrict_to_workspace,
        )

    async def execute(
        self,
        prompt: str,
        mode: str = "exec",
        working_dir: str | None = None,
        sandbox: str | None = None,
        model: str | None = None,
        timeout_sec: int | None = None,
        **kwargs: Any,
    ) -> str:
        payload = await self._client.run(
            prompt=prompt,
            mode=mode,
            working_dir=working_dir,
            sandbox=sandbox,
            model=model,
            timeout_sec=timeout_sec,
        )
        return json.dumps(payload, ensure_ascii=False)

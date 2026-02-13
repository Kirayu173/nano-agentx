"""Tool registry factories shared by main agent and subagent."""

from pathlib import Path
from typing import Any, Awaitable, Callable

from nanobot.agent.tools.browser import BrowserRunTool
from nanobot.agent.tools.codex import CodexMergeTool, CodexRunTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.todo import TodoTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import OutboundMessage
from nanobot.config.schema import BrowserToolConfig, CodexToolConfig, ExecToolConfig, WebSearchConfig


def _register_common_tools(
    registry: ToolRegistry,
    *,
    workspace: Path,
    restrict_to_workspace: bool,
    exec_config: ExecToolConfig,
    codex_config: CodexToolConfig,
    web_search_config: WebSearchConfig,
    web_browser_config: BrowserToolConfig,
) -> None:
    """Register tools shared by main agent and subagent."""
    allowed_dir = workspace if restrict_to_workspace else None

    registry.register(ReadFileTool(allowed_dir=allowed_dir, workspace=workspace))
    registry.register(WriteFileTool(allowed_dir=allowed_dir, workspace=workspace))
    registry.register(EditFileTool(allowed_dir=allowed_dir, workspace=workspace))
    registry.register(ListDirTool(allowed_dir=allowed_dir, workspace=workspace))
    registry.register(
        ExecTool(
            working_dir=str(workspace),
            timeout=exec_config.timeout,
            restrict_to_workspace=restrict_to_workspace,
        )
    )

    if codex_config.enabled:
        registry.register(
            CodexRunTool(
                workspace=workspace,
                codex_config=codex_config,
                restrict_to_workspace=restrict_to_workspace,
            )
        )
        registry.register(
            CodexMergeTool(
                workspace=workspace,
                codex_config=codex_config,
                restrict_to_workspace=restrict_to_workspace,
            )
        )

    registry.register(WebSearchTool(web_search_config=web_search_config))
    registry.register(WebFetchTool())

    if web_browser_config.enabled:
        registry.register(
            BrowserRunTool(
                workspace=workspace,
                web_browser_config=web_browser_config,
            )
        )

    registry.register(TodoTool(workspace=workspace))


def build_main_agent_tool_registry(
    *,
    workspace: Path,
    restrict_to_workspace: bool,
    exec_config: ExecToolConfig,
    codex_config: CodexToolConfig,
    web_search_config: WebSearchConfig,
    web_browser_config: BrowserToolConfig,
    message_send_callback: Callable[[OutboundMessage], Awaitable[None]],
    spawn_manager: Any,
    cron_service: Any | None,
) -> ToolRegistry:
    """Build tool registry for main agent loop."""
    registry = ToolRegistry()
    _register_common_tools(
        registry,
        workspace=workspace,
        restrict_to_workspace=restrict_to_workspace,
        exec_config=exec_config,
        codex_config=codex_config,
        web_search_config=web_search_config,
        web_browser_config=web_browser_config,
    )

    registry.register(MessageTool(send_callback=message_send_callback))
    registry.register(SpawnTool(manager=spawn_manager))
    if cron_service:
        registry.register(CronTool(cron_service))
    return registry


def build_subagent_tool_registry(
    *,
    workspace: Path,
    restrict_to_workspace: bool,
    exec_config: ExecToolConfig,
    codex_config: CodexToolConfig,
    web_search_config: WebSearchConfig,
    web_browser_config: BrowserToolConfig,
) -> ToolRegistry:
    """Build tool registry for subagent runs."""
    registry = ToolRegistry()
    _register_common_tools(
        registry,
        workspace=workspace,
        restrict_to_workspace=restrict_to_workspace,
        exec_config=exec_config,
        codex_config=codex_config,
        web_search_config=web_search_config,
        web_browser_config=web_browser_config,
    )
    return registry

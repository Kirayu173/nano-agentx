"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.runtime.outbound_policy import OutboundPolicy
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.factory import build_main_agent_tool_registry
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import get_config_path
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.redaction import SensitiveOutputRedactor

if TYPE_CHECKING:
    from nanobot.config.schema import (
        BrowserToolConfig,
        ChannelsConfig,
        CodexToolConfig,
        ExecToolConfig,
        WebSearchConfig,
    )
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RECENT_IMAGE_META_KEY = "_recent_image_context"
    _RECENT_IMAGE_FOLLOWUP_TURNS = 2
    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        brave_api_key: str | None = None,
        web_search_config: WebSearchConfig | None = None,
        web_browser_config: BrowserToolConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        codex_config: CodexToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        redact_sensitive_output: bool = True,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
    ):
        from nanobot.config.schema import (
            BrowserToolConfig,
            CodexToolConfig,
            ExecToolConfig,
            WebSearchConfig,
        )

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window

        self.web_search_config = web_search_config or WebSearchConfig()
        resolved_brave_key = (
            brave_api_key
            or self.web_search_config.providers.brave.api_key
            or self.web_search_config.api_key
            or None
        )
        if resolved_brave_key:
            self.web_search_config.api_key = resolved_brave_key
            self.web_search_config.providers.brave.api_key = resolved_brave_key
        self.brave_api_key = resolved_brave_key

        self.web_browser_config = web_browser_config or BrowserToolConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.codex_config = codex_config or CodexToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.redactor = SensitiveOutputRedactor(
            enabled=redact_sensitive_output,
            workspace=workspace,
            config_path=get_config_path(),
            extra_secrets=[provider.api_key or "", provider.api_base or ""],
        )
        self.outbound_policy = OutboundPolicy(
            workspace=workspace,
            redactor=self.redactor,
            recent_image_meta_key=self._RECENT_IMAGE_META_KEY,
            recent_image_followup_turns=self._RECENT_IMAGE_FOLLOWUP_TURNS,
        )

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=self.brave_api_key,
            web_search_config=self.web_search_config,
            web_browser_config=self.web_browser_config,
            exec_config=self.exec_config,
            codex_config=self.codex_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        self.tools = build_main_agent_tool_registry(
            workspace=self.workspace,
            restrict_to_workspace=self.restrict_to_workspace,
            exec_config=self.exec_config,
            codex_config=self.codex_config,
            web_search_config=self.web_search_config,
            web_browser_config=self.web_browser_config,
            message_send_callback=self._publish_outbound_safe,
            spawn_manager=self.subagents,
            cron_service=self.cron_service,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()
        self._consolidation_tasks: set[asyncio.Task] = set()
        self._consolidation_locks: dict[str, asyncio.Lock] = {}

    def _redact_text(self, content: str | None) -> str:
        """Apply output redaction policy to text."""
        return self.outbound_policy.redact_text(content)

    def _normalize_media_paths(self, media: list[str] | None) -> list[str]:
        """Normalize outbound media paths to absolute paths."""
        return self.outbound_policy.normalize_media_paths(media)

    @staticmethod
    def _ensure_session_metadata(session: Any) -> dict[str, Any]:
        """Return mutable metadata dict for a session, creating it if needed."""
        return OutboundPolicy._ensure_session_metadata(session)

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """Check whether path points to a readable image file."""
        return OutboundPolicy._is_image_file(path)

    def _extract_latest_image(self, media: list[str] | None) -> str | None:
        """Pick the latest image path from a media list."""
        return self.outbound_policy.extract_latest_image(media)

    def _remember_recent_image(self, session: Any, image_path: str) -> None:
        """Store latest image for short follow-up reuse."""
        self.outbound_policy.remember_recent_image(session, image_path)

    def _consume_recent_image(self, session: Any) -> str | None:
        """Reuse recent image for one turn and decrement remaining turns."""
        return self.outbound_policy.consume_recent_image(session)

    def _redact_outbound(self, msg: OutboundMessage) -> OutboundMessage:
        """Return a copy of outbound message with redacted content."""
        return self.outbound_policy.redact_outbound(msg)

    async def _publish_outbound_safe(self, msg: OutboundMessage) -> None:
        """Publish outbound messages after redacting sensitive content."""
        await self.bus.publish_outbound(self._redact_outbound(msg))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id, message_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>...</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search(\"query\")'."""

        def _fmt(tc):
            val = next(iter(tc.arguments.values()), None) if tc.arguments else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}...")' if len(val) > 40 else f'{tc.name}("{val}")'

        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if response.has_tool_calls:
                if on_progress:
                    clean = self._strip_think(response.content)
                    if clean:
                        await on_progress(self._redact_text(clean))
                    await on_progress(self._redact_text(self._tool_hint(response.tool_calls)), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    safe_args = self._redact_text(args_str)
                    logger.info("Tool call: {}({})", tool_call.name, safe_args[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(messages, tool_call.id, tool_call.name, result)
            else:
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    reasoning_content=response.reasoning_content,
                )
                final_content = self._strip_think(response.content)
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                try:
                    response = await self._process_message(msg)
                    if response is not None:
                        await self._publish_outbound_safe(response)
                    elif msg.channel == "cli":
                        await self._publish_outbound_safe(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata=msg.metadata or {},
                            )
                        )
                except Exception as e:
                    logger.error("Error processing message: {}", e)
                    await self._publish_outbound_safe(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )
            except asyncio.TimeoutError:
                continue

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    def _get_consolidation_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._consolidation_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._consolidation_locks[session_key] = lock
        return lock

    def _prune_consolidation_lock(self, session_key: str, lock: asyncio.Lock) -> None:
        """Drop lock entry if no longer in use."""
        if not lock.locked():
            self._consolidation_locks.pop(session_key, None)

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        if msg.channel == "system":
            channel, chat_id = msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, (msg.metadata or {}).get("message_id"))
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history), redact_user=True)
            self.sessions.save(session)
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=self._redact_text(final_content or "Background task completed."),
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._get_consolidation_lock(session.key)
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot_start = int(getattr(session, "last_consolidated", 0) or 0)
                    snapshot = session.messages[snapshot_start:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)
                self._prune_consolidation_lock(session.key, lock)

            if hasattr(session, "clear"):
                session.clear()
            else:
                session.messages = []
                if hasattr(session, "last_consolidated"):
                    session.last_consolidated = 0
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="New session started.")
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="nanobot commands:\n/new - Start a new conversation\n/help - Show available commands",
            )

        last_consolidated = int(getattr(session, "last_consolidated", 0) or 0)
        unconsolidated = len(session.messages) - last_consolidated
        if unconsolidated >= self.memory_window and session.key not in self._consolidating:
            self._consolidating.add(session.key)
            lock = self._get_consolidation_lock(session.key)

            if hasattr(session, "last_consolidated"):
                async def _consolidate_and_unlock():
                    try:
                        async with lock:
                            await self._consolidate_memory(session)
                    finally:
                        self._consolidating.discard(session.key)
                        self._prune_consolidation_lock(session.key, lock)
                        _task = asyncio.current_task()
                        if _task is not None:
                            self._consolidation_tasks.discard(_task)

                _task = asyncio.create_task(_consolidate_and_unlock())
                self._consolidation_tasks.add(_task)
            else:
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    self._prune_consolidation_lock(session.key, lock)

        incoming_media = self._normalize_media_paths(msg.media)
        effective_media = list(incoming_media)
        latest_image = self._extract_latest_image(incoming_media)
        if latest_image:
            self._remember_recent_image(session, latest_image)
        else:
            recent_image = self._consume_recent_image(session)
            if recent_image and recent_image not in effective_media:
                effective_media.append(recent_image)

        self._set_tool_context(msg.channel, msg.chat_id, (msg.metadata or {}).get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=effective_media if effective_media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            if self.channels_config:
                if tool_hint and not self.channels_config.send_tool_hints:
                    return
                if not tool_hint and not self.channels_config.send_progress:
                    return
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self._publish_outbound_safe(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        progress_callback = on_progress
        if progress_callback is None and self.channels_config:
            progress_callback = _bus_progress

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_progress=progress_callback,
        )

        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
                if final_content is None or not final_content.strip():
                    self._save_turn(session, all_msgs, 1 + len(history))
                    self.sessions.save(session)
                    return None

        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        final_content = self._redact_text(final_content)

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(
        self,
        session: Session,
        messages: list[dict[str, Any]],
        skip: int,
        *,
        redact_user: bool = False,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for msg in messages[skip:]:
            entry = {k: v for k, v in msg.items() if k != "reasoning_content"}
            content = entry.get("content")
            if isinstance(content, str):
                if entry.get("role") == "tool" and len(content) > self._TOOL_RESULT_MAX_CHARS:
                    content = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                if redact_user or entry.get("role") != "user":
                    content = self._redact_text(content)
                entry["content"] = content
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session: Session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session,
            self.provider,
            self.model,
            archive_all=archive_all,
            memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return self._redact_text(response.content if response else "")

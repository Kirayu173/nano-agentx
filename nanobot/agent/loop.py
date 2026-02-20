"""Agent loop: the core processing engine."""

import asyncio
from contextlib import AsyncExitStack
import json
import json_repair
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.runtime.outbound_policy import OutboundPolicy
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.factory import build_main_agent_tool_registry
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import get_config_path
from nanobot.config.schema import BrowserToolConfig, CodexToolConfig, ExecToolConfig, WebSearchConfig
from nanobot.cron.service import CronService
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.redaction import SensitiveOutputRedactor


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
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        memory_window: int = 50,
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
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.web_search_config = web_search_config or WebSearchConfig()
        if self.brave_api_key:
            self.web_search_config.providers.brave.api_key = self.brave_api_key
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
        self.tools = ToolRegistry()
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
        
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
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
        if self._mcp_connected or not self._mcp_servers:
            return
        self._mcp_connected = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        self._mcp_stack = AsyncExitStack()
        await self._mcp_stack.__aenter__()
        await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for tools that require routing info."""
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(channel, chat_id, message_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(channel, chat_id)

        cron_tool = self.tools.get("cron")
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
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            val = next(iter(tc.arguments.values()), None) if tc.arguments else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}...")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str]]:
        """
        Run the agent iteration loop.

        Args:
            initial_messages: Starting messages for the LLM conversation.
            on_progress: Optional callback to push intermediate content to the user.

        Returns:
            Tuple of (final_content, list_of_tools_used).
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        text_only_retried = False

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
                    await on_progress(self._redact_text(self._tool_hint(response.tool_calls)))

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
                    messages = self.context.add_tool_result(
                        messages,
                        tool_call.id,
                        tool_call.name,
                        result,
                    )
            else:
                final_content = self._strip_think(response.content)
                # Some models send an interim text response before tool calls.
                # Give them one retry; don't forward the text to avoid duplicates.
                if on_progress and not tools_used and not text_only_retried and final_content:
                    text_only_retried = True
                    logger.debug("Interim text response (no tools used yet), retrying: {}", final_content[:80])
                    messages = self.context.add_assistant_message(
                        messages,
                        response.content,
                        reasoning_content=response.reasoning_content,
                    )
                    final_content = None
                    continue
                break

        if final_content is None and iteration >= self.max_iterations:
            final_content = f"Reached {self.max_iterations} iterations without completion."

        return final_content, tools_used

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self._publish_outbound_safe(response)
                except Exception as e:
                    logger.error("Error processing message: {}", e)
                    await self._publish_outbound_safe(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).
            on_progress: Optional callback for intermediate output.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        
        # Get or create session
        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Handle slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            messages_to_archive = session.messages.copy()
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            async def _consolidate_and_cleanup() -> None:
                temp_session = Session(key=session.key)
                temp_session.messages = messages_to_archive
                await self._consolidate_memory(temp_session, archive_all=True)

            asyncio.create_task(_consolidate_and_cleanup())
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="New session started. Memory consolidation in progress.",
            )
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="nanobot commands:\n/new - Start a new conversation\n/help - Show available commands",
            )

        if len(session.messages) > self.memory_window and session.key not in self._consolidating:
            self._consolidating.add(session.key)
            try:
                await self._consolidate_memory(session)
            finally:
                self._consolidating.discard(session.key)

        # Keep latest image context for 1-2 follow-up turns in the same session.
        incoming_media = self._normalize_media_paths(msg.media)
        effective_media = list(incoming_media)
        latest_image = self._extract_latest_image(incoming_media)
        if latest_image:
            self._remember_recent_image(session, latest_image)
        else:
            recent_image = self._consume_recent_image(session)
            if recent_image and recent_image not in effective_media:
                effective_media.append(recent_image)
        
        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))

        # Build initial messages (use get_history for LLM-formatted messages)
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            media=effective_media if effective_media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        final_content, tools_used = await self._run_agent_loop(initial_messages, on_progress=on_progress)
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        final_content = self._redact_text(final_content)
        
        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")
        
        # Save to session
        session.add_message("user", msg.content)
        session.add_message(
            "assistant",
            final_content,
            tools_used=tools_used if tools_used else None,
        )
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info("Processing system message from {}", msg.sender_id)
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        self._set_tool_context(origin_channel, origin_chat_id, msg.metadata.get("message_id"))

        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )

        final_content, tools_used = await self._run_agent_loop(initial_messages)
        if final_content is None:
            final_content = "Background task completed."
        final_content = self._redact_text(final_content)
        
        # Save to session (mark as system message in history)
        system_msg = self._redact_text(f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("user", system_msg)
        session.add_message(
            "assistant",
            final_content,
            tools_used=tools_used if tools_used else None,
        )
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    async def _consolidate_memory(self, session: Any, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md."""
        if not getattr(session, "messages", None):
            return

        memory = MemoryStore(self.workspace)
        supports_offset = hasattr(session, "last_consolidated")
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} total messages archived", len(session.messages))
        else:
            keep_count = max(2, self.memory_window // 2)
            if len(session.messages) <= keep_count:
                logger.debug("Session {}: No consolidation needed (messages={}, keep={})", session.key, len(session.messages), keep_count)
                return

            last_consolidated = int(getattr(session, "last_consolidated", 0) or 0)
            messages_to_process = len(session.messages) - last_consolidated
            if messages_to_process <= 0:
                logger.debug(
                    "Session {}: No new messages to consolidate (last_consolidated={}, total={})",
                    session.key,
                    last_consolidated,
                    len(session.messages),
                )
                return

            old_messages = session.messages[last_consolidated:-keep_count]
            if not old_messages:
                return
            logger.info("Memory consolidation started: {} total, {} new to consolidate, {} keep", len(session.messages), len(old_messages), keep_count)

        lines = []
        for message in old_messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        conversation = "\n".join(lines)
        current_memory = memory.read_long_term()

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

**IMPORTANT**: Both values MUST be strings, not objects or arrays.

Example:
{{
  "history_entry": "[2026-02-14 22:50] User asked about...",
  "memory_update": "- Host: HARRYBOOK-T14P\n- Name: Nado"
}}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory consolidation agent. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            text = (response.content or "").strip()
            if not text:
                logger.warning("Memory consolidation: LLM returned empty response, skipping")
                return
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text)
            if not isinstance(result, dict):
                logger.warning("Memory consolidation: unexpected response type, skipping. Response: {}", text[:200])
                return

            if entry := result.get("history_entry"):
                # Defensive: ensure entry is a string (LLM may return dict)
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                if entry.strip():
                    memory.append_history(entry)

            if update := result.get("memory_update"):
                # Defensive: ensure update is a string
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    memory.write_long_term(update)

            if archive_all:
                if supports_offset:
                    session.last_consolidated = 0
            else:
                if supports_offset:
                    session.last_consolidated = len(session.messages) - keep_count
                else:
                    session.messages = session.messages[-keep_count:] if keep_count else []
                self.sessions.save(session)
            logger.info(
                "Memory consolidation done: {} messages, last_consolidated={}",
                len(session.messages),
                getattr(session, "last_consolidated", "n/a"),
            )
        except Exception as e:
            logger.error("Memory consolidation failed: {}", e)
    
    async def process_direct(
        self,
        content: str,
        session_key: str | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).
        
        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
            on_progress: Optional callback for intermediate output.
        
        Returns:
            The agent's response.
        """
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
        )

        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return self._redact_text(response.content if response else "")

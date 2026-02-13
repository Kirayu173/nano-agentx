"""Agent loop: the core processing engine."""

import asyncio
import json
from pathlib import Path
from typing import Any

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
from nanobot.session.manager import SessionManager
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
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
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
            brave_api_key=self.brave_api_key,
            web_search_config=self.web_search_config,
            web_browser_config=self.web_browser_config,
            exec_config=self.exec_config,
            codex_config=self.codex_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        
        self._running = False
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
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
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
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self._publish_outbound_safe(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage, session_key: str | None = None) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
            session_key: Optional explicit session key override.
        
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
            await self._consolidate_memory(session, archive_all=True)
            session.clear()
            self.sessions.save(session)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="New session started. Memory consolidated.",
            )
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="nanobot commands:\n/new - Start a new conversation\n/help - Show available commands",
            )

        # Consolidate memory before processing if session is too large.
        if len(session.messages) > self.memory_window:
            await self._consolidate_memory(session)

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
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)
        
        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=effective_media if effective_media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            # Handle tool calls
            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                
                # Execute tools
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    safe_args = self._redact_text(args_str)
                    logger.info(f"Tool call: {tool_call.name}({safe_args[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            else:
                # No tool calls, we're done
                final_content = response.content
                break
        
        if final_content is None:
            if iteration >= self.max_iterations:
                final_content = f"Reached {self.max_iterations} iterations without completion."
            else:
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
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        
        while iteration < self.max_iterations:
            iteration += 1
            
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    safe_args = self._redact_text(args_str)
                    logger.info(f"Tool call: {tool_call.name}({safe_args[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            else:
                final_content = response.content
                break
        
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
        """Consolidate old messages into MEMORY.md + HISTORY.md, then trim session."""
        if not session.messages:
            return
        memory = MemoryStore(self.workspace)
        if archive_all:
            old_messages = session.messages
            keep_count = 0
        else:
            keep_count = min(10, max(2, self.memory_window // 2))
            old_messages = session.messages[:-keep_count]
        if not old_messages:
            return
        logger.info(
            "Memory consolidation started: "
            f"{len(session.messages)} messages, archiving {len(old_messages)}, keeping {keep_count}"
        )

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
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError("consolidation response is not a JSON object")

            history_entry = result.get("history_entry")
            if isinstance(history_entry, str) and history_entry.strip():
                memory.append_history(history_entry)

            memory_update = result.get("memory_update")
            if isinstance(memory_update, str) and memory_update != current_memory:
                memory.write_long_term(memory_update)

            session.messages = session.messages[-keep_count:] if keep_count else []
            self.sessions.save(session)
            logger.info(f"Memory consolidation done, session trimmed to {len(session.messages)} messages")
        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")
    
    async def process_direct(
        self,
        content: str,
        session_key: str | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).
        
        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
        
        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
        )
        
        response = await self._process_message(msg, session_key=session_key)
        return self._redact_text(response.content if response else "")

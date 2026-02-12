"""Agent loop: the core processing engine."""

import asyncio
import json
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.browser import BrowserRunTool
from nanobot.agent.tools.codex import CodexMergeTool, CodexRunTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.todo import TodoTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
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
        self.web_search_config = web_search_config or WebSearchConfig()
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
        
        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
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
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir, workspace=self.workspace))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir, workspace=self.workspace))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir, workspace=self.workspace))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir, workspace=self.workspace))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        if self.codex_config.enabled:
            self.tools.register(CodexRunTool(
                workspace=self.workspace,
                codex_config=self.codex_config,
                restrict_to_workspace=self.restrict_to_workspace,
            ))
            self.tools.register(CodexMergeTool(
                workspace=self.workspace,
                codex_config=self.codex_config,
                restrict_to_workspace=self.restrict_to_workspace,
            ))
        
        # Web tools
        self.tools.register(WebSearchTool(web_search_config=self.web_search_config))
        self.tools.register(WebFetchTool())
        if self.web_browser_config.enabled:
            self.tools.register(
                BrowserRunTool(
                    workspace=self.workspace,
                    web_browser_config=self.web_browser_config,
                )
            )
        
        # TODO management tool
        self.tools.register(TodoTool(workspace=self.workspace))
        
        # Message tool
        message_tool = MessageTool(send_callback=self._publish_outbound_safe)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _redact_text(self, content: str | None) -> str:
        """Apply output redaction policy to text."""
        return self.redactor.redact(content or "")

    def _normalize_media_paths(self, media: list[str] | None) -> list[str]:
        """
        Normalize outbound media paths to absolute paths.

        Preference order for relative paths:
        1) Current process working directory
        2) Agent workspace path (with special handling for "workspace/..." prefix)
        """
        if not media:
            return []

        normalized: list[str] = []
        for raw_path in media:
            if not isinstance(raw_path, str):
                continue

            text = raw_path.strip()
            if not text:
                continue

            try:
                p = Path(text).expanduser()
                candidates: list[Path] = []

                if p.is_absolute():
                    candidates.append(p)
                else:
                    candidates.append(Path.cwd() / p)

                    normalized_text = text.replace("\\", "/")
                    if normalized_text.startswith("workspace/"):
                        rel = normalized_text[len("workspace/"):]
                        if rel:
                            candidates.append(self.workspace / rel)

                    candidates.append(self.workspace / p)

                chosen = next(
                    (candidate.resolve(strict=False) for candidate in candidates
                     if candidate.exists() and candidate.is_file()),
                    None,
                )

                if chosen is None:
                    if not p.is_absolute():
                        normalized_text = text.replace("\\", "/")
                        if normalized_text.startswith("workspace/"):
                            rel = normalized_text[len("workspace/"):]
                            if rel:
                                chosen = (self.workspace / rel).resolve(strict=False)
                    if chosen is None:
                        chosen = (p if p.is_absolute() else (self.workspace / p)).resolve(strict=False)

                normalized.append(str(chosen))
            except Exception:
                # Keep original value if normalization fails unexpectedly.
                normalized.append(text)

        return normalized

    @staticmethod
    def _ensure_session_metadata(session: Any) -> dict[str, Any]:
        """Return mutable metadata dict for a session, creating it if needed."""
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            return metadata
        metadata = {}
        try:
            session.metadata = metadata
        except Exception:
            # Fallback for custom session objects without writable metadata.
            pass
        return metadata

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """Check whether path points to a readable image file."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return False
        mime, _ = mimetypes.guess_type(str(p))
        return bool(mime and mime.startswith("image/"))

    def _extract_latest_image(self, media: list[str] | None) -> str | None:
        """Pick the latest image path from a media list."""
        if not media:
            return None
        for candidate in reversed(media):
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text:
                continue
            if self._is_image_file(text):
                return str(Path(text).resolve(strict=False))
        return None

    def _remember_recent_image(self, session: Any, image_path: str) -> None:
        """Store latest image for short follow-up reuse."""
        metadata = self._ensure_session_metadata(session)
        metadata[self._RECENT_IMAGE_META_KEY] = {
            "path": image_path,
            "turns_left": self._RECENT_IMAGE_FOLLOWUP_TURNS,
        }

    def _consume_recent_image(self, session: Any) -> str | None:
        """Reuse recent image for one turn and decrement remaining turns."""
        metadata = self._ensure_session_metadata(session)
        raw = metadata.get(self._RECENT_IMAGE_META_KEY)
        if not isinstance(raw, dict):
            metadata.pop(self._RECENT_IMAGE_META_KEY, None)
            return None

        path = raw.get("path")
        turns_left = raw.get("turns_left")
        if not isinstance(path, str) or not isinstance(turns_left, int) or turns_left <= 0:
            metadata.pop(self._RECENT_IMAGE_META_KEY, None)
            return None

        if not self._is_image_file(path):
            metadata.pop(self._RECENT_IMAGE_META_KEY, None)
            return None

        turns_left -= 1
        if turns_left <= 0:
            metadata.pop(self._RECENT_IMAGE_META_KEY, None)
        else:
            metadata[self._RECENT_IMAGE_META_KEY] = {"path": path, "turns_left": turns_left}

        return path

    def _redact_outbound(self, msg: OutboundMessage) -> OutboundMessage:
        """Return a copy of outbound message with redacted content."""
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=self._redact_text(msg.content),
            reply_to=msg.reply_to,
            media=self._normalize_media_paths(msg.media),
            metadata=msg.metadata,
        )

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
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
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
        session = self.sessions.get_or_create(msg.session_key)

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
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    safe_args = self._redact_text(args_str)
                    logger.info(f"Tool call: {tool_call.name}({safe_args[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # No tool calls, we're done
                final_content = response.content
                break
        
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        final_content = self._redact_text(final_content)
        
        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")
        
        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
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
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    safe_args = self._redact_text(args_str)
                    logger.info(f"Tool call: {tool_call.name}({safe_args[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                break
        
        if final_content is None:
            final_content = "Background task completed."
        final_content = self._redact_text(final_content)
        
        # Save to session (mark as system message in history)
        system_msg = self._redact_text(f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("user", system_msg)
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
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
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
        
        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            session_key_override=session_key or None,
        )
        
        response = await self._process_message(msg)
        return self._redact_text(response.content if response else "")

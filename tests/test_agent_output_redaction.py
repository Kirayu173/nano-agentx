from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import BrowserToolConfig
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class ScriptedProvider(LLMProvider):
    """Provider stub that returns scripted responses in order."""

    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        api_key: str = "sk-test-secret-123456",
        api_base: str = "http://127.0.0.1:8000/v1",
    ):
        super().__init__(api_key=api_key, api_base=api_base)
        self._responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(copy.deepcopy(messages))
        if not self._responses:
            return LLMResponse(content="no scripted response")
        return self._responses.pop(0)

    def get_default_model(self) -> str:
        return "test-model"


@dataclass
class InMemorySession:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        self.messages.append({"role": role, "content": content, **kwargs})

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        return [{"role": m["role"], "content": m["content"]} for m in recent]


class InMemorySessionManager:
    def __init__(self):
        self._sessions: dict[str, InMemorySession] = {}

    def get_or_create(self, key: str) -> InMemorySession:
        if key not in self._sessions:
            self._sessions[key] = InMemorySession(key=key)
        return self._sessions[key]

    def save(self, session: InMemorySession) -> None:
        self._sessions[session.key] = session


def _latest_user_has_image(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False
    user_content = messages[-1].get("content")
    if not isinstance(user_content, list):
        return False
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in user_content
    )


def _build_loop(
    workspace: Path,
    provider: LLMProvider,
    *,
    session_manager: InMemorySessionManager,
) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        web_browser_config=BrowserToolConfig(enabled=False),
        session_manager=session_manager,
    )


@pytest.mark.asyncio
async def test_process_direct_redacts_response_and_saved_assistant_content(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content=(
                    f"Your workspace is at: {workspace}\n"
                    "Chat ID: 123456\n"
                    "token: sk-live-very-sensitive-123456"
                )
            )
        ]
    )
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    reply = await loop.process_direct("hello", channel="cli", chat_id="123456")
    assert str(workspace) not in reply
    assert "123456" not in reply
    assert "sk-live-very-sensitive-123456" not in reply
    assert "[REDACTED_PATH]" in reply
    assert "[REDACTED_CHAT_ID]" in reply
    assert "[REDACTED_SECRET]" in reply

    session = sessions.get_or_create("cli:123456")
    assistant_msgs = [m["content"] for m in session.messages if m["role"] == "assistant"]
    assert assistant_msgs
    assert all(str(workspace) not in content for content in assistant_msgs)
    assert all("123456" not in content for content in assistant_msgs)
    assert all("sk-live-very-sensitive-123456" not in content for content in assistant_msgs)


@pytest.mark.asyncio
async def test_process_direct_honors_session_key_override(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider([LLMResponse(content="ok")])
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    await loop.process_direct(
        "run cron payload",
        session_key="cron:test-job",
        channel="feishu",
        chat_id="ou_test",
    )

    assert "cron:test-job" in sessions._sessions
    assert "feishu:ou_test" not in sessions._sessions


@pytest.mark.asyncio
async def test_message_tool_outbound_content_is_redacted(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="message",
                        arguments={
                            "content": (
                                f"Leak {workspace} Chat ID: 999 token: sk-tool-secret-999999 "
                                "via http://127.0.0.1:9000"
                            )
                        },
                    )
                ],
            ),
            LLMResponse(content=f"Final {workspace} Chat ID: 999 token: sk-tool-secret-999999"),
        ]
    )
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    inbound = InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="999",
        content="hello",
    )
    final_response = await loop._process_message(inbound)
    assert final_response is not None

    outbound_from_tool = await loop.bus.consume_outbound()
    assert str(workspace) not in outbound_from_tool.content
    assert "999" not in outbound_from_tool.content
    assert "sk-tool-secret-999999" not in outbound_from_tool.content
    assert "127.0.0.1:9000" not in outbound_from_tool.content
    assert "[REDACTED_PATH]" in outbound_from_tool.content
    assert "[REDACTED_CHAT_ID]" in outbound_from_tool.content
    assert "[REDACTED_SECRET]" in outbound_from_tool.content
    assert "[REDACTED_ENDPOINT]" in outbound_from_tool.content

    assert str(workspace) not in final_response.content
    assert "999" not in final_response.content
    assert "sk-tool-secret-999999" not in final_response.content


@pytest.mark.asyncio
async def test_message_tool_outbound_media_paths_are_normalized_to_workspace(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    screenshot_dir = workspace / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = screenshot_dir / "shot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="message",
                        arguments={
                            "content": "sending file",
                            "media": ["workspace/screenshots/shot.png"],
                        },
                    )
                ],
            ),
            LLMResponse(content="done"),
        ]
    )
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    inbound = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="ou_test",
        content="upload it",
    )
    await loop._process_message(inbound)

    outbound_from_tool = await loop.bus.consume_outbound()
    assert outbound_from_tool.media == [str(screenshot)]


@pytest.mark.asyncio
async def test_system_message_flow_saves_redacted_history(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [LLMResponse(content=f"Summary {workspace} Chat ID: abc123 token: sk-system-secret-123")]
    )
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    system_msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="telegram:abc123",
        content=f"Raw result {workspace} Chat ID: abc123 token: sk-system-secret-123",
    )
    response = await loop._process_message(system_msg)
    assert response is not None
    assert response.channel == "telegram"
    assert response.chat_id == "abc123"
    assert str(workspace) not in response.content
    assert "abc123" not in response.content
    assert "sk-system-secret-123" not in response.content

    session = sessions.get_or_create("telegram:abc123")
    assert session.messages
    for entry in session.messages:
        assert str(workspace) not in entry["content"]
        assert "abc123" not in entry["content"]
        assert "sk-system-secret-123" not in entry["content"]


@pytest.mark.asyncio
async def test_recent_image_is_reused_for_two_followups_then_expires(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    image = workspace / "vision.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    provider = ScriptedProvider(
        [
            LLMResponse(content="round1"),
            LLMResponse(content="round2"),
            LLMResponse(content="round3"),
            LLMResponse(content="round4"),
        ]
    )
    sessions = InMemorySessionManager()
    loop = _build_loop(workspace, provider, session_manager=sessions)

    first = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="ou_test",
        content="请看这张图",
        media=[str(image)],
    )
    second = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="ou_test",
        content="第一个追问",
    )
    third = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="ou_test",
        content="第二个追问",
    )
    fourth = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="ou_test",
        content="第三个追问",
    )

    await loop._process_message(first)
    await loop._process_message(second)
    await loop._process_message(third)
    await loop._process_message(fourth)

    assert len(provider.calls) == 4
    assert _latest_user_has_image(provider.calls[0]) is True
    assert _latest_user_has_image(provider.calls[1]) is True
    assert _latest_user_has_image(provider.calls[2]) is True
    assert _latest_user_has_image(provider.calls[3]) is False

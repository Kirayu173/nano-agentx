from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import BrowserToolConfig
from nanobot.providers.base import LLMProvider, LLMResponse


class ScriptedProvider(LLMProvider):
    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        api_key: str = "sk-test-secret-123456",
        api_base: str = "http://127.0.0.1:8000/v1",
    ):
        super().__init__(api_key=api_key, api_base=api_base)
        self._responses = responses

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
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


def _build_loop(
    workspace: Path,
    provider: LLMProvider,
    *,
    session_manager: InMemorySessionManager,
    memory_window: int,
) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        web_browser_config=BrowserToolConfig(enabled=False),
        session_manager=session_manager,
        memory_window=memory_window,
    )


def _seed_messages(session: InMemorySession, count: int) -> None:
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        session.add_message(role, f"msg-{i}", timestamp=f"2026-02-12T10:{i:02d}:00")


@pytest.mark.asyncio
async def test_memory_consolidation_writes_history_and_trims_session(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content=(
                    '{"history_entry":"[2026-02-12 10:00] merged discussion",'
                    '"memory_update":"# Long-term Memory\\n\\n- prefers concise updates"}'
                )
            ),
            LLMResponse(content="final reply"),
        ]
    )
    sessions = InMemorySessionManager()
    session = sessions.get_or_create("cli:direct")
    _seed_messages(session, 6)
    loop = _build_loop(workspace, provider, session_manager=sessions, memory_window=4)

    reply = await loop.process_direct("new input", session_key="cli:direct", channel="cli", chat_id="direct")

    assert "final reply" in reply
    history_file = workspace / "memory" / "HISTORY.md"
    memory_file = workspace / "memory" / "MEMORY.md"
    assert history_file.exists()
    assert memory_file.exists()
    assert "merged discussion" in history_file.read_text(encoding="utf-8")
    assert "prefers concise updates" in memory_file.read_text(encoding="utf-8")
    assert len(session.messages) <= 4


@pytest.mark.asyncio
async def test_memory_consolidation_failure_does_not_trim_session(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider([LLMResponse(content="not-json"), LLMResponse(content="final reply")])
    sessions = InMemorySessionManager()
    session = sessions.get_or_create("cli:direct")
    _seed_messages(session, 6)
    loop = _build_loop(workspace, provider, session_manager=sessions, memory_window=4)

    reply = await loop.process_direct("new input", session_key="cli:direct", channel="cli", chat_id="direct")

    assert "final reply" in reply
    assert len(session.messages) == 8
    assert not (workspace / "memory" / "HISTORY.md").exists()

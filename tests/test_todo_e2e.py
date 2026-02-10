from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.todo.storage import (
    TODO_AUTO_REVIEW_END_MARKER,
    TODO_AUTO_REVIEW_START_MARKER,
    TodoStorage,
    today_date,
)
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import BrowserToolConfig
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class ScriptedProvider(LLMProvider):
    """Provider stub returning scripted responses and recording message history."""

    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key="test-key", api_base="http://127.0.0.1:8000/v1")
        self._responses = list(responses)
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
            return LLMResponse(content="done")
        return self._responses.pop(0)

    def get_default_model(self) -> str:
        return "test/model"


@dataclass
class InMemorySession:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)

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


def _build_loop(workspace: Path, provider: LLMProvider) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        web_browser_config=BrowserToolConfig(enabled=False),
        session_manager=InMemorySessionManager(),
    )


def _collect_todo_results(provider: ScriptedProvider) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for call in provider.calls:
        for msg in call:
            if msg.get("role") != "tool" or msg.get("name") != "todo":
                continue
            tool_call_id = str(msg.get("tool_call_id", ""))
            if tool_call_id in seen:
                continue
            seen.add(tool_call_id)
            results.append(json.loads(msg.get("content", "{}")))
    return results


@pytest.mark.asyncio
async def test_agent_loop_todo_e2e_daily_review_idempotent(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="todo", arguments={"action": "init"})],
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c2", name="todo", arguments={"action": "review_daily"})],
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c3", name="todo", arguments={"action": "review_daily"})],
            ),
            LLMResponse(content="Daily review completed."),
        ]
    )
    loop = _build_loop(workspace, provider)

    reply = await loop.process_direct("Run daily review now.", channel="cli", chat_id="todo-e2e")
    assert "Daily review completed." in reply

    storage = TodoStorage(workspace)
    store = storage.load_store()
    assert store.meta.last_review_date == today_date()
    assert storage.heartbeat_path.exists()
    heartbeat = storage.heartbeat_path.read_text(encoding="utf-8")
    assert TODO_AUTO_REVIEW_START_MARKER in heartbeat
    assert TODO_AUTO_REVIEW_END_MARKER in heartbeat

    results = _collect_todo_results(provider)
    review_results = [r for r in results if r.get("action") == "review_daily"]
    assert len(review_results) == 2
    assert review_results[0]["ok"] is True
    assert review_results[1]["ok"] is True
    assert "already completed" in review_results[1]["summary"].lower()


@pytest.mark.asyncio
async def test_agent_loop_todo_e2e_dependency_guard(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="d1", name="todo", arguments={"action": "init"}),
                    ToolCallRequest(
                        id="d2",
                        name="todo",
                        arguments={"action": "add", "title": "Prepare release"},
                    ),
                    ToolCallRequest(
                        id="d3",
                        name="todo",
                        arguments={
                            "action": "add",
                            "title": "Publish changelog",
                            "depends_on": ["T0001"],
                        },
                    ),
                    ToolCallRequest(
                        id="d4",
                        name="todo",
                        arguments={"action": "remove", "id": "T0001"},
                    ),
                ],
            ),
            LLMResponse(content="Dependency checks finished."),
        ]
    )
    loop = _build_loop(workspace, provider)

    reply = await loop.process_direct("Try removing a depended task.", channel="cli", chat_id="todo-e2e")
    assert "Dependency checks finished." in reply

    results = _collect_todo_results(provider)
    remove_results = [r for r in results if r.get("action") == "remove"]
    assert len(remove_results) == 1
    assert remove_results[0]["ok"] is False
    assert "depended on" in remove_results[0]["summary"]

    store = TodoStorage(workspace).load_store()
    assert len(store.items) == 2
    assert {item.id for item in store.items} == {"T0001", "T0002"}


@pytest.mark.asyncio
async def test_agent_loop_todo_e2e_sorted_list_flow(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="l1", name="todo", arguments={"action": "init"}),
                    ToolCallRequest(
                        id="l2",
                        name="todo",
                        arguments={"action": "add", "title": "P3 task", "priority": 3},
                    ),
                    ToolCallRequest(
                        id="l3",
                        name="todo",
                        arguments={"action": "add", "title": "P1 task", "priority": 1},
                    ),
                    ToolCallRequest(
                        id="l4",
                        name="todo",
                        arguments={"action": "add", "title": "P2 task", "priority": 2},
                    ),
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="l5",
                        name="todo",
                        arguments={
                            "action": "list",
                            "sort_by": "priority",
                            "sort_order": "asc",
                            "limit": 2,
                            "filters": {"statuses": ["todo"]},
                        },
                    )
                ],
            ),
            LLMResponse(content="Sorted listing done."),
        ]
    )
    loop = _build_loop(workspace, provider)

    reply = await loop.process_direct("List top priorities.", channel="cli", chat_id="todo-e2e")
    assert "Sorted listing done." in reply

    results = _collect_todo_results(provider)
    list_results = [r for r in results if r.get("action") == "list"]
    assert len(list_results) == 1
    listed_items = list_results[0]["items"]
    assert len(listed_items) == 2
    assert [item["priority"] for item in listed_items] == [1, 2]

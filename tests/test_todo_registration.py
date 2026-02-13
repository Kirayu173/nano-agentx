import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    def __init__(self):
        super().__init__(api_key="test-key", api_base="http://127.0.0.1:8000/v1")
        self.last_tools: list[dict] = []

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        self.last_tools = list(tools or [])
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy/model"


def _tool_names(tool_defs: list[dict]) -> set[str]:
    names: set[str] = set()
    for item in tool_defs:
        if isinstance(item, dict):
            fn = item.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str):
                    names.add(name)
    return names


def test_agent_loop_registers_todo_tool(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
    )
    assert "todo" in loop.tools.tool_names


@pytest.mark.asyncio
async def test_subagent_registry_registers_todo_tool(tmp_path) -> None:
    provider = DummyProvider()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
    )
    await manager._run_subagent("t1", "noop", "noop", {"channel": "cli", "chat_id": "direct"})
    assert "todo" in _tool_names(provider.last_tools)

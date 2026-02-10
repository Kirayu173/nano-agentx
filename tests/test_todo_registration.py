import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy/model"


def test_agent_loop_registers_todo_tool(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
    )
    assert "todo" in loop.tools.tool_names


@pytest.mark.asyncio
async def test_subagent_registry_registers_todo_tool(tmp_path) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
    )
    tools = manager._build_tool_registry()
    assert "todo" in tools.tool_names

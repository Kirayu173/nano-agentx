from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import BrowserToolConfig, CodexToolConfig
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


def test_agent_loop_registers_codex_tool_when_enabled(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=True),
    )
    assert "codex_run" in loop.tools.tool_names


def test_agent_loop_does_not_register_codex_tool_when_disabled(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
    )
    assert "codex_run" not in loop.tools.tool_names


def test_subagent_registers_codex_tool_when_enabled(tmp_path) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=True),
    )
    tools = manager._build_tool_registry()
    assert "codex_run" in tools.tool_names


def test_subagent_does_not_register_codex_tool_when_disabled(tmp_path) -> None:
    manager = SubagentManager(
        provider=DummyProvider(),
        workspace=tmp_path,
        bus=MessageBus(),
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
    )
    tools = manager._build_tool_registry()
    assert "codex_run" not in tools.tool_names

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import BrowserToolConfig, CodexToolConfig
from nanobot.cron.service import CronService
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


def test_agent_loop_registers_codex_tool_when_enabled(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=True),
    )
    assert "codex_run" in loop.tools.tool_names
    assert "codex_merge" in loop.tools.tool_names


def test_agent_loop_does_not_register_codex_tool_when_disabled(tmp_path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
    )
    assert "codex_run" not in loop.tools.tool_names
    assert "codex_merge" not in loop.tools.tool_names


@pytest.mark.asyncio
async def test_subagent_registers_codex_tool_when_enabled(tmp_path) -> None:
    provider = DummyProvider()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=True),
    )
    await manager._run_subagent("t1", "noop", "noop", {"channel": "cli", "chat_id": "direct"})
    names = _tool_names(provider.last_tools)
    assert "codex_run" in names
    assert "codex_merge" in names


@pytest.mark.asyncio
async def test_subagent_does_not_register_codex_tool_when_disabled(tmp_path) -> None:
    provider = DummyProvider()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
    )
    await manager._run_subagent("t2", "noop", "noop", {"channel": "cli", "chat_id": "direct"})
    names = _tool_names(provider.last_tools)
    assert "codex_run" not in names
    assert "codex_merge" not in names


@pytest.mark.asyncio
async def test_main_and_subagent_control_tools_are_isolated(tmp_path) -> None:
    cron_service = CronService(tmp_path / "cron" / "jobs.json")
    loop = AgentLoop(
        bus=MessageBus(),
        provider=DummyProvider(),
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
        cron_service=cron_service,
    )
    assert {"message", "spawn", "cron"}.issubset(loop.tools.tool_names)

    provider = DummyProvider()
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        web_browser_config=BrowserToolConfig(enabled=False),
        codex_config=CodexToolConfig(enabled=False),
    )
    await manager._run_subagent("t3", "noop", "noop", {"channel": "cli", "chat_id": "direct"})
    names = _tool_names(provider.last_tools)
    assert "message" not in names
    assert "spawn" not in names
    assert "cron" not in names

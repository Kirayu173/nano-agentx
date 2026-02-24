import asyncio
from unittest.mock import AsyncMock

import pytest

from nanobot.heartbeat.service import HeartbeatService
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_trigger_now_runs_when_decision_is_run(tmp_path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("- [ ] check inbox", encoding="utf-8")

    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="heartbeat",
                    arguments={"action": "run", "tasks": "Check inbox and summarize"},
                )
            ],
        )
    )
    on_execute = AsyncMock(return_value="done")

    service = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="test-model",
        on_execute=on_execute,
        interval_s=9999,
        enabled=True,
    )

    result = await service.trigger_now()

    assert result == "done"
    on_execute.assert_awaited_once_with("Check inbox and summarize")


@pytest.mark.asyncio
async def test_trigger_now_returns_none_when_decision_is_skip(tmp_path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("- [ ] check inbox", encoding="utf-8")

    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="heartbeat",
                    arguments={"action": "skip"},
                )
            ],
        )
    )
    on_execute = AsyncMock(return_value="done")

    service = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="test-model",
        on_execute=on_execute,
        interval_s=9999,
        enabled=True,
    )

    result = await service.trigger_now()

    assert result is None
    on_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path) -> None:
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=LLMResponse(content=None, tool_calls=[]))

    service = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="test-model",
        interval_s=9999,
        enabled=True,
    )

    await service.start()
    first_task = service._task
    await service.start()

    assert service._task is first_task

    service.stop()
    await asyncio.sleep(0)

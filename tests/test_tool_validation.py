from types import SimpleNamespace
from typing import Any
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import WriteFileTool, _resolve_path
from nanobot.agent.tools.registry import ToolRegistry


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


class FakeCronService:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []

    def add_job(self, **kwargs: Any) -> SimpleNamespace:
        self.add_calls.append(kwargs)
        return SimpleNamespace(name=kwargs["name"], id="job-123")

    def list_jobs(self, include_disabled: bool = False) -> list[Any]:
        return []

    def remove_job(self, job_id: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_cron_tool_add_defaults_to_reminder_mode() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("feishu", "ou_test")

    result = await tool.execute(action="add", message="Drink water", every_seconds=60)

    assert "mode: reminder" in result
    assert len(service.add_calls) == 1
    call = service.add_calls[0]
    assert call["payload_kind"] == "system_event"
    assert call["deliver"] is True
    assert call["channel"] == "feishu"
    assert call["to"] == "ou_test"
    assert call["schedule"].kind == "every"
    assert call["schedule"].every_ms == 60000


@pytest.mark.asyncio
async def test_cron_tool_add_task_mode_uses_agent_turn_payload() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("telegram", "chat_1")

    result = await tool.execute(
        action="add",
        message="Check repo status and report",
        mode="task",
        cron_expr="0 9 * * *",
    )

    assert "mode: task" in result
    call = service.add_calls[0]
    assert call["payload_kind"] == "agent_turn"
    assert call["schedule"].kind == "cron"
    assert call["schedule"].expr == "0 9 * * *"


@pytest.mark.asyncio
async def test_cron_tool_add_in_seconds_creates_one_time_job() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("feishu", "ou_test")

    before_ms = int(time.time() * 1000)
    result = await tool.execute(
        action="add",
        message="Drink water",
        mode="reminder",
        in_seconds=120,
    )
    after_ms = int(time.time() * 1000)

    assert "one-time job" in result
    call = service.add_calls[0]
    assert call["schedule"].kind == "at"
    assert call["delete_after_run"] is True
    expected_min = before_ms + 120000
    expected_max = after_ms + 120000 + 2000
    assert expected_min <= (call["schedule"].at_ms or 0) <= expected_max


@pytest.mark.asyncio
async def test_cron_tool_add_at_creates_one_time_job() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("telegram", "chat_1")

    dt = (datetime.now().astimezone() + timedelta(minutes=3)).replace(microsecond=0)
    result = await tool.execute(
        action="add",
        message="Stand up",
        mode="reminder",
        at=dt.isoformat(),
    )

    assert "one-time job" in result
    call = service.add_calls[0]
    assert call["schedule"].kind == "at"
    assert call["delete_after_run"] is True
    assert abs((call["schedule"].at_ms or 0) - int(dt.timestamp() * 1000)) < 2000


@pytest.mark.asyncio
async def test_cron_tool_rejects_invalid_mode_and_non_positive_interval() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("feishu", "ou_test")

    invalid_mode = await tool.execute(
        action="add",
        message="hello",
        mode="unknown",
        every_seconds=10,
    )
    invalid_interval = await tool.execute(
        action="add",
        message="hello",
        mode="reminder",
        every_seconds=0,
    )

    assert invalid_mode == "Error: mode must be 'reminder' or 'task'"
    assert invalid_interval == "Error: every_seconds must be > 0"
    assert service.add_calls == []


@pytest.mark.asyncio
async def test_cron_tool_rejects_conflicting_schedule_inputs() -> None:
    service = FakeCronService()
    tool = CronTool(service)  # type: ignore[arg-type]
    tool.set_context("feishu", "ou_test")

    conflict = await tool.execute(
        action="add",
        message="hello",
        mode="reminder",
        every_seconds=60,
        in_seconds=120,
    )

    assert conflict == "Error: specify exactly one of every_seconds, cron_expr, in_seconds, or at"
    assert service.add_calls == []


def test_filesystem_resolve_relative_path_uses_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resolved = _resolve_path("memory/MEMORY.md", workspace=workspace)

    assert resolved == (workspace / "memory" / "MEMORY.md").resolve(strict=False)


def test_filesystem_resolve_absolute_path_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absolute = (tmp_path / "absolute.txt").resolve(strict=False)

    resolved = _resolve_path(str(absolute), workspace=workspace)

    assert resolved == absolute


def test_filesystem_resolve_blocks_outside_allowed_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(PermissionError):
        _resolve_path("../outside.txt", allowed_dir=workspace, workspace=workspace)


@pytest.mark.asyncio
async def test_write_file_tool_relative_path_writes_under_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = WriteFileTool(workspace=workspace)

    result = await tool.execute(path="memory/MEMORY.md", content="hello")

    assert "Successfully wrote" in result
    assert (workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "hello"

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from nanobot.agent.tools.codex.merge_tool import CodexMergeTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.cli.commands import _migrate_codex_merge_cron, execute_cron_job
from nanobot.config.schema import CodexToolConfig
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronPayload, CronSchedule

pytestmark = pytest.mark.asyncio


class FakeTools:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, params: dict):
        self.calls.append((name, params))
        return "tool-result"


class FakeAgent:
    def __init__(self):
        self.tools = FakeTools()
        self.process_direct_calls: list[str] = []

    async def process_direct(self, content: str, **kwargs):
        self.process_direct_calls.append(content)
        return "agent-result"


@dataclass
class FakeOutbound:
    channel: str
    chat_id: str
    content: str


class FakeBus:
    def __init__(self):
        self.messages: list[FakeOutbound] = []

    async def publish_outbound(self, message):
        self.messages.append(
            FakeOutbound(channel=message.channel, chat_id=message.chat_id, content=message.content)
        )


def _job(payload: CronPayload) -> CronJob:
    return CronJob(
        id="job1",
        name="test",
        schedule=CronSchedule(kind="every", every_ms=60000),
        payload=payload,
    )


async def test_tool_call_payload_dispatches_directly_to_tool_registry() -> None:
    agent = FakeAgent()
    bus = FakeBus()
    job = _job(
        CronPayload(
            kind="tool_call",
            tool_name="codex_merge",
            tool_args={"action": "list"},
            deliver=False,
            channel="telegram",
            to="u1",
        )
    )

    result = await execute_cron_job(job, agent, bus)

    assert result == "tool-result"
    assert agent.tools.calls == [("codex_merge", {"action": "list"})]
    assert agent.process_direct_calls == []


async def test_tool_call_payload_delivers_tool_result_when_enabled() -> None:
    agent = FakeAgent()
    bus = FakeBus()
    job = _job(
        CronPayload(
            kind="tool_call",
            tool_name="codex_merge",
            tool_args={"action": "status", "plan_id": "abc"},
            deliver=True,
            channel="telegram",
            to="u2",
        )
    )

    await execute_cron_job(job, agent, bus)

    assert len(bus.messages) == 1
    assert bus.messages[0].channel == "telegram"
    assert bus.messages[0].chat_id == "u2"
    assert bus.messages[0].content == "tool-result"


async def test_tool_call_payload_without_tool_name_returns_error() -> None:
    agent = FakeAgent()
    bus = FakeBus()
    job = _job(CronPayload(kind="tool_call", deliver=False))

    result = await execute_cron_job(job, agent, bus)

    assert result == "Error: tool_name is required for tool_call payload"
    assert agent.tools.calls == []
    assert agent.process_direct_calls == []


async def test_migrate_codex_merge_cron_replaces_legacy_job_and_report_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report").mkdir(parents=True, exist_ok=True)

    cron_store = tmp_path / "cron" / "jobs.json"
    service = CronService(cron_store)
    legacy = service.add_job(
        name="legacy job",
        schedule=CronSchedule(kind="cron", expr="0 23 * * *"),
        message="old",
        payload_kind="agent_turn",
        deliver=True,
        channel="telegram",
        to="u100",
    )

    store = service._load_store()
    for job in store.jobs:
        if job.id == legacy.id:
            job.id = "8dbfbddb"
    service._save_store()

    _migrate_codex_merge_cron(service, workspace)
    jobs = service.list_jobs(include_disabled=True)

    assert not any(job.id == "8dbfbddb" for job in jobs)
    nightly = next(
        (
            job
            for job in jobs
            if job.payload.kind == "tool_call"
            and job.payload.tool_name == "codex_merge"
            and isinstance(job.payload.tool_args, dict)
            and job.payload.tool_args.get("action") == "plan_latest"
        ),
        None,
    )
    assert nightly is not None
    assert nightly.schedule.kind == "cron"
    assert nightly.schedule.expr == "0 23 * * *"
    assert nightly.payload.channel == "telegram"
    assert nightly.payload.to == "u100"
    assert not (workspace / "report").exists()
    assert (workspace / "reports").exists()


class _StubCodexClient:
    def __init__(self, responses: list[dict[str, object]]):
        self.responses = list(responses)

    async def run(self, **kwargs):
        return self.responses.pop(0)


class _RegistryAgent:
    def __init__(self, registry: ToolRegistry):
        self.tools = registry

    async def process_direct(self, content: str, **kwargs):
        return "unused"


async def test_end_to_end_orchestration_with_cron_plan_then_revise_and_execute(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "upstream-main-conflict-report-20260212.md").write_text(
        "conflicts in app.py",
        encoding="utf-8",
    )

    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
        client=_StubCodexClient(
            [
                {"ok": True, "message": "Plan V1", "thread_id": "p1", "usage": {}},
                {"ok": True, "message": "Plan V2", "thread_id": "p2", "usage": {}},
                {"ok": True, "message": "Merged and pushed", "thread_id": "p3", "usage": {}},
            ]
        ),  # type: ignore[arg-type]
        repo_root=workspace,
    )
    registry = ToolRegistry()
    registry.register(tool)

    agent = _RegistryAgent(registry)
    bus = FakeBus()
    cron_job = _job(
        CronPayload(
            kind="tool_call",
            tool_name="codex_merge",
            tool_args={"action": "plan_latest"},
            deliver=True,
            channel="telegram",
            to="owner-1",
        )
    )

    planned = json.loads((await execute_cron_job(cron_job, agent, bus)) or "{}")
    revised = json.loads(
        await tool.execute(
            action="revise_plan",
            plan_id=str(planned["plan_id"]),
            feedback="Please minimize risk",
        )
    )
    executed = json.loads(
        await tool.execute(
            action="execute_merge",
            plan_id=str(planned["plan_id"]),
            confirmation_token=str(revised["confirmation_token"]),
        )
    )

    assert planned["ok"] is True
    assert revised["ok"] is True
    assert executed["ok"] is True
    assert executed["status"] == "executed"
    assert len(bus.messages) == 1
    assert bus.messages[0].chat_id == "owner-1"

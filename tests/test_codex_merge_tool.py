from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent.tools.codex.merge_tool import CodexMergeTool
from nanobot.agent.tools.codex.store import MergePlanStore
from nanobot.config.schema import CodexToolConfig

pytestmark = pytest.mark.asyncio


class FakeCodexClient:
    def __init__(self, responses: list[dict[str, object]]):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            return {"ok": True, "message": "default response", "usage": {}}
        return self.responses.pop(0)


def _create_report(workspace: Path, suffix: str = "20260212") -> Path:
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"upstream-main-conflict-report-{suffix}.md"
    path.write_text("# Report\n\nPotential conflicts in foo.py and bar.py", encoding="utf-8")
    return path


def _load_json(result: str) -> dict[str, object]:
    return json.loads(result)


async def test_plan_latest_generates_plan_and_persists_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report_path = _create_report(workspace)

    client = FakeCodexClient([
        {
            "ok": True,
            "message": "Use strategy A with staged validation.",
            "thread_id": "thread_1",
            "usage": {"input_tokens": 12, "output_tokens": 34},
        }
    ])
    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True),
        client=client,  # type: ignore[arg-type]
        repo_root=workspace,
    )

    payload = _load_json(await tool.execute(action="plan_latest"))

    assert payload["ok"] is True
    assert payload["action"] == "plan_latest"
    assert payload["status"] == "planned"
    assert payload["report_path"] == str(report_path)
    plan_id = str(payload["plan_id"])

    store = MergePlanStore(workspace)
    record = store.load(plan_id)
    assert record is not None
    assert record.status == "planned"
    assert record.recommendation.startswith("Use strategy A")


async def test_plan_latest_returns_readable_error_when_report_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True),
        client=FakeCodexClient([]),  # type: ignore[arg-type]
        repo_root=workspace,
    )

    payload = _load_json(await tool.execute(action="plan_latest"))

    assert payload["ok"] is False
    assert payload["error"]["code"] == "report_not_found"


async def test_revise_plan_updates_recommendation_and_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_report(workspace)

    client = FakeCodexClient(
        [
            {
                "ok": True,
                "message": "Initial plan",
                "thread_id": "thread_1",
                "usage": {},
            },
            {
                "ok": True,
                "message": "Revised plan with safer conflict resolution",
                "thread_id": "thread_2",
                "usage": {},
            },
        ]
    )
    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True),
        client=client,  # type: ignore[arg-type]
        repo_root=workspace,
    )

    planned = _load_json(await tool.execute(action="plan_latest"))
    revised = _load_json(
        await tool.execute(
            action="revise_plan",
            plan_id=str(planned["plan_id"]),
            feedback="Please reduce risk and keep commits minimal",
        )
    )

    assert revised["ok"] is True
    assert revised["status"] == "revised"
    assert revised["revision"] == 1
    assert revised["confirmation_token"] != planned["confirmation_token"]


async def test_execute_merge_rejects_missing_or_invalid_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_report(workspace)

    client = FakeCodexClient([
        {
            "ok": True,
            "message": "Initial plan",
            "thread_id": "thread_1",
            "usage": {},
        }
    ])
    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
        client=client,  # type: ignore[arg-type]
        repo_root=workspace,
    )

    planned = _load_json(await tool.execute(action="plan_latest"))
    missing = _load_json(
        await tool.execute(action="execute_merge", plan_id=str(planned["plan_id"]))
    )
    invalid = _load_json(
        await tool.execute(
            action="execute_merge",
            plan_id=str(planned["plan_id"]),
            confirmation_token="wrong",
        )
    )

    assert missing["ok"] is False
    assert missing["error"]["code"] == "missing_confirmation_token"
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_confirmation_token"
    assert len(client.calls) == 1


async def test_execute_merge_updates_status_for_success_and_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_report(workspace)

    # success path
    success_client = FakeCodexClient(
        [
            {"ok": True, "message": "Initial plan", "thread_id": "t1", "usage": {}},
            {"ok": True, "message": "Merged and pushed", "thread_id": "t2", "usage": {}},
        ]
    )
    success_tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
        client=success_client,  # type: ignore[arg-type]
        repo_root=workspace,
    )

    planned = _load_json(await success_tool.execute(action="plan_latest"))
    executed = _load_json(
        await success_tool.execute(
            action="execute_merge",
            plan_id=str(planned["plan_id"]),
            confirmation_token=str(planned["confirmation_token"]),
        )
    )
    assert executed["ok"] is True
    assert executed["status"] == "executed"

    status_payload = _load_json(
        await success_tool.execute(action="status", plan_id=str(planned["plan_id"]))
    )
    assert status_payload["plan"]["status"] == "executed"

    # failure path in isolated workspace
    fail_workspace = tmp_path / "workspace-fail"
    fail_workspace.mkdir()
    _create_report(fail_workspace)
    fail_client = FakeCodexClient(
        [
            {"ok": True, "message": "Initial plan", "thread_id": "t3", "usage": {}},
            {
                "ok": False,
                "error": {"code": "codex_failed", "message": "merge conflict unresolved"},
                "thread_id": "t4",
                "usage": {},
            },
        ]
    )
    fail_tool = CodexMergeTool(
        workspace=fail_workspace,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
        client=fail_client,  # type: ignore[arg-type]
        repo_root=fail_workspace,
    )

    fail_plan = _load_json(await fail_tool.execute(action="plan_latest"))
    failed = _load_json(
        await fail_tool.execute(
            action="execute_merge",
            plan_id=str(fail_plan["plan_id"]),
            confirmation_token=str(fail_plan["confirmation_token"]),
        )
    )
    assert failed["ok"] is False
    assert failed["status"] == "failed"

    failed_status = _load_json(
        await fail_tool.execute(action="status", plan_id=str(fail_plan["plan_id"]))
    )
    assert failed_status["plan"]["status"] == "failed"


async def test_codex_merge_orchestration_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_report(workspace)

    client = FakeCodexClient(
        [
            {"ok": True, "message": "Plan V1", "thread_id": "p1", "usage": {}},
            {"ok": True, "message": "Plan V2 after feedback", "thread_id": "p2", "usage": {}},
            {"ok": True, "message": "Execution complete and pushed", "thread_id": "p3", "usage": {}},
        ]
    )
    tool = CodexMergeTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
        client=client,  # type: ignore[arg-type]
        repo_root=workspace,
    )

    plan_payload = _load_json(await tool.execute(action="plan_latest"))
    revise_payload = _load_json(
        await tool.execute(
            action="revise_plan",
            plan_id=str(plan_payload["plan_id"]),
            feedback="Prefer smaller blast radius",
        )
    )
    execute_payload = _load_json(
        await tool.execute(
            action="execute_merge",
            plan_id=str(plan_payload["plan_id"]),
            confirmation_token=str(revise_payload["confirmation_token"]),
        )
    )

    assert plan_payload["ok"] is True
    assert revise_payload["ok"] is True
    assert execute_payload["ok"] is True
    assert execute_payload["status"] == "executed"

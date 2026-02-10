import json

import pytest

from nanobot.agent.tools.todo.tool import TodoTool


def _payload(raw: str) -> dict:
    return json.loads(raw)


@pytest.mark.asyncio
async def test_todo_tool_add_list_and_stats(tmp_path) -> None:
    tool = TodoTool(workspace=tmp_path)

    init_res = _payload(await tool.execute(action="init"))
    assert init_res["ok"] is True

    a = _payload(
        await tool.execute(
            action="add",
            title="Fix login bug",
            priority=1,
            tags=["bug", "urgent"],
            due="2026-02-12",
        )
    )
    assert a["ok"] is True
    assert a["items"][0]["id"] == "T0001"

    b = _payload(
        await tool.execute(
            action="add",
            title="Write release notes",
            priority=2,
            depends_on=["T0001"],
        )
    )
    assert b["ok"] is True
    assert b["items"][0]["depends_on"] == ["T0001"]

    listed = _payload(await tool.execute(action="list", sort_by="priority", sort_order="asc"))
    assert listed["ok"] is True
    assert len(listed["items"]) == 2
    assert listed["items"][0]["id"] == "T0001"

    stats = _payload(await tool.execute(action="stats"))
    assert stats["ok"] is True
    assert stats["stats"]["total"] == 2
    assert stats["stats"]["by_status"]["todo"] == 2


@pytest.mark.asyncio
async def test_todo_tool_rejects_cycle_and_dependency_break(tmp_path) -> None:
    tool = TodoTool(workspace=tmp_path)
    await tool.execute(action="init")
    await tool.execute(action="add", title="A")
    await tool.execute(action="add", title="B", depends_on=["T0001"])

    cycle = _payload(
        await tool.execute(
            action="update",
            id="T0001",
            patch={"depends_on": ["T0002"]},
        )
    )
    assert cycle["ok"] is False
    assert "cycle" in cycle["summary"].lower()

    blocked_remove = _payload(await tool.execute(action="remove", id="T0001"))
    assert blocked_remove["ok"] is False
    assert "depended on" in blocked_remove["summary"]


@pytest.mark.asyncio
async def test_todo_tool_bulk_archive_and_review_idempotent(tmp_path) -> None:
    tool = TodoTool(workspace=tmp_path)
    await tool.execute(action="init")
    await tool.execute(action="add", title="Task A")
    await tool.execute(action="add", title="Task B")

    moved = _payload(
        await tool.execute(
            action="bulk_update",
            ids=["T0001", "T0002"],
            patch={"status": "done", "tags": ["sprint-1"]},
        )
    )
    assert moved["ok"] is True
    assert len(moved["items"]) == 2
    assert all(item["status"] == "done" for item in moved["items"])

    archived = _payload(await tool.execute(action="archive", ids=["T0001", "T0002"]))
    assert archived["ok"] is True
    assert all(item["status"] == "archived" for item in archived["items"])

    review_1 = _payload(await tool.execute(action="review_daily"))
    assert review_1["ok"] is True

    review_2 = _payload(await tool.execute(action="review_daily"))
    assert review_2["ok"] is True
    assert "already completed" in review_2["summary"].lower()

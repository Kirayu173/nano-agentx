"""TODO management tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.todo.service import TodoService


class TodoTool(Tool):
    """Manage TODO tasks in a markdown-backed store."""

    name = "todo"
    description = (
        "Manage TODO tasks in memory/todo.md. "
        "Supports init, CRUD, bulk actions, filtering, archive, stats, and daily review."
    )

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "init",
                    "add",
                    "list",
                    "update",
                    "bulk_update",
                    "move",
                    "done",
                    "remove",
                    "bulk_remove",
                    "archive",
                    "reorder",
                    "stats",
                    "review_daily",
                ],
                "description": "TODO action to perform",
            },
            "id": {"type": "string", "description": "Single task id, e.g. T0001"},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "Task ids"},
            "title": {"type": "string", "description": "Task title"},
            "note": {"type": "string", "description": "Task note"},
            "status": {
                "type": "string",
                "enum": ["todo", "doing", "blocked", "done", "archived"],
                "description": "Task status",
            },
            "priority": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "description": "Task priority, 1 is highest",
            },
            "due": {"type": "string", "description": "ISO date or datetime"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Task tags"},
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dependency ids",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "statuses": {"type": "array", "items": {"type": "string"}},
                    "tags_any": {"type": "array", "items": {"type": "string"}},
                    "tags_all": {"type": "array", "items": {"type": "string"}},
                    "keyword": {"type": "string"},
                    "priority_min": {"type": "integer", "minimum": 1, "maximum": 4},
                    "priority_max": {"type": "integer", "minimum": 1, "maximum": 4},
                    "due_before": {"type": "string"},
                    "due_after": {"type": "string"},
                    "overdue": {"type": "boolean"},
                    "include_archived": {"type": "boolean"},
                },
                "description": "Filter options for list/archive",
            },
            "patch": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "status": {"type": "string", "enum": ["todo", "doing", "blocked", "done", "archived"]},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 4},
                    "due": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "description": "Update fields for update/bulk_update",
            },
            "sort_by": {
                "type": "string",
                "enum": ["priority", "due", "created", "updated"],
                "description": "Sort strategy for list/reorder",
            },
            "sort_order": {"type": "string", "enum": ["asc", "desc"], "description": "Sort order"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Max results"},
        },
        "required": ["action"],
    }

    def __init__(self, workspace: Path):
        self._service = TodoService(workspace=workspace)

    async def execute(self, action: str, **kwargs: Any) -> str:
        result = self._service.handle(action=action, **kwargs)
        return json.dumps(result, ensure_ascii=False)

"""Business logic for TODO tool actions."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from nanobot.agent.tools.todo.models import (
    OPEN_STATUSES,
    VALID_STATUSES,
    TodoItem,
    TodoStore,
)
from nanobot.agent.tools.todo.storage import TodoStorage, now_iso, today_date

_ID_PATTERN = re.compile(r"^T\d{4,}$")


class TodoService:
    """Stateful TODO operations backed by markdown storage."""

    def __init__(self, workspace: Path):
        self.storage = TodoStorage(workspace)

    def handle(self, action: str, **kwargs: Any) -> dict[str, Any]:
        """Dispatch action and return a structured payload."""
        action_name = (action or "").strip().lower()
        handlers: dict[str, Any] = {
            "init": self._action_init,
            "add": self._action_add,
            "list": self._action_list,
            "update": self._action_update,
            "bulk_update": self._action_bulk_update,
            "move": self._action_move,
            "done": self._action_done,
            "remove": self._action_remove,
            "bulk_remove": self._action_bulk_remove,
            "archive": self._action_archive,
            "reorder": self._action_reorder,
            "stats": self._action_stats,
            "review_daily": self._action_review_daily,
        }

        if action_name not in handlers:
            return self._error(action_name, f"Unsupported action: {action_name}")

        try:
            return handlers[action_name](**kwargs)
        except Exception as e:
            return self._error(action_name, str(e))

    def _action_init(self, **kwargs: Any) -> dict[str, Any]:
        if self.storage.todo_path.exists():
            store = self.storage.load_store()
        else:
            store = self.storage.init_store()
        self.storage.ensure_auto_review_block()
        return self._success(
            "init",
            "TODO store initialized and daily review block ensured.",
            stats=self._compute_stats(store),
        )

    def _action_add(
        self,
        title: str | None = None,
        note: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due: str | None = None,
        tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        now = now_iso()

        clean_title = self._normalize_title(title)
        item_status = self._normalize_status(status or "todo")
        item_priority = self._normalize_priority(priority if priority is not None else 2)
        item_due = self._normalize_due(due)
        item_tags = self._normalize_string_list(tags)
        item_deps = self._normalize_id_list(depends_on, "depends_on")

        next_id = self._next_id(store)
        item = TodoItem(
            id=next_id,
            title=clean_title,
            status=item_status,
            priority=item_priority,
            note=(note or "").strip(),
            due=item_due,
            tags=item_tags,
            depends_on=item_deps,
            created_at=now,
            updated_at=now,
            completed_at=now if item_status == "done" else None,
        )

        temp_store = copy.deepcopy(store)
        temp_store.items.append(item)
        self._validate_dependencies(temp_store.items)

        store.items = temp_store.items
        store.meta.last_id = int(next_id[1:])
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)

        return self._success(
            "add",
            f"Added task {next_id}.",
            items=[self._to_public_item(item)],
            stats=self._compute_stats(store),
        )

    def _action_list(
        self,
        filters: dict[str, Any] | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        filtered = self._apply_filters(store.items, filters or {})
        ordered = self._sort_items(filtered, sort_by, sort_order)

        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be >= 1")
            ordered = ordered[:limit]

        return self._success(
            "list",
            f"Listed {len(ordered)} task(s).",
            items=[self._to_public_item(item) for item in ordered],
            stats=self._compute_stats(store),
        )

    def _action_update(
        self,
        id: str | None = None,
        patch: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        target_id = self._normalize_id(id, "id")
        patch_data = self._normalize_patch(patch)

        updated_item = self._update_single_item(store, target_id, patch_data)
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)

        return self._success(
            "update",
            f"Updated task {target_id}.",
            items=[self._to_public_item(updated_item)],
            stats=self._compute_stats(store),
        )

    def _action_bulk_update(
        self,
        ids: list[str] | None = None,
        patch: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        target_ids = self._normalize_id_list(ids, "ids")
        if not target_ids:
            raise ValueError("ids is required for bulk_update")

        patch_data = self._normalize_patch(patch)
        store = self.storage.load_or_init_store()
        temp = copy.deepcopy(store)
        updated: list[TodoItem] = []
        for task_id in target_ids:
            updated.append(self._update_single_item(temp, task_id, patch_data))
        temp.meta.updated_at = now_iso()

        self.storage.save_store(temp)
        return self._success(
            "bulk_update",
            f"Updated {len(updated)} task(s).",
            items=[self._to_public_item(item) for item in updated],
            stats=self._compute_stats(temp),
        )

    def _action_move(self, id: str | None = None, status: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if status is None:
            raise ValueError("status is required for move")
        return self._action_update(id=id, patch={"status": status})

    def _action_done(self, id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self._action_update(id=id, patch={"status": "done"})

    def _action_remove(self, id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        target_id = self._normalize_id(id, "id")
        item = self._find_item(store, target_id)
        if item is None:
            raise ValueError(f"Task not found: {target_id}")

        conflicts = self._find_external_dependents(store, {target_id})
        if conflicts:
            detail = ", ".join(sorted(conflicts.get(target_id, [])))
            raise ValueError(
                f"Cannot remove {target_id}: depended on by active task(s): {detail}."
            )

        store.items = [t for t in store.items if t.id != target_id]
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)
        return self._success(
            "remove",
            f"Removed task {target_id}.",
            items=[{"id": target_id}],
            stats=self._compute_stats(store),
        )

    def _action_bulk_remove(self, ids: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        target_ids = set(self._normalize_id_list(ids, "ids"))
        if not target_ids:
            raise ValueError("ids is required for bulk_remove")

        store = self.storage.load_or_init_store()
        existing_ids = {item.id for item in store.items}
        missing = sorted(target_ids - existing_ids)
        if missing:
            raise ValueError(f"Task(s) not found: {', '.join(missing)}")

        conflicts = self._find_external_dependents(store, target_ids)
        if conflicts:
            parts = []
            for dep, users in sorted(conflicts.items()):
                parts.append(f"{dep} <- {', '.join(sorted(users))}")
            raise ValueError(
                "Cannot bulk remove due to active dependencies: " + "; ".join(parts)
            )

        store.items = [item for item in store.items if item.id not in target_ids]
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)
        return self._success(
            "bulk_remove",
            f"Removed {len(target_ids)} task(s).",
            items=[{"id": task_id} for task_id in sorted(target_ids)],
            stats=self._compute_stats(store),
        )

    def _action_archive(
        self,
        ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        updated: list[TodoItem] = []
        now = now_iso()

        if ids:
            target_ids = set(self._normalize_id_list(ids, "ids"))
            for task_id in target_ids:
                item = self._find_item(store, task_id)
                if item is None:
                    raise ValueError(f"Task not found: {task_id}")
                if item.status != "done":
                    raise ValueError(f"Only done tasks can be archived: {task_id}")
                item.status = "archived"
                item.updated_at = now
                updated.append(item)
        else:
            scoped_filters = dict(filters or {})
            scoped_filters["statuses"] = ["done"]
            candidates = self._apply_filters(store.items, scoped_filters)
            for item in candidates:
                if item.status == "done":
                    item.status = "archived"
                    item.updated_at = now
                    updated.append(item)

        if not updated:
            return self._success(
                "archive",
                "No tasks archived.",
                items=[],
                stats=self._compute_stats(store),
            )

        store.meta.updated_at = now_iso()
        self.storage.save_store(store)
        return self._success(
            "archive",
            f"Archived {len(updated)} task(s).",
            items=[self._to_public_item(item) for item in updated],
            stats=self._compute_stats(store),
        )

    def _action_reorder(
        self,
        sort_by: str | None = None,
        sort_order: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        ordered = self._sort_items(store.items, sort_by or "priority", sort_order or "asc")
        store.items = ordered
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)
        return self._success(
            "reorder",
            f"Reordered {len(store.items)} task(s).",
            items=[self._to_public_item(item) for item in store.items[:20]],
            stats=self._compute_stats(store),
        )

    def _action_stats(self, **kwargs: Any) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        return self._success("stats", "Computed task statistics.", stats=self._compute_stats(store))

    def _action_review_daily(self, **kwargs: Any) -> dict[str, Any]:
        store = self.storage.load_or_init_store()
        today = today_date()
        if store.meta.last_review_date == today:
            return self._success(
                "review_daily",
                "Daily review already completed today.",
                stats=self._compute_stats(store),
            )

        open_items = [item for item in store.items if item.status in OPEN_STATUSES]
        ranked = self._sort_items(open_items, sort_by="priority", sort_order="asc")[:5]
        stats = self._compute_stats(store)
        summary = (
            f"Daily review: {stats['total']} total, {stats['open']} open, "
            f"{stats['overdue']} overdue, top focus: "
            + (", ".join(item.id for item in ranked) if ranked else "none")
        )

        store.meta.last_review_date = today
        store.meta.last_review_summary = summary
        store.meta.updated_at = now_iso()
        self.storage.save_store(store)

        return self._success(
            "review_daily",
            summary,
            items=[self._to_public_item(item) for item in ranked],
            stats=stats,
        )

    def _update_single_item(
        self,
        store: TodoStore,
        task_id: str,
        patch: dict[str, Any],
    ) -> TodoItem:
        item = self._find_item(store, task_id)
        if item is None:
            raise ValueError(f"Task not found: {task_id}")

        allowed_fields = {"title", "note", "status", "priority", "due", "tags", "depends_on"}
        unknown = sorted(set(patch.keys()) - allowed_fields)
        if unknown:
            raise ValueError(f"Unsupported patch field(s): {', '.join(unknown)}")

        if "title" in patch:
            item.title = self._normalize_title(patch.get("title"))
        if "note" in patch:
            item.note = str(patch.get("note") or "").strip()
        if "priority" in patch:
            item.priority = self._normalize_priority(patch.get("priority"))
        if "due" in patch:
            item.due = self._normalize_due(patch.get("due"))
        if "tags" in patch:
            item.tags = self._normalize_string_list(patch.get("tags"))
        if "depends_on" in patch:
            item.depends_on = self._normalize_id_list(patch.get("depends_on"), "depends_on")
        if "status" in patch:
            item.status = self._normalize_status(patch.get("status"))

        self._validate_dependencies(store.items)

        item.updated_at = now_iso()
        if item.status == "done" and not item.completed_at:
            item.completed_at = item.updated_at
        if item.status in OPEN_STATUSES:
            item.completed_at = None

        return item

    def _compute_stats(self, store: TodoStore) -> dict[str, Any]:
        counts = {status: 0 for status in VALID_STATUSES}
        now = datetime.now()
        overdue = 0
        priority_dist = {"1": 0, "2": 0, "3": 0, "4": 0}
        for item in store.items:
            counts[item.status] = counts.get(item.status, 0) + 1
            if item.status in OPEN_STATUSES and self._is_overdue(item, now):
                overdue += 1
            if item.status != "archived":
                priority_dist[str(item.priority)] += 1

        open_count = counts["todo"] + counts["doing"] + counts["blocked"]
        return {
            "total": len(store.items),
            "open": open_count,
            "overdue": overdue,
            "by_status": counts,
            "priority_distribution": priority_dist,
            "last_review_date": store.meta.last_review_date,
            "last_review_summary": store.meta.last_review_summary,
        }

    def _find_item(self, store: TodoStore, task_id: str) -> TodoItem | None:
        for item in store.items:
            if item.id == task_id:
                return item
        return None

    def _next_id(self, store: TodoStore) -> str:
        existing = {item.id for item in store.items}
        next_num = max(store.meta.last_id, 0) + 1
        while f"T{next_num:04d}" in existing:
            next_num += 1
        return f"T{next_num:04d}"

    def _apply_filters(self, items: list[TodoItem], filters: dict[str, Any]) -> list[TodoItem]:
        result = list(items)
        include_archived = bool(filters.get("include_archived", False))
        statuses = filters.get("statuses")
        status_set: set[str] | None = None

        if statuses is not None:
            if not isinstance(statuses, list):
                raise ValueError("filters.statuses must be a list")
            status_set = {self._normalize_status(s) for s in statuses}
        elif not include_archived:
            result = [item for item in result if item.status != "archived"]

        if status_set is not None:
            result = [item for item in result if item.status in status_set]

        tags_any = self._normalize_string_list(filters.get("tags_any")) if "tags_any" in filters else []
        if tags_any:
            tags_any_set = set(tags_any)
            result = [item for item in result if tags_any_set.intersection(item.tags)]

        tags_all = self._normalize_string_list(filters.get("tags_all")) if "tags_all" in filters else []
        if tags_all:
            tags_all_set = set(tags_all)
            result = [item for item in result if tags_all_set.issubset(set(item.tags))]

        keyword = str(filters.get("keyword", "")).strip().lower()
        if keyword:
            result = [
                item
                for item in result
                if keyword in item.id.lower()
                or keyword in item.title.lower()
                or keyword in item.note.lower()
            ]

        if filters.get("priority_min") is not None:
            pmin = self._normalize_priority(filters.get("priority_min"))
            result = [item for item in result if item.priority >= pmin]
        if filters.get("priority_max") is not None:
            pmax = self._normalize_priority(filters.get("priority_max"))
            result = [item for item in result if item.priority <= pmax]

        due_before = filters.get("due_before")
        if due_before is not None:
            cutoff = self._parse_due_datetime(str(due_before))
            result = [item for item in result if item.due and self._parse_due_datetime(item.due) <= cutoff]

        due_after = filters.get("due_after")
        if due_after is not None:
            cutoff = self._parse_due_datetime(str(due_after))
            result = [item for item in result if item.due and self._parse_due_datetime(item.due) >= cutoff]

        if filters.get("overdue") is not None:
            overdue_flag = bool(filters.get("overdue"))
            now = datetime.now()
            result = [
                item
                for item in result
                if (self._is_overdue(item, now) and overdue_flag)
                or (not self._is_overdue(item, now) and not overdue_flag)
            ]

        return result

    def _sort_items(
        self,
        items: list[TodoItem],
        sort_by: str | None,
        sort_order: str | None,
    ) -> list[TodoItem]:
        if not sort_by:
            return list(items)

        key_name = str(sort_by).strip().lower()
        if key_name not in {"priority", "due", "created", "updated"}:
            raise ValueError("sort_by must be one of: priority, due, created, updated")
        order = (sort_order or "asc").strip().lower()
        if order not in {"asc", "desc"}:
            raise ValueError("sort_order must be one of: asc, desc")
        reverse = order == "desc"

        def created_ts(item: TodoItem) -> float:
            return self._parse_general_datetime(item.created_at).timestamp() if item.created_at else 0.0

        def updated_ts(item: TodoItem) -> float:
            return self._parse_general_datetime(item.updated_at).timestamp() if item.updated_at else 0.0

        def due_ts(item: TodoItem) -> float:
            if not item.due:
                return float("inf")
            return self._parse_due_datetime(item.due).timestamp()

        key_map = {
            "priority": lambda item: (item.priority, due_ts(item), created_ts(item)),
            "due": lambda item: (due_ts(item), item.priority, created_ts(item)),
            "created": lambda item: created_ts(item),
            "updated": lambda item: updated_ts(item),
        }
        return sorted(items, key=key_map[key_name], reverse=reverse)

    def _find_external_dependents(self, store: TodoStore, target_ids: set[str]) -> dict[str, list[str]]:
        conflicts: dict[str, list[str]] = {task_id: [] for task_id in target_ids}
        for item in store.items:
            if item.status == "archived":
                continue
            if item.id in target_ids:
                continue
            for dep in item.depends_on:
                if dep in target_ids:
                    conflicts[dep].append(item.id)
        return {k: v for k, v in conflicts.items() if v}

    def _validate_dependencies(self, items: list[TodoItem]) -> None:
        id_set = {item.id for item in items}
        for item in items:
            for dep in item.depends_on:
                if dep == item.id:
                    raise ValueError(f"Task cannot depend on itself: {item.id}")
                if dep not in id_set:
                    raise ValueError(f"Dependency not found for {item.id}: {dep}")

        active = [item for item in items if item.status != "archived"]
        graph = {item.id: [dep for dep in item.depends_on if dep in {a.id for a in active}] for item in active}
        state: dict[str, int] = {}

        def dfs(node: str, stack: list[str]) -> None:
            state[node] = 1
            stack.append(node)
            for nxt in graph.get(node, []):
                if state.get(nxt, 0) == 0:
                    dfs(nxt, stack)
                elif state.get(nxt) == 1:
                    cycle = " -> ".join(stack + [nxt])
                    raise ValueError(f"Dependency cycle detected: {cycle}")
            stack.pop()
            state[node] = 2

        for node in graph:
            if state.get(node, 0) == 0:
                dfs(node, [])

    def _to_public_item(self, item: TodoItem) -> dict[str, Any]:
        due_overdue = self._is_overdue(item, datetime.now())
        return {
            "id": item.id,
            "title": item.title,
            "status": item.status,
            "priority": item.priority,
            "due": item.due,
            "tags": item.tags,
            "depends_on": item.depends_on,
            "note": item.note,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "completed_at": item.completed_at,
            "overdue": due_overdue,
        }

    def _is_overdue(self, item: TodoItem, now: datetime) -> bool:
        if item.status not in OPEN_STATUSES or not item.due:
            return False
        return self._parse_due_datetime(item.due) < now

    def _normalize_title(self, title: Any) -> str:
        value = str(title or "").strip()
        if not value:
            raise ValueError("title is required")
        return value

    def _normalize_status(self, status: Any) -> str:
        value = str(status or "").strip().lower()
        if value not in VALID_STATUSES:
            raise ValueError(f"status must be one of {list(VALID_STATUSES)}")
        return value

    def _normalize_priority(self, priority: Any) -> int:
        try:
            value = int(priority)
        except (TypeError, ValueError):
            raise ValueError("priority must be an integer in range 1..4") from None
        if value < 1 or value > 4:
            raise ValueError("priority must be an integer in range 1..4")
        return value

    def _normalize_due(self, due: Any) -> str | None:
        if due is None:
            return None
        text = str(due).strip()
        if not text:
            return None
        parsed = self._parse_due_datetime(text)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text
        return parsed.isoformat(timespec="seconds")

    def _normalize_string_list(self, values: Any) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError("Expected a list of strings")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise ValueError("Expected a list of strings")
            clean = value.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            result.append(clean)
        return result

    def _normalize_id(self, value: Any, field: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            raise ValueError(f"{field} is required")
        if not _ID_PATTERN.match(text):
            raise ValueError(f"{field} must match pattern T####")
        return text

    def _normalize_id_list(self, values: Any, field: str) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = self._normalize_id(value, field)
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _normalize_patch(self, patch: dict[str, Any] | None) -> dict[str, Any]:
        if patch is None:
            raise ValueError("patch is required")
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        if not patch:
            raise ValueError("patch must not be empty")
        return patch

    def _parse_due_datetime(self, value: str) -> datetime:
        text = value.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            d = date.fromisoformat(text)
            return datetime.combine(d, time(23, 59, 59))
        return self._parse_general_datetime(text)

    def _parse_general_datetime(self, value: str) -> datetime:
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt

    def _success(
        self,
        action: str,
        summary: str,
        *,
        items: list[dict[str, Any]] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "action": action,
            "summary": summary,
            "items": items or [],
            "stats": stats or {},
            "errors": [],
        }

    def _error(self, action: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "action": action,
            "summary": message,
            "items": [],
            "stats": {},
            "errors": [message],
        }

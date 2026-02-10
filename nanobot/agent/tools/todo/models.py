"""Models for TODO tool data and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TodoStatus = Literal["todo", "doing", "blocked", "done", "archived"]
TodoPriority = int

VALID_STATUSES: tuple[TodoStatus, ...] = ("todo", "doing", "blocked", "done", "archived")
OPEN_STATUSES: set[TodoStatus] = {"todo", "doing", "blocked"}
COMPLETED_STATUSES: set[TodoStatus] = {"done", "archived"}


def _normalize_string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Expected a list of strings")

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError("Expected a list of strings")
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


def _validate_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        raise ValueError("priority must be an integer in range 1..4") from None
    if priority < 1 or priority > 4:
        raise ValueError("priority must be an integer in range 1..4")
    return priority


def _validate_status(value: Any) -> TodoStatus:
    status = str(value or "todo").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {list(VALID_STATUSES)}")
    return status  # type: ignore[return-value]


@dataclass(slots=True)
class TodoItem:
    """Single TODO item."""

    id: str
    title: str
    status: TodoStatus = "todo"
    priority: TodoPriority = 2
    note: str = ""
    due: str | None = None
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoItem":
        if not isinstance(data, dict):
            raise ValueError("Todo item must be an object")

        item_id = str(data.get("id", "")).strip()
        if not item_id:
            raise ValueError("Todo item id is required")

        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError(f"title is required for item {item_id}")

        status = _validate_status(data.get("status", "todo"))
        priority = _validate_priority(data.get("priority", 2))
        note = str(data.get("note", ""))
        due = data.get("due")
        if due is not None:
            due = str(due).strip() or None

        created_at = str(data.get("created_at", "")).strip()
        updated_at = str(data.get("updated_at", "")).strip()
        completed_at = data.get("completed_at")
        if completed_at is not None:
            completed_at = str(completed_at).strip() or None

        return cls(
            id=item_id,
            title=title,
            status=status,
            priority=priority,
            note=note,
            due=due,
            tags=_normalize_string_list(data.get("tags")),
            depends_on=_normalize_string_list(data.get("depends_on")),
            created_at=created_at,
            updated_at=updated_at,
            completed_at=completed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TodoStoreMeta:
    """Metadata for TODO store."""

    version: int = 1
    last_id: int = 0
    last_review_date: str | None = None
    last_review_summary: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoStoreMeta":
        if not isinstance(data, dict):
            raise ValueError("meta must be an object")
        version = int(data.get("version", 1))
        last_id = int(data.get("last_id", 0))
        return cls(
            version=version,
            last_id=max(0, last_id),
            last_review_date=(str(data.get("last_review_date")).strip() or None)
            if data.get("last_review_date") is not None
            else None,
            last_review_summary=(str(data.get("last_review_summary")).strip() or None)
            if data.get("last_review_summary") is not None
            else None,
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TodoStore:
    """TODO store containing metadata and items."""

    meta: TodoStoreMeta = field(default_factory=TodoStoreMeta)
    items: list[TodoItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoStore":
        if not isinstance(data, dict):
            raise ValueError("store must be an object")
        raw_meta = data.get("meta", {})
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("items must be an array")
        items = [TodoItem.from_dict(item) for item in raw_items]
        return cls(meta=TodoStoreMeta.from_dict(raw_meta), items=items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }

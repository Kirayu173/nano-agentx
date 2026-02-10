"""Markdown storage backend for TODO data."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from nanobot.agent.tools.todo.models import TodoStore, TodoStoreMeta

TODO_DATA_START_MARKER = "<!-- TODO_DATA_START -->"
TODO_DATA_END_MARKER = "<!-- TODO_DATA_END -->"

TODO_AUTO_REVIEW_START_MARKER = "<!-- TODO_AUTO_REVIEW_START -->"
TODO_AUTO_REVIEW_END_MARKER = "<!-- TODO_AUTO_REVIEW_END -->"

TODO_AUTO_REVIEW_BLOCK = f"""{TODO_AUTO_REVIEW_START_MARKER}
- [ ] Daily TODO review: use `todo(action="review_daily")`; if it runs, summarize key changes briefly.
{TODO_AUTO_REVIEW_END_MARKER}"""


def now_iso() -> str:
    """Current local time in ISO format with second precision."""
    return datetime.now().isoformat(timespec="seconds")


def today_date() -> str:
    """Current local date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


class TodoStorage:
    """Persistence for TODO data using a markdown file with embedded JSON."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.memory_dir = self.workspace / "memory"
        self.todo_path = self.memory_dir / "todo.md"
        self.todo_backup_path = self.memory_dir / "todo.md.bak"
        self.heartbeat_path = self.workspace / "HEARTBEAT.md"

    def create_default_store(self) -> TodoStore:
        """Create an empty store with initialized metadata."""
        now = now_iso()
        return TodoStore(
            meta=TodoStoreMeta(
                version=1,
                last_id=0,
                created_at=now,
                updated_at=now,
            ),
            items=[],
        )

    def init_store(self) -> TodoStore:
        """Initialize todo file and daily review heartbeat block."""
        store = self.create_default_store()
        self.save_store(store)
        self.ensure_auto_review_block()
        return store

    def load_or_init_store(self) -> TodoStore:
        """Load existing store, or initialize if missing."""
        if not self.todo_path.exists():
            return self.init_store()
        return self.load_store()

    def load_store(self) -> TodoStore:
        """Load store from markdown data block."""
        if not self.todo_path.exists():
            raise FileNotFoundError(f"TODO file not found: {self.todo_path}")

        text = self.todo_path.read_text(encoding="utf-8")
        payload = self._extract_payload(text)
        return TodoStore.from_dict(payload)

    def save_store(self, store: TodoStore) -> None:
        """Save store to markdown using atomic write and single backup."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._render_markdown(store)

        if self.todo_path.exists():
            current = self.todo_path.read_text(encoding="utf-8")
            self.todo_backup_path.write_text(current, encoding="utf-8")

        tmp_path = self.todo_path.with_name(f"{self.todo_path.name}.tmp")
        tmp_path.write_text(markdown, encoding="utf-8")
        tmp_path.replace(self.todo_path)

    def ensure_auto_review_block(self) -> None:
        """Ensure HEARTBEAT.md contains the managed daily TODO review block."""
        if self.heartbeat_path.exists():
            content = self.heartbeat_path.read_text(encoding="utf-8")
        else:
            content = (
                "# Heartbeat Tasks\n\n"
                "This file is checked every 30 minutes by your nanobot agent.\n\n"
                "## Active Tasks\n\n"
                "## Completed\n"
            )

        pattern = re.compile(
            rf"{re.escape(TODO_AUTO_REVIEW_START_MARKER)}[\s\S]*?{re.escape(TODO_AUTO_REVIEW_END_MARKER)}",
            re.MULTILINE,
        )
        if pattern.search(content):
            next_content = pattern.sub(TODO_AUTO_REVIEW_BLOCK, content)
        else:
            suffix = "\n" if content.endswith("\n") else "\n\n"
            next_content = f"{content}{suffix}{TODO_AUTO_REVIEW_BLOCK}\n"

        if next_content != content:
            self.heartbeat_path.write_text(next_content, encoding="utf-8")

    def _extract_payload(self, markdown: str) -> dict:
        start_index = markdown.find(TODO_DATA_START_MARKER)
        end_index = markdown.find(TODO_DATA_END_MARKER)
        if start_index < 0 or end_index < 0 or end_index <= start_index:
            raise ValueError(
                "Invalid TODO file: TODO data block markers are missing or malformed. "
                "Run todo(action='init') to repair."
            )

        segment = markdown[start_index + len(TODO_DATA_START_MARKER) : end_index]
        fence_match = re.search(r"```json\s*([\s\S]*?)\s*```", segment)
        if not fence_match:
            raise ValueError(
                "Invalid TODO file: JSON fenced block not found between data markers. "
                "Run todo(action='init') to repair."
            )

        json_text = fence_match.group(1).strip()
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid TODO file: data JSON parse failed ({e.msg} at line {e.lineno}). "
                "Repair the JSON block or run todo(action='init')."
            ) from e
        if not isinstance(payload, dict):
            raise ValueError("Invalid TODO file: root JSON payload must be an object.")
        return payload

    def _render_markdown(self, store: TodoStore) -> str:
        now = now_iso()
        status_order = ("todo", "doing", "blocked", "done", "archived")
        section_titles = {
            "todo": "TODO",
            "doing": "DOING",
            "blocked": "BLOCKED",
            "done": "DONE",
            "archived": "ARCHIVED",
        }
        lines: list[str] = [
            "# TODO Board",
            "",
            "Managed by the `todo` tool. Manual edits are allowed in board text,",
            "but keep the JSON data block valid.",
            "",
            f"_Last rendered: {now}_",
            "",
            "## Board",
            "",
        ]

        for status in status_order:
            lines.append(f"### {section_titles[status]}")
            group = [item for item in store.items if item.status == status]
            if not group:
                lines.append("- (empty)")
                lines.append("")
                continue

            for item in group:
                checkbox = "[x]" if status in {"done", "archived"} else "[ ]"
                headline = f"- {checkbox} {item.id} | P{item.priority}"
                if item.due:
                    headline += f" | due:{item.due}"
                headline += f" | {item.title}"
                lines.append(headline)
                if item.tags:
                    lines.append(f"  tags: {', '.join(item.tags)}")
                if item.depends_on:
                    lines.append(f"  depends_on: {', '.join(item.depends_on)}")
                if item.note:
                    lines.append(f"  note: {item.note}")
            lines.append("")

        lines.extend(
            [
                TODO_DATA_START_MARKER,
                "```json",
                json.dumps(store.to_dict(), indent=2, ensure_ascii=False),
                "```",
                TODO_DATA_END_MARKER,
                "",
            ]
        )

        return "\n".join(lines)

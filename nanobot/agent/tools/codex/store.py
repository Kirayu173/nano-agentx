"""Persistent store for codex merge plans."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.agent.tools.codex.models import MergePlanRecord


class MergePlanStore:
    """Store merge plans under workspace/memory/merge_plans."""

    def __init__(self, workspace: Path):
        self._dir = workspace.resolve() / "memory" / "merge_plans"

    @property
    def directory(self) -> Path:
        return self._dir

    def save(self, record: MergePlanRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(record.plan_id)
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, plan_id: str) -> MergePlanRecord | None:
        path = self.path_for(plan_id)
        if not path.exists() or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return MergePlanRecord.from_dict(data)

    def list(self, limit: int = 20) -> list[MergePlanRecord]:
        if not self._dir.exists():
            return []

        records: list[MergePlanRecord] = []
        for path in self._dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            try:
                records.append(MergePlanRecord.from_dict(raw))
            except Exception:
                continue

        records.sort(key=lambda item: item.updated_at_ms, reverse=True)
        return records[: max(1, limit)]

    def path_for(self, plan_id: str) -> Path:
        safe = (plan_id or "").strip()
        return self._dir / f"{safe}.json"

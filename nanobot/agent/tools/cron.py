"""Cron tool for scheduling reminders and tasks."""

import time
from datetime import datetime
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""
    
    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._channel = ""
        self._chat_id = ""
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current session context for delivery."""
        self._channel = channel
        self._chat_id = chat_id
    
    @property
    def name(self) -> str:
        return "cron"
    
    @property
    def description(self) -> str:
        return "Schedule one-time or recurring reminders and tasks. Actions: add, list, remove."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform"
                },
                "message": {
                    "type": "string",
                    "description": "Reminder message (for add)"
                },
                "mode": {
                    "type": "string",
                    "enum": ["reminder", "task"],
                    "description": "reminder: deliver message directly; task: execute through agent each run (default: reminder)"
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)"
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)"
                },
                "in_seconds": {
                    "type": "integer",
                    "description": "Run once after N seconds (for one-time reminders)"
                },
                "at": {
                    "type": "string",
                    "description": "Run once at ISO datetime (e.g. '2026-02-11T09:00:00' or with timezone offset)"
                },
                "job_id": {
                    "type": "string",
                    "description": "Job ID (for remove)"
                }
            },
            "required": ["action"]
        }
    
    async def execute(
        self,
        action: str,
        message: str = "",
        mode: str = "reminder",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        in_seconds: int | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any
    ) -> str:
        if action == "add":
            return self._add_job(message, mode, every_seconds, cron_expr, in_seconds, at)
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"
    
    def _add_job(
        self,
        message: str,
        mode: str,
        every_seconds: int | None,
        cron_expr: str | None,
        in_seconds: int | None,
        at: str | None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"
        if mode not in {"reminder", "task"}:
            return "Error: mode must be 'reminder' or 'task'"

        schedule_inputs = [
            every_seconds is not None,
            bool(cron_expr),
            in_seconds is not None,
            bool(at),
        ]
        if sum(schedule_inputs) != 1:
            return "Error: specify exactly one of every_seconds, cron_expr, in_seconds, or at"

        delete_after_run = False
        now_ms = int(time.time() * 1000)

        # Build schedule
        if every_seconds is not None:
            if every_seconds <= 0:
                return "Error: every_seconds must be > 0"
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr)
        elif in_seconds is not None:
            if in_seconds <= 0:
                return "Error: in_seconds must be > 0"
            schedule = CronSchedule(kind="at", at_ms=now_ms + in_seconds * 1000)
            delete_after_run = True
        elif at:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return "Error: at must be an ISO datetime like 2026-02-11T09:00:00"
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            at_ms = int(dt.timestamp() * 1000)
            if at_ms <= now_ms:
                return "Error: at must be in the future"
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after_run = True
        else:
            return "Error: specify exactly one of every_seconds, cron_expr, in_seconds, or at"
        
        payload_kind = "system_event" if mode == "reminder" else "agent_turn"
        job = self._cron.add_job(
            name=message[:30],
            schedule=schedule,
            message=message,
            payload_kind=payload_kind,
            deliver=True,
            channel=self._channel,
            to=self._chat_id,
            delete_after_run=delete_after_run,
        )
        schedule_label = "one-time" if schedule.kind == "at" else "recurring"
        return f"Created {schedule_label} job '{job.name}' (id: {job.id}, mode: {mode})"
    
    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [f"- {j.name} (id: {j.id}, {j.schedule.kind})" for j in jobs]
        return "Scheduled jobs:\n" + "\n".join(lines)
    
    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"

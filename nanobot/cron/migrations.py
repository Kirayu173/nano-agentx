"""Cron data migrations."""

import shutil
from pathlib import Path


def migrate_codex_merge_cron(cron_service, workspace: Path) -> None:
    """Apply one-time cron/report migration for codex merge workflow."""
    from nanobot.cron.types import CronSchedule

    jobs = cron_service.list_jobs(include_disabled=True)

    legacy_job = next((job for job in jobs if job.id == "8dbfbddb"), None)
    delivery_channel = legacy_job.payload.channel if legacy_job else None
    delivery_to = legacy_job.payload.to if legacy_job else None
    if legacy_job is not None:
        cron_service.remove_job(legacy_job.id)

    reports_dir = workspace / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    legacy_reports_dir = workspace / "report"
    if legacy_reports_dir.exists() and legacy_reports_dir.is_dir():
        shutil.rmtree(legacy_reports_dir, ignore_errors=True)

    jobs = cron_service.list_jobs(include_disabled=True)
    exists = any(
        job.payload.kind == "tool_call"
        and (job.payload.tool_name or "") == "codex_merge"
        and isinstance(job.payload.tool_args, dict)
        and job.payload.tool_args.get("action") == "plan_latest"
        and job.schedule.kind == "cron"
        and (job.schedule.expr or "").strip() == "0 23 * * *"
        for job in jobs
    )
    if exists:
        return

    cron_service.add_job(
        name="nightly-codex-merge-plan",
        schedule=CronSchedule(kind="cron", expr="0 23 * * *"),
        message="Nightly codex merge planning",
        payload_kind="tool_call",
        tool_name="codex_merge",
        tool_args={
            "action": "plan_latest",
            "base_ref": "origin/main",
            "upstream_ref": "upstream/main",
            "target_branch": "main",
        },
        deliver=bool(delivery_channel and delivery_to),
        channel=delivery_channel,
        to=delivery_to,
    )


from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nanobot.cron.service import CronService, _compute_next_run
from nanobot.cron.types import CronSchedule


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_compute_next_run_cron_respects_schedule_timezone() -> None:
    now = datetime(2026, 2, 11, 12, 43, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    schedule = CronSchedule(kind="cron", expr="0 13 * * *", tz="Asia/Shanghai")

    next_run_ms = _compute_next_run(schedule, _ms(now))

    expected = datetime(2026, 2, 11, 13, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_run_ms == _ms(expected)


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None

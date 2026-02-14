from datetime import datetime
from zoneinfo import ZoneInfo

from nanobot.cron.service import _compute_next_run
from nanobot.cron.types import CronSchedule


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_compute_next_run_cron_respects_schedule_timezone() -> None:
    now = datetime(2026, 2, 11, 12, 43, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    schedule = CronSchedule(kind="cron", expr="0 13 * * *", tz="Asia/Shanghai")

    next_run_ms = _compute_next_run(schedule, _ms(now))

    expected = datetime(2026, 2, 11, 13, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_run_ms == _ms(expected)

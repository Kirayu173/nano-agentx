---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron` tool to schedule recurring reminders, recurring agent tasks, or one-time reminders.

## Three Modes

1. **Reminder** - `mode="reminder"`; periodic direct reminders only.
2. **Task** - `mode="task"`; periodic agent execution tasks.
3. **One-time** - `mode="one_time"`; one-shot direct reminder that auto-deletes.

## Mode Rules

- `reminder` / `task`: require exactly one of `every_seconds` or `cron_expr`.
- `one_time`: require exactly one of `in_seconds` or `at`.
- Do not mix periodic and one-time parameters in the same call.

## Examples

Recurring reminder:
```
cron(action="add", mode="reminder", message="Time to take a break!", every_seconds=1200)
```

Recurring task:
```
cron(action="add", mode="task", message="Check HKUDS/nanobot GitHub stars and report", cron_expr="0 9 * * *")
```

One-time reminder in 2 minutes:
```
cron(action="add", mode="one_time", message="Time to drink water!", in_seconds=120)
```

One-time reminder at fixed time:
```
cron(action="add", mode="one_time", message="Meeting starts now", at="2026-02-12T10:30:00")
```

Timezone-aware cron:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

For reminders, write the final reminder text (for example, `"Time to take a break!"`) instead of a scheduling command (for example, `"Remind me every 20 minutes"`), to avoid re-scheduling loops.

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | mode: "reminder", every_seconds: 1200 |
| every hour | mode: "reminder", every_seconds: 3600 |
| every day at 8am | mode: "task", cron_expr: "0 8 * * *" |
| weekdays at 5pm | mode: "task", cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | mode: "task", cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| in 2 minutes | mode: "one_time", in_seconds: 120 |
| at a specific time | mode: "one_time", at: ISO datetime string (compute from current time) |

## Timezone

Use `tz` with `cron_expr` to schedule in a specific IANA timezone. Without `tz`, the server's local timezone is used.

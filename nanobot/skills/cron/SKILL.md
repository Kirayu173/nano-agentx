---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron` tool to schedule reminders or recurring tasks.

## Two Modes

1. **Reminder** - `mode="reminder"`; message is sent directly to user
2. **Task** - `mode="task"`; message is a task description, agent executes and sends result

## Examples

Fixed reminder:
```
cron(action="add", mode="reminder", message="Time to take a break!", every_seconds=1200)
```

Dynamic task (agent executes each time):
```
cron(action="add", mode="task", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
```

For reminders, write the final reminder text (for example, `"Time to take a break!"`) instead of a scheduling command (for example, `"Remind me every 20 minutes"`), to avoid re-scheduling loops.

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |

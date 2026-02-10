# TODO Organizer Workflows

## 1) First-Time Setup

```text
todo(action="init")
todo(action="stats")
```

Use this once for a new workspace to create `memory/todo.md` and ensure daily review heartbeat block.

## 2) Capture and Triage

```text
todo(action="add", title="Prepare release notes", priority=2, tags=["release"])
todo(action="add", title="Fix login edge case", priority=1, tags=["bug"], due="2026-02-12")
todo(action="list", sort_by="priority", sort_order="asc")
```

## 3) Dependency-Safe Planning

```text
todo(action="add", title="Deploy DB migration", priority=1)
todo(action="add", title="Run data backfill", priority=2, depends_on=["T0001"])
```

If a dependency is invalid or cyclic, the tool will reject the operation with a clear error.

## 4) Batch Operations

```text
todo(action="bulk_update", ids=["T0002", "T0003"], patch={"status":"doing"})
todo(action="bulk_update", ids=["T0002", "T0003"], patch={"tags":["sprint-12"]})
todo(action="bulk_remove", ids=["T0010", "T0011"])
```

## 5) Completion and Archive

```text
todo(action="done", id="T0002")
todo(action="archive", ids=["T0002"])
```

Archive only applies to tasks already marked `done`.

## 6) Daily Review

```text
todo(action="review_daily")
```

Run once per day; repeated calls on the same day no-op with a clear message.

## 7) Reminder-on-Demand

When user explicitly asks for reminder/notification:

```text
cron(action="add", message="Remind me to submit report", cron_expr="0 9 * * *")
```

If `cron` tool is unavailable, use:

```bash
nanobot cron add --name "todo-reminder" --message "Remind me to submit report" --cron "0 9 * * *"
```

Do not auto-create reminders from `due` fields unless user asked.

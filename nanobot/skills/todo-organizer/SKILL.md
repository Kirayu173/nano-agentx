---
name: todo-organizer
description: Organize and maintain TODO tasks using the `todo` tool. Use for task capture, prioritization, dependency checks, batch updates, daily reviews, and backlog cleanup. Create reminders only when the user explicitly asks for notifications.
---

# TODO Organizer

Use this skill for TODO management workflows. Prefer the `todo` tool as the source of truth.

## Core Rules

- Initialize TODO storage with `todo(action="init")` before first use.
- Use `todo` actions for CRUD, filtering, bulk edits, archive, and stats.
- Do not manually edit `memory/todo.md` unless repairing broken data.
- Create reminders only when the user explicitly asks (manual reminder policy).

## Reminder Policy

When the user explicitly asks for a reminder:

1. Prefer `cron(action="add", ...)` if the `cron` tool is available.
2. If `cron` tool is unavailable, use `exec` with `nanobot cron add ...`.
3. Do not create reminders automatically from `due` fields.

## Daily Review Policy

- Use `todo(action="review_daily")` for daily summarization.
- This action is idempotent for the same day.
- Keep review output concise and actionable.

## Typical Workflow

1. `todo(action="init")`
2. Capture tasks with `todo(action="add", ...)`
3. Triage with `todo(action="list", sort_by="priority", sort_order="asc")`
4. Batch maintenance via `bulk_update` / `bulk_remove`
5. Close work with `done` and periodic `archive`
6. Report health with `stats`

## References

- Detailed workflows: `references/workflows.md`

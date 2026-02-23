# Tool Usage Notes

Tool signatures are provided automatically via function calling. This file documents non-obvious constraints and patterns.

## File Tools

- `read_file(path)`
- `write_file(path, content)`
- `edit_file(path, old_text, new_text)`
- `list_dir(path)`

When workspace restriction is enabled, file paths outside the workspace are blocked.

## Shell Tool

- `exec(command, working_dir=None)`

Safety notes:

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked
- Output is truncated
- Workspace restriction may block external paths

## Web Tools

- `web_search(query, count=5)` supports provider config (`brave`, `tavily`, `serper`)
- `web_fetch(url, extractMode="markdown", maxChars=50000)`
- `browser_run(...)` is available only when browser automation is enabled

## Messaging and Delegation

- `message(content, channel=None, chat_id=None, media=None)`
- `spawn(task, label=None)`

`media` accepts local file paths for channels that support attachments.

## TODO Tool

- `todo(action=..., ...)` manages `memory/todo.md`

Use `todo` as the source of truth for TODO operations. Prefer `review_daily` for daily summaries.

## Cron Tool

- `cron(action=..., mode=..., ...)` schedules reminders and periodic tasks

Mode rules:

- `reminder`: periodic only (`every_seconds` or `cron_expr`)
- `task`: periodic only, executes via agent turn
- `one_time`: one-shot only (`in_seconds` or `at`)

## Codex Tools (Optional)

- `codex_run(...)`
- `codex_merge(...)`

These tools are available only when Codex tooling is enabled in config.

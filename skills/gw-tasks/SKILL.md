---
name: gw-tasks
description: >
  Create, list, and complete Google Tasks via CLI.
  TRIGGERS: "google tasks", "create task", "task list", "complete task", "to-do", "task reminder"
---

# Tasks

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

```bash
$CLI tasks create --title "Follow up" [--due "2026-03-05"] [--notes "..."]
$CLI tasks list --compact
$CLI tasks complete TASK_ID
```

## Response Shapes (validated)

`list`: `{"tasks":[{"id","title","due","notes","status"}],"count":0}`

`create`: `{"created":true,"id","title","due"}`

`complete`: `{"completed":true,"id"}`

## Examples

```bash
# Create task with due date
$CLI tasks create --title "Review Q2 projections" --due "2026-04-10" --notes "Check runway"

# List open tasks
$CLI tasks list --compact

# Complete a task
$CLI tasks complete "MDIxNTc2OTcw..."
```

## Notes

- `--due` format is `YYYY-MM-DD`.
- Default account is `main`. Task IDs are long base64 strings.

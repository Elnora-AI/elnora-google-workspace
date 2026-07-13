---
name: google-workspace
description: >
  Google Workspace CLI — routes to service-specific skills for Gmail, Sheets, Calendar, Tasks, Docs, Forms.
  TRIGGERS: "gmail", "email", "send email", "inbox", "calendar", "meeting", "sheets",
  "spreadsheet", "tasks", "google docs", "gdoc", "outreach", "google form", "form responses",
  "create google form", "feedback form", "survey form"
---

# Google Workspace CLI

Routes to service-specific skills. Use the right skill for your task:

| Need | Skill |
|------|-------|
| Send, draft, reply, reply-all, manage email (with attachments) | **gw-gmail** |
| Quick inbox check | **gw-inbox** |
| Read/write spreadsheets | **gw-sheets** |
| Schedule meetings | **gw-calendar** |
| Manage to-dos | **gw-tasks** |
| Read/create/edit documents | **gw-docs** |
| Create / edit Google Forms, read metadata and responses | **gw-forms** |

**Attachments are supported** on every sending verb: `send`, `draft`, `draft-reply`, `draft-reply-all`, `reply`, `reply-all`, `update-draft`, and `attach-to-draft`. Use `--attach /path/to/file` and repeat for multiple files. See **gw-gmail** skill for examples.

**Draft modification is supported.** Full lifecycle: `list-drafts` → `get-draft` → `update-draft` / `attach-to-draft` → `send-draft` or `delete-draft`. See **gw-gmail**.

**Reply auto-preserves original Cc** on `reply` and `draft-reply`. Reply-all expands Cc to include original To recipients. See **gw-gmail** for the `--cc` / `--no-cc` override semantics.

## UI Verification

All write operations (calendar create/update, docs create/append/replace, forms create/add-items, sheets write/append, gmail draft) should be verified visually using Chrome DevTools MCP (`mcp__chrome-devtools__navigate_page` + `mcp__chrome-devtools__take_screenshot`). Each service skill has specific verification steps — follow them before reporting done.

## CLI

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Auth Troubleshooting

Auth tokens live under `~/.config/gw/`, with a legacy fallback to `.google-token*.json` files at the **repo root**. The CLI finds them automatically — it is **CWD-independent**. If auth seems broken:

1. Check auth state: `gw auth status`
2. Read `${CLAUDE_PLUGIN_ROOT}/lib/auth.py` to understand resolution — do NOT guess paths
3. **Never** point to plugin cache paths (`~/.claude/plugins/cache/...`) — that's not where auth lives
4. Re-auth only if the token is genuinely missing: `gw auth login --account <name>`

## Account Routing

Accounts are user-defined in `accounts.json` (see `gw auth list` / `gw auth login --account <name>`); the names and emails below are only examples.

| Account | Example email | Typical use |
|---|---|---|
| `--account main` | `you@example.com` | Primary (default) |
| `--account work` | `work@example.com` | Work / outreach |
| `--account personal` | `personal@example.com` | Personal |

Use a dedicated outreach account (e.g. `--account work`) for cold outreach rather than your primary. Never default to `personal` — it must be passed explicitly. All commands output JSON; add `--compact` to save tokens.

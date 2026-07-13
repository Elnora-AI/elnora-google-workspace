---
name: gw-inbox
description: >
  Quick inbox scan — recent emails with sender, subject, snippet.
  TRIGGERS: "check inbox", "inbox", "new emails", "unread emails", "scan inbox",
  "what's in my inbox", "any new mail", "email summary"
---

# Inbox Scan

Quick scan of recent emails. For full operations (send, draft, reply), use **gw-gmail**.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

```bash
# Recent emails (default: last 24h)
$CLI gmail scan [--since "1d"] --compact

# Unread emails with more control
$CLI gmail list --query "is:unread" --limit 10 --compact

# Specific account
$CLI gmail scan --since "3d" --account work --compact

# List drafts (use this, not `list --query in:drafts`, if you need draftIds for chaining)
$CLI gmail list-drafts [--query "..."] [--limit 20] --compact
```

## Response Shape (validated)

`scan` and `list` return: `{"messages":[{"id","threadId","from","to","subject","date","snippet","labelIds"}],"count","query"}`

`list-drafts` (and `list --query "in:drafts"`, which auto-routes) returns a **different shape**: `{"drafts":[{"draftId","id","threadId","from","to","subject","date","snippet","labelIds"}],"count","query"}` — note the top-level `drafts` key instead of `messages`, and each entry includes `draftId` for chaining into `get-draft`/`update-draft`/`send-draft`/`delete-draft`.

## Read a specific email

```bash
$CLI gmail get MESSAGE_ID --compact
```

Returns: `{"id","threadId","from","to","subject","date","snippet","labelIds","body"}`

## Account Routing

Accounts are user-defined in `accounts.json` (see `gw auth list` / `gw auth login --account <name>`); the names and emails below are only examples.

| Account | Example email | Typical use |
|---|---|---|
| `--account main` | `you@example.com` | Primary inbox (default) |
| `--account work` | `work@example.com` | Work / outreach inbox |
| `--account personal` | `personal@example.com` | Personal inbox |

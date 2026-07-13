---
name: gw-gmail
description: >
  Send, draft, list, read, reply, scan, and manage Gmail via CLI.
  TRIGGERS: "gmail", "email", "send email", "draft email", "reply email", "inbox",
  "outreach", "scan replies", "mail", "compose", "trash email", "email thread"
---

# Gmail

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Account Routing

Accounts are user-defined in `accounts.json` (see `gw auth list` / `gw auth login --account <name>`); the names and emails below are only examples.

| Account | Example email | Typical use |
|---|---|---|
| `--account main` | `you@example.com` | Primary (default) |
| `--account work` | `work@example.com` | Work / outreach |
| `--account personal` | `personal@example.com` | Personal |

## Commands

### Send & Draft

Always use `--body-file -` with heredoc. Never `--body "..."` — special characters break shell quoting.

```bash
$CLI gmail send --to EMAIL --subject "..." --body-file - [--cc "a@x.com,b@y.com"] [--thread-id TID] [--attach FILE] [--account work] <<'BODY'
Email body here.
BODY

$CLI gmail draft --to EMAIL --subject "..." --body-file - [--cc "a@x.com,b@y.com"] [--thread-id TID] [--attach FILE] [--account work] <<'BODY'
Draft body here.
BODY
```

Multiple recipients: `--to` and `--cc` accept comma-separated addresses or repeated flags. Always comma-separate in one string for clarity: `--cc "a@x.com,b@y.com,c@z.com"`.

Response: `{"sent":true,"id":"MSG_ID","threadId":"TID"}` or `{"drafted":true,"id":"DRAFT_ID","messageId":"MSG_ID"}`

### Attachments

**The CLI fully supports file attachments.** Use `--attach` with an absolute file path. Repeat for multiple files.

```bash
# Single attachment
$CLI gmail send --to EMAIL --subject "..." --attach /path/to/file.pdf --body-file - <<'BODY'
See attached.
BODY

# Multiple attachments
$CLI gmail draft --to EMAIL --subject "..." --attach /path/to/report.pdf --attach /path/to/data.csv --body-file - <<'BODY'
Two files attached.
BODY

# Attachment on a draft reply
$CLI gmail draft-reply MESSAGE_ID --attach /path/to/file.pdf --body-file - <<'BODY'
Attaching the document you requested.
BODY
```

Works on: `send`, `draft`, `draft-reply`. Any file type. Uses MIME multipart/mixed encoding. Supports `~/` paths.

**Quote paths with spaces** (e.g. Google Drive): `--attach "/path/with spaces/file.pdf"`

### Reply

All four reply commands preserve the thread, add a `Re:` subject prefix if missing, and include your display name + signature from `sendAs`. **Original Cc is auto-preserved** on plain reply/draft-reply (minus your own address). Override with `--cc "a@x,b@y"` or clear with `--no-cc`.

```bash
# Plain reply (To = original sender, Cc preserved from original)
$CLI gmail reply MESSAGE_ID --body-file - [--cc "a@x,b@y"] [--no-cc] [--to EMAIL] [--attach FILE] [--account work] <<'BODY'
Reply text.
BODY

# Plain draft reply — does NOT send
$CLI gmail draft-reply MESSAGE_ID --body-file - [--cc "a@x,b@y"] [--no-cc] [--to EMAIL] [--attach FILE] [--account work] <<'BODY'
Draft reply text.
BODY

# Reply-all: To = original sender, Cc = original To + Cc, deduped, minus self
$CLI gmail reply-all MESSAGE_ID --body-file - [--cc "..."] [--no-cc] [--to EMAIL] [--attach FILE] <<'BODY'
Reply to everyone on the thread.
BODY

# Draft reply-all — does NOT send
$CLI gmail draft-reply-all MESSAGE_ID --body-file - [--cc "..."] [--no-cc] [--to EMAIL] [--attach FILE] <<'BODY'
Draft reply to everyone.
BODY
```

**Cc behavior:**
- Default (no flag): plain `reply`/`draft-reply` preserves the original Cc list. `reply-all`/`draft-reply-all` expands Cc to include original To recipients too (minus you, minus whoever's in your new To).
- `--cc "frank@w.com,gina@x.com"`: explicit override, discards original Cc.
- `--no-cc`: force Cc empty. Conflicts with `--cc`.
- `--to "dave@z.com"`: override the primary recipient. In reply-all, the original sender moves to Cc instead of being dropped.

**Self-reply (follow-up on your own sent message):** detected via the authenticated `sendAs` email. Plain reply flips To to the first original recipient. Reply-all flips To to all original recipients. Cc is preserved from the original in both modes.

### Read

```bash
$CLI gmail list [--query "is:unread"] [--limit 20] [--account main] --compact
$CLI gmail get MESSAGE_ID --compact
$CLI gmail get-thread THREAD_ID --compact
$CLI gmail scan [--since "1d"] --compact          # no --limit or --query
$CLI gmail labels --compact
```

Response shapes (validated):

`list`: `{"messages":[{"id","threadId","from","to","subject","date","snippet","labelIds"}],"count","query"}`

`get`: `{"id","threadId","from","to","subject","date","snippet","labelIds","body"}`

`get-thread`: `{"messages":[...],"count","threadId"}`

`scan`: `{"messages":[...],"count","query"}`

`labels`: `{"labels":[{"id","name","type"}],"count"}`

### Manage Drafts

Full lifecycle — find, modify, send, delete:

```bash
# Find drafts — returns draftId for chaining
$CLI gmail list-drafts [--query "from:someone@example.com"] [--limit 20] --compact

# (Also works: gmail list --query "in:drafts" auto-routes to list-drafts)

# Inspect a specific draft
$CLI gmail get-draft DRAFT_ID --compact

# Attach a file to an existing draft (preserves body, subject, recipients, existing attachments)
$CLI gmail attach-to-draft DRAFT_ID --attach /path/to/report.pdf

# Update any field of an existing draft — omitted fields are preserved
$CLI gmail update-draft DRAFT_ID --subject "New subject"
$CLI gmail update-draft DRAFT_ID --body-file - <<'BODY'
Replacement body.
BODY
$CLI gmail update-draft DRAFT_ID --attach /path/to/file.pdf                   # replaces existing attachments
$CLI gmail update-draft DRAFT_ID --attach /path/to/file.pdf --append-attachments  # keeps existing + adds new

# Send or discard
$CLI gmail send-draft DRAFT_ID [--account work]
$CLI gmail delete-draft DRAFT_ID   # permanent, no trash
```

Response shapes:
- `list-drafts`: `{"drafts":[{"draftId","id","threadId","from","to","subject","date","snippet","labelIds"}],"count","query"}`
- `update-draft`: `{"updated":true,"id":"DRAFT_ID","messageId":"MSG_ID"}`
- `attach-to-draft`: `{"updated":true,"id":"DRAFT_ID","messageId":"MSG_ID","attached":N}`
- `delete-draft`: `{"deleted":true,"id":"DRAFT_ID"}`

**Canonical attach-to-existing-draft workflow:**

```bash
# Find the draft you're editing in Gmail, then attach a PDF from the terminal
DID=$($CLI gmail list-drafts --query "subject:\"Kill the Bench\"" --limit 1 --compact \
  | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['drafts'][0]['draftId'])")
$CLI gmail attach-to-draft "$DID" --attach ~/Downloads/report.pdf
```

### Manage Messages

```bash
$CLI gmail trash MESSAGE_ID
$CLI gmail download-attachments MESSAGE_ID --dest /path/to/dir
```

## UI Verification

After `draft`, `draft-reply`, `draft-reply-all`, `update-draft`, or `attach-to-draft`, verify the draft looks correct in Gmail using Chrome DevTools MCP:

1. Open Gmail drafts:
   ```
   mcp__chrome-devtools__navigate_page → https://mail.google.com/mail/u/0/#drafts
   ```
2. Click into the draft and take a screenshot. Confirm: recipients, subject, body content, and attachments are as expected.
3. If anything looks wrong, fix it with `gmail update-draft` before reporting done.

Do NOT verify sent messages — those are already out the door.

## Safety Rules

1. **Never auto-send first time.** Draft first, show the user for review.
2. **Outreach = `--account work`** always. Never cold email from `main`.
3. **Max 50 emails/hour** via `--account work`.
4. **Bulk sends:** draft first 3-5, wait for "approved", then send rest.
5. **Never delete emails.** Only trash.

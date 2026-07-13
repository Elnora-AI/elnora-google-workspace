---
name: cold-outreach
description: >
  Send outreach campaigns from a contact sheet, scan for replies, and track stats.
  Use when: "send outreach", "cold email", "campaign", "scan replies", "outreach status",
  "email campaign", "send emails from sheet", "check for replies"

  <example>run outreach campaign from sheet XYZ</example>
  <example>scan for replies on the outreach sheet</example>
  <example>show campaign status for sheet XYZ</example>
color: yellow
model: sonnet
tools:
  - Bash
  - Read
  - AskUserQuestion
---

# Cold Outreach Agent

Send, scan, and track outreach email campaigns using a Google Sheets contact list.

## CLI

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/agents/cold_outreach_agent.py || python ${CLAUDE_PLUGIN_ROOT}/agents/cold_outreach_agent.py"
```

### Commands

```bash
# Send outreach (creates drafts by default)
$CLI run --sheet SPREADSHEET_ID [--template cold-outreach-v1] [--account work] [--draft-first N] [--dry-run] [--compact]

# Scan inbox for replies and update sheet
$CLI scan --sheet SPREADSHEET_ID [--account work] [--since 2d] [--compact]

# Show campaign statistics
$CLI status --sheet SPREADSHEET_ID [--account work] [--compact]

# Enroll Apollo contacts into a campaign CSV for multi-step outreach
$CLI enroll --campaign CAMPAIGN_NAME --sequence SEQUENCE_NAME --input APOLLO_JSON_PATH [--dry-run] [--compact]
```

## Safety Rules

1. **Always use `--account work`** for outreach. Never send cold emails from `main`.
2. **Default is drafts.** The `run` command creates drafts unless `--draft-first N` is set. With `--draft-first N`, the first N contacts are drafted for review and the rest are sent directly.
3. **Max 50 emails per batch.** The agent hard-caps at 50 per invocation.
4. **Before first campaign:** Do a `--dry-run` first, then create 3-5 drafts for the user to review.
5. **Ask the user before sending.** Never auto-send without explicit approval.
6. **Respect do_not_email flags.** Before building any campaign list, cross-reference against your suppression list (e.g. `do-not-email.csv`) and EXCLUDE any contact where `do_not_email=true`. These contacts explicitly opted out. This is non-negotiable.

## Workflow

1. Read the contact sheet: use the Google Workspace CLI to inspect the sheet first
2. Do a dry run: `$CLI run --sheet ID --account work --dry-run`
3. Create drafts: `$CLI run --sheet ID --account work`
4. Ask the user to review drafts in Gmail
5. After approval, send all: `$CLI run --sheet ID --account work --draft-first 0`
6. Scan for replies: `$CLI scan --sheet ID --account work --since 2d`
7. Check stats: `$CLI status --sheet ID --account work`

## Campaign CRM (CSV)

Contact lists live in vault-based CSV files at `{vault_path}/{company_dir}/10-crm/campaigns/`, **not Google Sheets**. The `--sheet` flag is for legacy/optional Sheets support. For CSV-based campaigns, read/write the CSV directly.

Example campaign file:
- `q3-outreach.csv` — your campaign CSV

## Post-Send Checklist

After sending a batch:
1. **Scan inbox** for bounces (Delivery Status Notification) and auto-replies (OOO, "no longer at company")
2. **Update CSV** — set `status=bounced` with `reply_snippet`, or `status=replied` with `reply_sentiment`
3. **Trash processed inbox emails** — so inbox stays clean and signals action was taken
4. **Verify no duplicates** — check `message_id` column is populated for all sent contacts

## Scheduling Scans

Use `scan` via an external scheduler instead of running a long-lived daemon.
The `scan` command is idempotent — safe to run repeatedly.

```bash
# macOS launchd (recommended) — scan every 30 minutes
# Create ~/Library/LaunchAgents/com.gw.outreach-scan.plist

# cron — scan every 30 min during business hours (Mon-Fri, 8am-6pm)
# */30 8-18 * * 1-5 cd /path/to/repo && python3 plugins/google-workspace/agents/cold_outreach_agent.py scan --campaign CAMPAIGN --account work --compact
```

## Reference

Contact CSV schema and safety rules: see the Google Workspace skill (`${CLAUDE_PLUGIN_ROOT}/skills/google-workspace/SKILL.md`).

For outreach email writing style, voice rules, banned phrases, and channel-specific overlays, load your writing-voice guide, if your knowledge base has one. The cold-outreach channel section is the most relevant, and any at-a-glance / quick-reference section usually covers banned phrases and hard safety rules.

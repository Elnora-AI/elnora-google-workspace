---
name: prep-meeting
description: Pre-meeting brief — looks up attendees in CRM, reads recent transcripts, and Slack-DMs you 3-5 main points before a meeting. Not a full page — just what you need to walk in informed.
argument-hint: <event-id|event-title-substring> [--no-slack]
allowed-tools: Bash
---

# Pre-meeting brief: $ARGUMENTS

## Task

Generate a pre-meeting brief for a Google Calendar event and deliver it to you over Slack (or stdout). The brief is intentionally short (3-5 bullets) — it's what you read on your phone in the 5 minutes before walking into the meeting, not a full briefing document.

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/cli/prep_meeting.py {{ARGUMENTS}}
```

The CLI accepts either an event ID or a title substring (it'll search the next 3 days of calendar events).

## What it includes

- **Who** — name, role, company (from contacts.csv)
- **Last touch** — last_contact_date + channel (so you know if you've spoken recently)
- **Open next_action** — anything overdue/owed (from contacts.csv)
- **Customer file pointer** — for paying customers, points to the CRM's company/account detail file
- **Recent transcripts** — last 1–2 transcripts with this person, with a one-line summary of what was discussed

## What it deliberately doesn't include

- Full meeting agenda or talking points
- Exhaustive history (use the customer file or transcript directly for that)
- Any commercial/pricing/scope discussion (those are judgment calls you make live)

## Examples

**By event id (from `gw calendar list`):**
```
/prep-meeting abc123def456ghi789jklm
```

**By title substring (next 3 days):**
```
/prep-meeting Acme
/prep-meeting <attendee-name>
```

**Print only, don't DM Slack:**
```
/prep-meeting Acme --no-slack
```

## Notes

- The brief is DM'd to the Slack user in `$GW_SLACK_USER_ID` via the Slack CLI at `$GW_SLACK_CLI_BIN`. If either is unset, the brief is printed to stdout instead.
- Requires the optional knowledge base for CRM/transcript lookups. If no knowledge base is configured, the command no-ops with a message. Transcript scanning uses the directories in `GW_TRANSCRIPT_DIRS` (comma-separated, relative to the company dir).
- If the attendees aren't in `contacts.csv`, the brief falls back to listing their email addresses (so you know who to research).
- Best run 15-30 minutes before the meeting. Could be wired into a scheduled job that scans upcoming meetings hourly and DMs briefs 30 min before, but right now it's manual.

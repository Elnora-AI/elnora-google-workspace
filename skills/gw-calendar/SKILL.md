---
name: gw-calendar
description: >
  Create, update, and list Google Calendar events via CLI. Handles Google Meet links,
  attendees, timezones, reminders, and locations. Also reads and edits booking pages
  (appointment schedules) by driving Chrome, since Google ships no API for them.
  TRIGGERS: "calendar", "meeting", "schedule", "event", "google meet", "book a call",
  "availability", "schedule meeting", "create event", "update event", "reschedule",
  "change event", "modify event", "reminder", "booking page", "booking pages",
  "appointment schedule", "bookable hours", "booking link"
---

# Calendar

## Account

Events are created on the default account unless you pass `--account <name>`. The default account is configurable — run `gw auth list` to see the accounts you have set up, and pass `--account <name>` to target a specific one.

## Required Fields — always populate

Every `create` and `update` MUST include ALL of these fields. Never create a partial event.

| Field | Flag | Default |
|-------|------|---------|
| Title | `--title` | — (required) |
| Start time | `--start` | — (required) |
| Duration | `--duration` | 30 min |
| Description | `--description` | — |
| Location | `--location` | — |
| Attendees | `--attendees` | — |
| Reminders | `--reminders` | 60,30,10 (auto) |
| Meet link | `--meet` | only when virtual |

**If the request is missing any of these details:**

1. **Search email first.** Use the gmail skill to find the relevant thread:
   ```bash
   $CLI gmail list --query "subject:KEYWORD" --limit 5 --compact
   $CLI gmail get MESSAGE_ID --compact
   ```
   Extract: location/address, attendees, agenda/description, time details.
2. **If email doesn't have it, ask the user.** Do not guess or leave fields blank.

Examples of what to look for:
- "Let's meet at the office" → search email for the address, use as `--location`.
- "Call with Alex" → search email for Alex's email, full name for title, any agenda for description.
- "Coffee with investor" → search email for venue name + address, attendee email.
- In-person → `--location` with full address. Virtual → `--meet` flag. Hybrid → both.
- Workspace may auto-attach a Meet link on any event write (account "Automatically add Google Meet" setting), so an in-person event can sprout one after an unrelated `update`. That's not a bug. Pass `--meet false` to strip it (the CLI sends `conferenceDataVersion=1`, required for removal to take effect).

## Defaults (auto-applied)

- **Reminders**: 60, 30, 10 min popup — set automatically on `create`. Do NOT change unless the user explicitly requests different reminders. Override: `--reminders "120,15"`.
- **Duration**: 30 minutes.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

```bash
$CLI calendar create --title "Demo call" --start "2026-03-01T14:00" [--duration 30] [--meet] [--attendees "a@co.com,b@co.com"] [--description "..."] [--location "123 Main St, City"] [--timezone "America/Denver"] [--reminders "60,30,10"] [--all-day --end "2026-03-03"] [--busy|--free]
$CLI calendar update --event-id "EVENT_ID" [--title "New title"] [--start "2026-03-01T15:00"] [--duration 60] [--meet true|false] [--attendees "a@co.com"] [--description "..."] [--location "..."] [--timezone "America/Denver"] [--reminders "60,30,10"] [--busy|--free]
$CLI calendar list [--days 7] [--calendar all|CAL_ID] --compact
$CLI calendar calendars --compact
$CLI calendar get --event-id "EVENT_ID" [--calendar CAL_ID] --compact
```

`list`/`get` default to the `primary` calendar. Pass `--calendar all` to `list` to merge every accessible calendar (sorted by start), or `--calendar CAL_ID` to target a secondary calendar. Get a calendar's ID from `calendar calendars`. An event from a secondary calendar must be read with the same `--calendar CAL_ID` it was listed under.

## Response Shapes (validated)

`list`: `{"events":[{"id","calendar","title","start","end","location","attendees","meetLink","link"}],"count":7,"days":3,"calendars":["primary"]}`

`calendars`: `{"calendars":[{"id","summary","description","primary","accessRole","selected","timeZone"}],"count":3}` — primary sorts first.

`get`: `{"id","calendar","title","description","start","end","location","busy","status","organizer","attendees":[{"email","displayName","responseStatus","optional","organizer","self"}],"attendeeCount","meetLink","link"}` — use this to read attendee RSVP status (`accepted`/`declined`/`tentative`/`needsAction`), which `list` flattens to bare emails. `busy` is `true` when the event blocks your free/busy time (opaque), `false` when it shows as free (transparent).

`create`: `{"created":true,"id","title","start","end","location","busy","meetLink","link"}`

`update`: `{"updated":true,"id","title","start","end","location","busy","meetLink","link"}`

## Examples

```bash
# 30-min meeting with Meet link
$CLI calendar create --title "Intro call with Acme" --start "2026-04-07T10:00" --duration 30 --meet --attendees "alex@acme.com"

# Change duration to 60 minutes
$CLI calendar update --event-id "abc123" --duration 60

# Reschedule and rename
$CLI calendar update --event-id "abc123" --title "Extended demo" --start "2026-04-07T11:00" --duration 60

# Multi-day all-day trip that blocks you as busy (--end is the last full day, inclusive)
$CLI calendar create --title "Tentative: offsite" --start "2026-07-02" --end "2026-07-03" --all-day --busy --location "Berlin, Germany"

# Flip an existing event to show as free (does not block free/busy)
$CLI calendar update --event-id "abc123" --free

# List next 3 days
$CLI calendar list --days 3 --compact
```

## Booking Pages (Appointment Schedules)

Google exposes no API for these — Calendar API v3 has no appointment-schedule
resource, and booking pages are neither events nor calendars, so OAuth cannot
see them at all. These commands drive the Calendar UI in the user's own running
Chrome instead, so no Google session credentials are ever stored.

**Requires** Chrome running with the `chrome://inspect/#remote-debugging` toggle
enabled (a one-time setting, the same connection the chrome-devtools MCP uses).
The commands reuse an open Calendar tab, navigating it as they work.

```bash
$CLI calendar booking list [--user-index 0]
$CLI calendar booking get --name "Intro call"
$CLI calendar booking update --name "Intro call" [--duration 30] [--hours "mon=9:00am-5:00pm,fri=unavailable"] [--timezone "Salt Lake City"] [--rename "New name"] [--dry-run]
$CLI calendar booking delete --name "Old page" --confirm
```

Only one client may hold Chrome's browser endpoint, so these commands cannot run
while another tool (the chrome-devtools MCP, say) is driving the same browser.
Connections are retried with backoff, which also covers Chrome briefly refusing
a reconnect right after a previous command finished.

`--name` must match the sidebar entry exactly; run `booking list` to get it.
`--user-index` is the `N` in `calendar.google.com/calendar/u/N` — Chrome's
signed-in account slot, which is not the same thing as `--account`.

**`--hours` format** — comma-separated `day=value`. A value is either
`unavailable` or one or more `start-end` ranges joined by `+`. Times accept
24-hour (`17:00`) or 12-hour (`5pm`, `5:00 PM`) form. **Days you leave out keep
their current hours**, so you can change one day without restating the week.

```bash
# One day, leaving every other day alone
$CLI calendar booking update --name "Intro call" --hours "mon=6:00pm-10:00pm"

# Split day (morning and afternoon), and clear a day
$CLI calendar booking update --name "30 min with Jane Doe" --hours "wed=9-12+14-17,fri=unavailable"

# Move a page to a new city's timezone and rename it in the same pass
$CLI calendar booking update --name "Intro call (US)" --rename "Intro call" --timezone "Salt Lake City"

# Preview without saving — always do this first on a page you have not edited before
$CLI calendar booking update --name "Intro call" --duration 45 --dry-run
```

`--duration` accepts only what the UI offers: 15, 30, 45, 60, 90 or 120 minutes.

**`--timezone` searches cities, not zone names** — that is how the Calendar field
itself works. Pass `"Salt Lake City"`, not `"America/Denver"` or `"MST"`; it
resolves to `(GMT-06:00) Mountain Time - Denver`. The command fails rather than
guessing if no option matches the text, and reports the city it matched.

**`--confirm` is required on `delete`.** Deleting takes the page's public booking
link down immediately, so anyone holding that link loses it. Appointments already
booked through the page are *not* cancelled — Google keeps them on the calendar.

`get` returns `{"name","duration","recurrence","timezone","availability":[{"day","periods":[{"start","end"}]}]}`,
with `periods: []` meaning unavailable. `update` returns the same shape plus
`"saved": true`, read back from a fresh reopen of the page — it does not report
the form it just filled in. If the saved state does not match the request the
command fails rather than reporting success.

Because this is UI automation, it can break if Google restructures the
appointment-schedule editor. A `GW_CHROME_ERROR` naming a missing button or a
timeout means exactly that; check the page in Chrome.

## UI Verification

After `create` or `update`, verify the event looks correct in Google Calendar using Chrome DevTools MCP:

1. Open the event link returned in the `link` field:
   ```
   mcp__chrome-devtools__navigate_page → result.link
   ```
2. Take a screenshot and confirm: title, time, duration, location, attendees, and Meet link (if requested) all match.
3. If anything looks wrong, fix it with `calendar update` before reporting done.

## Notes

- `--duration` defaults to 30 minutes.
- `--timezone` overrides local detection. Use IANA names (e.g. `America/Denver`).
- On Windows, set `TZ` env var — `ZoneInfo("localtime")` is unsupported.

"""Google Workspace CLI — Calendar commands."""

from __future__ import annotations

import sys
from datetime import datetime, timezone as tz

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Calendar commands on the CLI group."""

    @cli_group.group()
    def calendar():
        """Google Calendar operations — create events, list upcoming, sync to CRM."""
        pass

    @calendar.command()
    @click.option("--title", required=True, help="Event title")
    @click.option("--start", required=True, help="Start time ISO format (e.g., 2026-03-01T14:00) or date (YYYY-MM-DD) for all-day")
    @click.option("--duration", default=30, type=int, help="Duration in minutes (default: 30)")
    @click.option("--meet", is_flag=True, help="Attach Google Meet link")
    @click.option("--attendees", default=None, help="Comma-separated attendee emails")
    @click.option("--description", default=None, help="Event description")
    @click.option("--location", default=None, help="Event location (address or place name)")
    @click.option("--timezone", default=None, help="IANA timezone (e.g., America/Denver). Overrides local timezone detection.")
    @click.option("--reminders", default=None, help="Comma-separated reminder minutes (default: 60,30,10)")
    @click.option("--all-day", is_flag=True, help="Create an all-day event (--start is a date, --duration is ignored)")
    @click.option("--end", default=None, help="All-day only: last full day (inclusive), YYYY-MM-DD, for multi-day events")
    @click.option("--busy/--free", "busy", default=True, help="Show time as busy (opaque) or free (transparent). Default: busy")
    @account_option
    @compact_option
    def create(title, start, duration, meet, attendees, description, location, timezone, reminders, all_day, end, busy, account, compact):
        """Create a calendar event (optionally with Google Meet)."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.create(
                title=title, start=start, duration=duration,
                meet=meet, attendees=attendees, description=description,
                location=location, timezone_name=timezone,
                reminders=reminders, account=account, all_day=all_day,
                end=end, busy=busy,
            )
            output_success(result, compact=compact)

    @calendar.command()
    @click.option("--event-id", required=True, help="Event ID to update (from 'calendar list')")
    @click.option("--title", default=None, help="New event title")
    @click.option("--start", default=None, help="New start time ISO format")
    @click.option("--duration", default=None, type=int, help="New duration in minutes")
    @click.option("--meet", default=None, type=bool, help="Add/remove Google Meet link (true/false)")
    @click.option("--attendees", default=None, help="Comma-separated attendee emails (replaces existing)")
    @click.option("--description", default=None, help="New event description")
    @click.option("--location", default=None, help="New event location")
    @click.option("--timezone", default=None, help="IANA timezone override")
    @click.option("--reminders", default=None, help="Override reminders: comma-separated minutes (e.g. 60,30,10)")
    @click.option("--busy/--free", "busy", default=None, help="Show time as busy (opaque) or free (transparent)")
    @account_option
    @compact_option
    def update(event_id, title, start, duration, meet, attendees, description, location, timezone, reminders, busy, account, compact):
        """Update an existing calendar event."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.update(
                event_id=event_id, title=title, start=start, duration=duration,
                meet=meet, attendees=attendees, description=description,
                location=location, timezone_name=timezone,
                reminders=reminders, account=account, busy=busy,
            )
            output_success(result, compact=compact)

    @calendar.command()
    @click.option("--event-id", required=True, help="Event ID to read (from 'calendar list')")
    @click.option("--calendar", "calendar_id", default="primary", help="Calendar ID the event lives on (from 'calendar calendars'; default: primary)")
    @account_option
    @compact_option
    def get(event_id, calendar_id, account, compact):
        """Read a single event with full attendee details (including RSVP status)."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.get_event(event_id=event_id, calendar_id=calendar_id, account=account)
            output_success(result, compact=compact)

    @calendar.command()
    @click.option("--event-id", required=True, help="Event ID to delete (from 'calendar list')")
    @click.option("--calendar", "calendar_id", default="primary", help="Calendar ID the event lives on (from 'calendar calendars'; default: primary)")
    @click.option("--notify/--no-notify", default=False, help="Send cancellation notices to attendees (default: no-notify)")
    @account_option
    @compact_option
    def delete(event_id, calendar_id, notify, account, compact):
        """Delete a single event. Use --notify to email attendees a cancellation."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.delete_event(event_id=event_id, calendar_id=calendar_id, notify=notify, account=account)
            output_success(result, compact=compact)

    @calendar.command(name="calendars")
    @account_option
    @compact_option
    def calendar_calendars(account, compact):
        """List every calendar the account can access (id, name, access role)."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.list_calendars(account=account)
            output_success(result, compact=compact)

    @calendar.command(name="list")
    @click.option("--days", default=7, type=int, help="Look-ahead days (default: 7)")
    @click.option("--calendar", "calendar_id", default="primary", help="Calendar ID, or 'all' to merge every accessible calendar (default: primary)")
    @account_option
    @compact_option
    def calendar_list(days, calendar_id, account, compact):
        """List upcoming events (use --calendar all to span every calendar)."""
        import calendar_ops
        with _handle_errors(compact):
            result = calendar_ops.list_events(days=days, calendar_id=calendar_id, account=account)
            output_success(result, compact=compact)

    # ------------------------------------------------------------------
    # sync-crm command
    # ------------------------------------------------------------------

    @calendar.command(name="sync-crm")
    @click.option("--lookback-days", default=2, type=int, help="Days to look back (default: 2)")
    @click.option("--limit", default=0, type=int, help="Max events to process (0=unlimited)")
    @click.option("--no-enrich", is_flag=True, help="Skip Exa enrichment")
    @click.option("--dry-run", is_flag=True, help="Show what would change without writing")
    @compact_option
    def sync_crm(lookback_days, limit, no_enrich, dry_run, compact):
        """Sync calendar attendees to CRM — auto-add new contacts and enrich."""
        import gw_config
        if not gw_config.kb_configured():
            click.echo(click.style(gw_config.KB_NOT_CONFIGURED, fg="yellow"))
            return

        import calendar_crm_sync as sync

        if dry_run:
            click.echo(click.style("DRY RUN — no files will be written\n", fg="yellow"))

        # Load state
        state = sync.load_state()
        processed_ids = set(state.get("processed_event_ids", []))

        # Fetch only appointment-schedule bookings
        click.echo(f"Fetching appointment bookings from last {lookback_days} day(s)...", nl=False)
        sys.stdout.flush()
        events = sync.fetch_booking_events(lookback_days)
        click.echo(f" found {len(events)}")

        # Filter already-processed events
        new_events = [e for e in events if e.get("id") not in processed_ids]
        if not new_events:
            click.echo(click.style("No new events to process.", fg="cyan"))
            return

        # Apply limit
        if limit > 0 and len(new_events) > limit:
            click.echo(click.style(
                f"Limiting to {limit} of {len(new_events)} new events.", fg="cyan"
            ))
            new_events = new_events[:limit]

        click.echo(f"Processing {len(new_events)} new event(s)...\n")

        # Build CRM lookups once
        contacts_lookup = sync._contacts_by_email()
        companies_lookup = sync._companies_by_domain()

        total_new = 0
        total_updated = 0
        total_new_cos = 0
        errors: list[tuple[str, str]] = []

        for i, event in enumerate(new_events, 1):
            eid = event.get("id", "?")[:16]
            title = event.get("title", "(no title)")
            click.echo(f"[{i}/{len(new_events)}] {title}...", nl=False)
            sys.stdout.flush()

            try:
                result = sync.sync_one_event(
                    event=event,
                    contacts_lookup=contacts_lookup,
                    companies_lookup=companies_lookup,
                    enrich=not no_enrich,
                    dry_run=dry_run,
                )

                n_new = len(result["new_contacts"])
                n_upd = len(result["updated_contacts"])
                n_cos = len(result["new_companies"])
                total_new += n_new
                total_updated += n_upd
                total_new_cos += n_cos

                parts = []
                if n_new:
                    parts.append(click.style(f"+{n_new} new", fg="green"))
                if n_upd:
                    parts.append(click.style(f"~{n_upd} updated", fg="cyan"))
                if n_cos:
                    parts.append(click.style(f"+{n_cos} companies", fg="blue"))
                if not parts:
                    parts.append(click.style("no external attendees", fg="yellow"))

                click.echo(f" {', '.join(parts)}")

                # Print details
                for name in result["new_contacts"]:
                    click.echo(f"    + {name}")
                for name in result["updated_contacts"]:
                    click.echo(f"    ~ {name}")
                for name in result["new_companies"]:
                    click.echo(f"    + company: {name}")

                # Save state after EACH event (crash-safe)
                if not dry_run:
                    processed_ids.add(event.get("id", ""))
                    state["processed_event_ids"] = sorted(processed_ids)
                    state["last_sync"] = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    state["total_synced"] = len(state["processed_event_ids"])
                    sync.save_state(state)

            except Exception as exc:
                errors.append((eid, str(exc)))
                click.echo(click.style(f" ERROR: {exc}", fg="red"))

            sys.stdout.flush()

        # Summary
        label = "Would process" if dry_run else "Processed"
        click.echo(f"\n{label} {len(new_events)} event(s):")
        click.echo(f"  New contacts:     {total_new}")
        click.echo(f"  Updated contacts: {total_updated}")
        click.echo(f"  New companies:    {total_new_cos}")

        if errors:
            click.echo(click.style(f"\n{len(errors)} error(s) occurred.", fg="red"))

    # ------------------------------------------------------------------
    # sync-crm status command
    # ------------------------------------------------------------------

    @calendar.command(name="sync-crm-status")
    @compact_option
    def sync_crm_status(compact):
        """Show calendar-to-CRM sync statistics."""
        import gw_config
        if not gw_config.kb_configured():
            click.echo(click.style(gw_config.KB_NOT_CONFIGURED, fg="yellow"))
            return
        import calendar_crm_sync as sync

        state = sync.load_state()
        total = state.get("total_synced", 0)
        last = state.get("last_sync") or "never"
        processed = len(state.get("processed_event_ids", []))

        click.echo(click.style("Calendar CRM Sync Status", fg="cyan", bold=True))
        click.echo(f"  Processed events: {processed}")
        click.echo(f"  Total synced:     {total}")
        click.echo(f"  Last sync:        {last}")

    # ------------------------------------------------------------------
    # sync-crm install/uninstall
    # ------------------------------------------------------------------

    @calendar.command(name="sync-crm-install")
    @click.option("--interval-hours", default=2, type=int, help="Hours between runs (default: 2)")
    def sync_crm_install(interval_hours):
        """Schedule the calendar→CRM sync to run periodically.

        Registered on the host's native scheduler automatically: launchd (macOS),
        Task Scheduler (Windows), or the user crontab (Linux). Falls back to
        printing the exact command if the scheduler can't be driven.
        """
        import scheduler
        scheduler.install("calendar", interval_hours)

    @calendar.command(name="sync-crm-uninstall")
    def sync_crm_uninstall():
        """Remove the scheduled calendar→CRM sync job from the native scheduler."""
        import scheduler
        scheduler.uninstall("calendar")

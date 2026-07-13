"""Google Calendar operations — create events (with Meet), list upcoming."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from auth import build_service
from googleapiclient.errors import HttpError
from output import ValidationError, handle_http_error, validate_email


DEFAULT_REMINDERS = [60, 30, 10]


def _build_reminders(reminders: str | None) -> dict:
    """Build the reminders object. None = default (60,30,10). Explicit = parse CSV of minutes."""
    if reminders is not None:
        try:
            minutes_list = [int(m.strip()) for m in reminders.split(",") if m.strip()]
        except ValueError:
            raise ValidationError(
                f"Invalid --reminders: '{reminders}'. Must be comma-separated minutes.",
                suggestion="Example: --reminders 60,30,10",
            )
        for m in minutes_list:
            if m <= 0:
                raise ValidationError(
                    f"Reminder minutes must be positive, got: {m}",
                    suggestion="Use positive numbers, e.g. --reminders 60,30,10",
                )
    else:
        minutes_list = DEFAULT_REMINDERS

    return {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": m} for m in minutes_list],
    }


def _get_service(account: str | None = None):
    return build_service("calendar", "v3", account)


def create(
    title: str,
    start: str,
    duration: int = 30,
    meet: bool = False,
    attendees: str | None = None,
    description: str | None = None,
    location: str | None = None,
    timezone_name: str | None = None,
    reminders: str | None = None,
    account: str | None = None,
    all_day: bool = False,
    end: str | None = None,
    busy: bool = True,
) -> dict:
    """Create a calendar event. Optionally attach a Google Meet link."""
    if all_day:
        try:
            start_date = datetime.fromisoformat(start).date() if "T" in start else datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                f"Invalid --start: '{start}'. Must be a date (YYYY-MM-DD) for all-day events.",
                suggestion="Example: --start 2026-06-07",
            )
        if end:
            try:
                last_day = datetime.fromisoformat(end).date() if "T" in end else datetime.strptime(end, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError(
                    f"Invalid --end: '{end}'. Must be a date (YYYY-MM-DD) for all-day events.",
                    suggestion="Example: --end 2026-06-08 (the last full day, inclusive)",
                )
            if last_day < start_date:
                raise ValidationError(
                    f"--end ({end}) is before --start ({start}).",
                    suggestion="--end must be on or after --start.",
                )
            end_date = last_day + timedelta(days=1)  # Google all-day end is exclusive
        else:
            end_date = start_date + timedelta(days=1)
        event: dict = {
            "summary": title,
            "start": {"date": start_date.isoformat()},
            "end": {"date": end_date.isoformat()},
        }
    else:
        try:
            start_dt = datetime.fromisoformat(start)
        except ValueError:
            raise ValidationError(
                f"Invalid --start: '{start}'. Must be ISO format.",
                suggestion="Example: --start 2026-03-01T14:00",
            )

        if start_dt.tzinfo is None:
            if timezone_name:
                try:
                    tz = ZoneInfo(timezone_name)
                except (KeyError, Exception):
                    raise ValidationError(
                        f"Invalid --timezone: '{timezone_name}'.",
                        suggestion="Use an IANA timezone, e.g. America/Denver, America/New_York, UTC",
                    )
            else:
                try:
                    tz = ZoneInfo("localtime")
                except (KeyError, Exception):
                    tz = timezone.utc
            start_dt = start_dt.replace(tzinfo=tz)

        if duration <= 0:
            raise ValidationError(
                f"Duration must be positive, got: {duration}",
                suggestion="Use a positive number of minutes, e.g. --duration 30",
            )

        end_dt = start_dt + timedelta(minutes=duration)

        tz_name = str(start_dt.tzinfo) if hasattr(start_dt.tzinfo, 'key') else "UTC"
        if hasattr(start_dt.tzinfo, 'key'):
            tz_name = start_dt.tzinfo.key

        event: dict = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        }

    event["transparency"] = "opaque" if busy else "transparent"

    if description:
        event["description"] = description

    if location:
        event["location"] = location

    if meet:
        event["conferenceData"] = {
            "createRequest": {
                "requestId": f"gw-{start_dt.timestamp():.0f}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    if attendees:
        attendee_list = [e.strip() for e in attendees.split(",") if e.strip()]
        for addr in attendee_list:
            validate_email(addr, field="attendees")
        event["attendees"] = [{"email": addr} for addr in attendee_list]

    event["reminders"] = _build_reminders(reminders)

    service = _get_service(account)
    try:
        result = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1 if meet else 0,
            sendUpdates="all" if attendees else "none",
        ).execute()
    except HttpError as e:
        handle_http_error(e, "calendar create")
        raise  # unreachable

    output = {
        "created": True,
        "id": result["id"],
        "title": result.get("summary", title),
        "start": result["start"].get("dateTime", result["start"].get("date", "")),
        "end": result["end"].get("dateTime", result["end"].get("date", "")),
        "location": result.get("location", ""),
        "busy": result.get("transparency", "opaque") != "transparent",
        "link": result.get("htmlLink", ""),
    }
    if meet:
        entry_points = result.get("conferenceData", {}).get("entryPoints", [])
        meet_link = next((ep["uri"] for ep in entry_points if ep["entryPointType"] == "video"), None)
        output["meetLink"] = meet_link

    return output


def update(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    duration: int | None = None,
    meet: bool | None = None,
    attendees: str | None = None,
    description: str | None = None,
    location: str | None = None,
    timezone_name: str | None = None,
    reminders: str | None = None,
    account: str | None = None,
    busy: bool | None = None,
) -> dict:
    """Update an existing calendar event. Only provided fields are changed."""
    service = _get_service(account)

    try:
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        handle_http_error(e, "calendar get")
        raise

    if title is not None:
        existing["summary"] = title

    if description is not None:
        existing["description"] = description

    if location is not None:
        existing["location"] = location

    if start is not None or duration is not None:
        if start is not None:
            try:
                start_dt = datetime.fromisoformat(start)
            except ValueError:
                raise ValidationError(
                    f"Invalid --start: '{start}'. Must be ISO format.",
                    suggestion="Example: --start 2026-03-01T14:00",
                )
        else:
            start_dt = datetime.fromisoformat(existing["start"]["dateTime"])

        if start_dt.tzinfo is None:
            if timezone_name:
                try:
                    tz = ZoneInfo(timezone_name)
                except (KeyError, Exception):
                    raise ValidationError(
                        f"Invalid --timezone: '{timezone_name}'.",
                        suggestion="Use an IANA timezone, e.g. America/Denver",
                    )
            else:
                tz_from_existing = existing.get("start", {}).get("timeZone")
                if tz_from_existing:
                    try:
                        tz = ZoneInfo(tz_from_existing)
                    except (KeyError, Exception):
                        tz = timezone.utc
                else:
                    try:
                        tz = ZoneInfo("localtime")
                    except (KeyError, Exception):
                        tz = timezone.utc
            start_dt = start_dt.replace(tzinfo=tz)

        dur = duration if duration is not None else None
        if dur is None:
            old_start = datetime.fromisoformat(existing["start"]["dateTime"])
            old_end = datetime.fromisoformat(existing["end"]["dateTime"])
            dur = int((old_end - old_start).total_seconds() / 60)

        if dur <= 0:
            raise ValidationError(
                f"Duration must be positive, got: {dur}",
                suggestion="Use a positive number of minutes, e.g. --duration 60",
            )

        end_dt = start_dt + timedelta(minutes=dur)
        tz_name = str(start_dt.tzinfo)
        if hasattr(start_dt.tzinfo, 'key'):
            tz_name = start_dt.tzinfo.key

        existing["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_name}
        existing["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_name}

    conf_version = 0
    if meet is True and "conferenceData" not in existing:
        ts = datetime.fromisoformat(existing["start"]["dateTime"]).timestamp()
        existing["conferenceData"] = {
            "createRequest": {
                "requestId": f"gw-{ts:.0f}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        conf_version = 1
    elif meet is False:
        existing.pop("conferenceData", None)
        # Removing conferenceData is ignored by the API unless version is 1.
        conf_version = 1

    if attendees is not None:
        attendee_list = [e.strip() for e in attendees.split(",") if e.strip()]
        for addr in attendee_list:
            validate_email(addr, field="attendees")
        existing["attendees"] = [{"email": addr} for addr in attendee_list]

    if reminders is not None:
        existing["reminders"] = _build_reminders(reminders)

    if busy is not None:
        existing["transparency"] = "opaque" if busy else "transparent"

    has_attendees = bool(existing.get("attendees"))

    try:
        result = service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body=existing,
            conferenceDataVersion=conf_version,
            sendUpdates="all" if has_attendees else "none",
        ).execute()
    except HttpError as e:
        handle_http_error(e, "calendar update")
        raise

    output = {
        "updated": True,
        "id": result["id"],
        "title": result.get("summary", ""),
        "start": result["start"].get("dateTime", result["start"].get("date", "")),
        "end": result["end"].get("dateTime", result["end"].get("date", "")),
        "location": result.get("location", ""),
        "busy": result.get("transparency", "opaque") != "transparent",
        "link": result.get("htmlLink", ""),
    }
    entry_points = result.get("conferenceData", {}).get("entryPoints", [])
    meet_link = next((ep["uri"] for ep in entry_points if ep["entryPointType"] == "video"), None)
    if meet_link:
        output["meetLink"] = meet_link

    return output


def list_calendars(account: str | None = None) -> dict:
    """List every calendar the account can access (from the calendar list)."""
    service = _get_service(account)
    try:
        result = service.calendarList().list(maxResults=250, showHidden=True).execute()
    except HttpError as e:
        handle_http_error(e, "calendar calendars")
        raise  # unreachable

    calendars = [
        {
            "id": cal["id"],
            "summary": cal.get("summaryOverride", cal.get("summary", "")),
            "description": cal.get("description", ""),
            "primary": cal.get("primary", False),
            "accessRole": cal.get("accessRole", ""),
            "selected": cal.get("selected", False),
            "timeZone": cal.get("timeZone", ""),
        }
        for cal in result.get("items", [])
    ]
    calendars.sort(key=lambda c: (not c["primary"], c["summary"].lower()))
    return {"calendars": calendars, "count": len(calendars)}


def _all_calendar_ids(service) -> list[str]:
    """Return every calendar id the account can read, primary first."""
    result = service.calendarList().list(maxResults=250, showHidden=True).execute()
    ids = []
    for cal in result.get("items", []):
        if cal.get("primary"):
            ids.insert(0, cal["id"])
        else:
            ids.append(cal["id"])
    return ids or ["primary"]


def get_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Fetch a single event with full attendee details (including RSVP status)."""
    service = _get_service(account)
    try:
        ev = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as e:
        handle_http_error(e, "calendar get")
        raise  # unreachable

    attendees = [
        {
            "email": a.get("email", ""),
            "displayName": a.get("displayName", ""),
            "responseStatus": a.get("responseStatus", "needsAction"),
            "optional": a.get("optional", False),
            "organizer": a.get("organizer", False),
            "self": a.get("self", False),
        }
        for a in ev.get("attendees", [])
    ]

    return {
        "id": ev["id"],
        "calendar": calendar_id,
        "title": ev.get("summary", "(no title)"),
        "description": ev.get("description", ""),
        "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
        "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
        "location": ev.get("location", ""),
        "busy": ev.get("transparency", "opaque") != "transparent",
        "status": ev.get("status", ""),
        "organizer": ev.get("organizer", {}).get("email", ""),
        "attendees": attendees,
        "attendeeCount": len(attendees),
        "meetLink": ev.get("hangoutLink", ""),
        "link": ev.get("htmlLink", ""),
    }


def delete_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
    notify: bool = False,
) -> dict:
    """Delete an event. Reads its title first so the result is identifiable.

    notify=True sends cancellation notices to attendees (sendUpdates=all);
    default sends none, so deleting a duplicate won't spam guests.
    """
    service = _get_service(account)
    try:
        ev = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        title = ev.get("summary", "(no title)")
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all" if notify else "none",
        ).execute()
    except HttpError as e:
        handle_http_error(e, "calendar delete")
        raise  # unreachable

    return {"deleted": True, "id": event_id, "title": title, "calendar": calendar_id}


def list_events(
    days: int = 7,
    account: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """List upcoming events within the next N days.

    calendar_id "all" reads every calendar the account can access and merges
    the results, sorted by start time.
    """
    if days <= 0:
        raise ValidationError(
            f"Days must be positive, got: {days}",
            suggestion="Use a positive number of days, e.g. --days 7",
        )
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=days)

    service = _get_service(account)
    cal_ids = _all_calendar_ids(service) if calendar_id == "all" else [calendar_id]

    events = []
    for cid in cal_ids:
        try:
            result = service.events().list(
                calendarId=cid,
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=50,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        except HttpError as e:
            handle_http_error(e, "calendar list")
            raise  # unreachable

        for ev in result.get("items", []):
            events.append({
                "id": ev["id"],
                "calendar": cid,
                "title": ev.get("summary", "(no title)"),
                "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
                "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
                "location": ev.get("location", ""),
                "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
                "meetLink": ev.get("hangoutLink", ""),
                "link": ev.get("htmlLink", ""),
            })

    if len(cal_ids) > 1:
        events.sort(key=lambda e: e["start"])

    return {"events": events, "count": len(events), "days": days, "calendars": cal_ids}


def list_events_range(
    days_back: int = 2,
    days_forward: int = 2,
    account: str | None = None,
) -> dict:
    """List events in a range: from N days ago to M days ahead.

    Used by calendar-to-CRM sync to catch both past meetings and
    newly booked future meetings.
    """
    now = datetime.now(timezone.utc)
    time_min = now - timedelta(days=days_back)
    time_max = now + timedelta(days=days_forward)

    service = _get_service(account)
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=100,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except HttpError as e:
        handle_http_error(e, "calendar list_events_range")
        raise  # unreachable

    events = []
    for ev in result.get("items", []):
        events.append({
            "id": ev["id"],
            "title": ev.get("summary", "(no title)"),
            "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
            "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
            "location": ev.get("location", ""),
            "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
            "meetLink": ev.get("hangoutLink", ""),
            "link": ev.get("htmlLink", ""),
        })

    return {"events": events, "count": len(events), "days_back": days_back, "days_forward": days_forward}

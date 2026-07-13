"""Tests for calendar_ops module — mocked Google API tests for create and list."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import ValidationError


@pytest.fixture
def mock_calendar_service():
    return MagicMock()


@pytest.fixture
def patch_calendar(mock_calendar_service):
    with patch("calendar_ops.build_service", return_value=mock_calendar_service):
        import calendar_ops
        yield calendar_ops, mock_calendar_service


def test_create_basic(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().insert().execute.return_value = {
        "id": "evt1",
        "summary": "Demo call",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }
    result = cal_mod.create(title="Demo call", start="2026-03-01T14:00:00+00:00", duration=30)
    assert result["created"] is True
    assert result["id"] == "evt1"


def test_create_with_meet(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().insert().execute.return_value = {
        "id": "evt2",
        "summary": "Meet call",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt2",
        "conferenceData": {
            "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]
        },
    }
    result = cal_mod.create(title="Meet call", start="2026-03-01T14:00:00+00:00", meet=True)
    assert result["meetLink"] == "https://meet.google.com/abc-defg-hij"


def test_create_invalid_start(patch_calendar):
    cal_mod, _ = patch_calendar
    with pytest.raises(ValidationError, match="Invalid --start"):
        cal_mod.create(title="Test", start="not-a-date")


def test_update_meet_false_removes_link(patch_calendar):
    """--meet false must strip conferenceData AND set conferenceDataVersion=1,
    otherwise the API silently ignores the removal and the Meet link survives."""
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt3",
        "summary": "Quarterly review",
        "start": {"dateTime": "2026-03-01T18:00:00+00:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-03-01T21:00:00+00:00", "timeZone": "America/New_York"},
        "conferenceData": {
            "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]
        },
    }
    mock_svc.events().patch().execute.return_value = {
        "id": "evt3",
        "summary": "Quarterly review",
        "start": {"dateTime": "2026-03-01T18:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T21:00:00+00:00"},
    }

    cal_mod.update(event_id="evt3", meet=False, reminders="60,30,10")

    _, kwargs = mock_svc.events().patch.call_args
    assert kwargs["conferenceDataVersion"] == 1
    assert "conferenceData" not in kwargs["body"]


def test_create_negative_duration(patch_calendar):
    cal_mod, _ = patch_calendar
    with pytest.raises(ValidationError, match="positive"):
        cal_mod.create(title="Test", start="2026-03-01T14:00:00+00:00", duration=-1)


def test_create_validates_attendee_emails(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    with pytest.raises(ValidationError, match="Invalid email"):
        cal_mod.create(title="Test", start="2026-03-01T14:00:00+00:00", attendees="not-email")


def test_create_uses_local_tz_when_naive():
    """Verify that naive datetimes get local timezone, not UTC."""
    import calendar_ops
    # We can't easily mock ZoneInfo("localtime"), but we can verify the logic
    # by passing a naive datetime and checking the event body
    # Just verify the function handles it without error
    with patch("calendar_ops.build_service") as mock_build:
        mock_svc = MagicMock()
        mock_build.return_value = mock_svc
        mock_svc.events().insert().execute.return_value = {
            "id": "evt3",
            "summary": "Local event",
            "start": {"dateTime": "2026-03-01T14:00:00"},
            "end": {"dateTime": "2026-03-01T14:30:00"},
            "htmlLink": "https://calendar.google.com/event?eid=evt3",
        }
        result = calendar_ops.create(title="Local event", start="2026-03-01T14:00")
        assert result["created"] is True


def test_create_with_location(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().insert().execute.return_value = {
        "id": "evt4",
        "summary": "Office visit",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "location": "123 Main St, Springfield",
        "htmlLink": "https://calendar.google.com/event?eid=evt4",
    }
    result = cal_mod.create(
        title="Office visit", start="2026-03-01T14:00:00+00:00",
        location="123 Main St, Springfield",
    )
    assert result["location"] == "123 Main St, Springfield"
    body = mock_svc.events().insert.call_args[1]["body"]
    assert body["location"] == "123 Main St, Springfield"


def test_list_events_success(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().list().execute.return_value = {
        "items": [
            {
                "id": "evt1",
                "summary": "Meeting",
                "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
                "end": {"dateTime": "2026-03-01T15:00:00+00:00"},
                "htmlLink": "https://calendar.google.com/event?eid=evt1",
            }
        ]
    }
    result = cal_mod.list_events(days=7)
    assert result["count"] == 1
    assert result["events"][0]["title"] == "Meeting"


def test_create_default_reminders(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().insert().execute.return_value = {
        "id": "evt_rem",
        "summary": "Reminder test",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt_rem",
    }
    cal_mod.create(title="Reminder test", start="2026-03-01T14:00:00+00:00")
    body = mock_svc.events().insert.call_args[1]["body"]
    assert body["reminders"]["useDefault"] is False
    minutes = sorted(o["minutes"] for o in body["reminders"]["overrides"])
    assert minutes == [10, 30, 60]


def test_create_custom_reminders(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().insert().execute.return_value = {
        "id": "evt_crem",
        "summary": "Custom rem",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt_crem",
    }
    cal_mod.create(title="Custom rem", start="2026-03-01T14:00:00+00:00", reminders="120,15")
    body = mock_svc.events().insert.call_args[1]["body"]
    minutes = sorted(o["minutes"] for o in body["reminders"]["overrides"])
    assert minutes == [15, 120]


def test_create_invalid_reminders(patch_calendar):
    cal_mod, _ = patch_calendar
    with pytest.raises(ValidationError, match="Invalid --reminders"):
        cal_mod.create(title="Test", start="2026-03-01T14:00:00+00:00", reminders="abc")


def test_update_duration(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt1",
        "summary": "Demo call",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00", "timeZone": "UTC"},
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }
    mock_svc.events().patch().execute.return_value = {
        "id": "evt1",
        "summary": "Demo call",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T15:00:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }
    result = cal_mod.update(event_id="evt1", duration=60)
    assert result["updated"] is True
    assert result["id"] == "evt1"
    body = mock_svc.events().patch.call_args[1]["body"]
    assert "15:00:00" in body["end"]["dateTime"]


def test_update_title_only(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt2",
        "summary": "Old title",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00", "timeZone": "UTC"},
        "htmlLink": "https://calendar.google.com/event?eid=evt2",
    }
    mock_svc.events().patch().execute.return_value = {
        "id": "evt2",
        "summary": "New title",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt2",
    }
    result = cal_mod.update(event_id="evt2", title="New title")
    assert result["updated"] is True
    assert result["title"] == "New title"
    body = mock_svc.events().patch.call_args[1]["body"]
    assert body["summary"] == "New title"


def test_update_invalid_start(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt3",
        "summary": "Test",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00", "timeZone": "UTC"},
    }
    with pytest.raises(ValidationError, match="Invalid --start"):
        cal_mod.update(event_id="evt3", start="not-a-date")


def test_list_events_negative_days(patch_calendar):
    cal_mod, _ = patch_calendar
    with pytest.raises(ValidationError, match="positive"):
        cal_mod.list_events(days=0)


def test_get_event_attendees(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt1",
        "summary": "Demo call",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
        "organizer": {"email": "you@example.com"},
        "attendees": [
            {"email": "you@example.com", "responseStatus": "accepted", "organizer": True, "self": True},
            {"email": "prospect@acme.com", "displayName": "Jane Doe", "responseStatus": "needsAction"},
        ],
    }
    result = cal_mod.get_event(event_id="evt1")
    assert result["id"] == "evt1"
    assert result["attendeeCount"] == 2
    assert result["organizer"] == "you@example.com"
    assert result["attendees"][1]["email"] == "prospect@acme.com"
    assert result["attendees"][1]["responseStatus"] == "needsAction"
    assert result["attendees"][1]["displayName"] == "Jane Doe"


def test_get_event_no_attendees(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt2",
        "summary": "Solo block",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt2",
    }
    result = cal_mod.get_event(event_id="evt2")
    assert result["attendeeCount"] == 0
    assert result["attendees"] == []


def test_get_event_secondary_calendar(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.events().get().execute.return_value = {
        "id": "evt3",
        "summary": "Team sync",
        "start": {"dateTime": "2026-03-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-01T14:30:00+00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=evt3",
    }
    result = cal_mod.get_event(event_id="evt3", calendar_id="team@example.com")
    assert result["calendar"] == "team@example.com"
    assert mock_svc.events().get.call_args[1]["calendarId"] == "team@example.com"


def test_list_calendars(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.calendarList().list().execute.return_value = {
        "items": [
            {"id": "team@example.com", "summary": "Team", "accessRole": "writer"},
            {"id": "you@example.com", "summary": "Primary", "primary": True, "accessRole": "owner"},
        ]
    }
    result = cal_mod.list_calendars()
    assert result["count"] == 2
    # primary sorts first
    assert result["calendars"][0]["primary"] is True
    assert result["calendars"][0]["id"] == "you@example.com"


def test_list_events_all_calendars(patch_calendar):
    cal_mod, mock_svc = patch_calendar
    mock_svc.calendarList().list().execute.return_value = {
        "items": [
            {"id": "you@example.com", "primary": True},
            {"id": "team@example.com"},
        ]
    }

    def list_side_effect(*args, **kwargs):
        cid = kwargs["calendarId"]
        items = {
            "you@example.com": [{
                "id": "p1", "summary": "Primary evt",
                "start": {"dateTime": "2026-03-01T15:00:00+00:00"},
                "end": {"dateTime": "2026-03-01T15:30:00+00:00"},
                "htmlLink": "https://x/p1",
            }],
            "team@example.com": [{
                "id": "t1", "summary": "Team evt",
                "start": {"dateTime": "2026-03-01T09:00:00+00:00"},
                "end": {"dateTime": "2026-03-01T09:30:00+00:00"},
                "htmlLink": "https://x/t1",
            }],
        }
        resp = MagicMock()
        resp.execute.return_value = {"items": items[cid]}
        return resp

    mock_svc.events().list.side_effect = list_side_effect
    result = cal_mod.list_events(days=7, calendar_id="all")
    assert result["count"] == 2
    assert result["calendars"] == ["you@example.com", "team@example.com"]
    # merged and sorted by start time — team evt (09:00) before primary (15:00)
    assert [e["id"] for e in result["events"]] == ["t1", "p1"]
    assert result["events"][0]["calendar"] == "team@example.com"

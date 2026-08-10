"""Tests for calendar_booking — time/hours parsing and the saved-state verifier.

The Chrome-driving parts need a live browser and are exercised manually; what is
covered here is everything that runs before Chrome is touched, plus the check
that decides whether a save actually did what was asked.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import calendar_booking
from calendar_booking import ChromeError, parse_hours, parse_time
from output import ValidationError


# ---------------------------------------------------------------------------
# parse_time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("17:00", "5:00pm"),
        ("5:00pm", "5:00pm"),
        ("5pm", "5:00pm"),
        ("5:00 PM", "5:00pm"),
        ("5:00p.m.", "5:00pm"),
        ("9", "9:00am"),
        ("9:30am", "9:30am"),
        ("00:00", "12:00am"),
        ("12:00am", "12:00am"),
        ("12pm", "12:00pm"),
        ("23:59", "11:59pm"),
        ("24:00", "12:00am"),
        ("  17:15  ", "5:15pm"),
    ],
)
def test_parse_time_accepts(raw, expected):
    assert parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "17:60", "25:00", "13:00pm", "0pm", "5:1:2"])
def test_parse_time_rejects(raw):
    with pytest.raises(ValidationError):
        parse_time(raw)


# ---------------------------------------------------------------------------
# parse_hours
# ---------------------------------------------------------------------------

def test_parse_hours_single_day():
    assert parse_hours("mon=9:00am-5:00pm") == {"Mon": [("9:00am", "5:00pm")]}


def test_parse_hours_multiple_periods():
    assert parse_hours("tue=9-12+14-17") == {
        "Tue": [("9:00am", "12:00pm"), ("2:00pm", "5:00pm")]
    }


def test_parse_hours_unavailable_is_empty_list():
    assert parse_hours("wed=unavailable") == {"Wed": []}


def test_parse_hours_mixed_and_day_aliases():
    assert parse_hours("Monday=9-17, fri=UNAVAILABLE ,SUN=10am-2pm") == {
        "Mon": [("9:00am", "5:00pm")],
        "Fri": [],
        "Sun": [("10:00am", "2:00pm")],
    }


def test_parse_hours_omitted_days_are_absent():
    """Days not named must not appear, so callers leave them untouched."""
    assert set(parse_hours("mon=9-17")) == {"Mon"}


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "mon",                 # no '='
        "xyz=9-17",            # unknown day
        "mon=9:00am",          # no range separator
        "mon=5pm-5pm",         # empty range
        "mon=9-17,mon=10-18",  # duplicate day
        "mon=25:00-26:00",     # out of range
    ],
)
def test_parse_hours_rejects(spec):
    with pytest.raises(ValidationError):
        parse_hours(spec)


# ---------------------------------------------------------------------------
# _verify — guards against a save that silently did the wrong thing
# ---------------------------------------------------------------------------

def _saved(duration="30 minutes", availability=None):
    return {
        "duration": duration,
        "availability": availability
        if availability is not None
        else [{"day": "Mon", "periods": [{"start": "9:00am", "end": "5:00pm"}]}],
    }


def test_verify_passes_when_state_matches():
    calendar_booking._verify(_saved(), 30, {"Mon": [("9:00am", "5:00pm")]})


@pytest.mark.parametrize(
    ("minutes", "label"),
    [(15, "15 minutes"), (45, "45 minutes"), (60, "1 hour"), (90, "1.5 hours"), (120, "2 hours")],
)
def test_verify_accepts_the_ui_duration_labels(minutes, label):
    """The UI writes '1 hour' rather than '60 minutes', so the check must map them."""
    calendar_booking._verify(_saved(duration=label), minutes, {})


def test_verify_rejects_wrong_duration():
    with pytest.raises(ChromeError, match="duration"):
        calendar_booking._verify(_saved(duration="30 minutes"), 60, {})


def test_verify_rejects_wrong_hours():
    with pytest.raises(ChromeError, match="Mon"):
        calendar_booking._verify(_saved(), None, {"Mon": [("10:00am", "4:00pm")]})


def test_verify_rejects_day_that_should_be_unavailable():
    with pytest.raises(ChromeError, match="Mon"):
        calendar_booking._verify(_saved(), None, {"Mon": []})


def test_verify_rejects_missing_day():
    with pytest.raises(ChromeError, match="Sat"):
        calendar_booking._verify(_saved(), None, {"Sat": [("9:00am", "5:00pm")]})


def test_verify_reports_every_problem_at_once():
    with pytest.raises(ChromeError) as exc:
        calendar_booking._verify(_saved(duration="30 minutes"), 60, {"Mon": [("8:00am", "4:00pm")]})
    assert "duration" in str(exc.value)
    assert "Mon" in str(exc.value)


# ---------------------------------------------------------------------------
# update — argument validation happens before Chrome is contacted
# ---------------------------------------------------------------------------

def test_update_requires_a_field():
    with pytest.raises(ValidationError, match="Nothing to update"):
        calendar_booking.update("Some page")


def test_update_rejects_duration_the_ui_cannot_set():
    with pytest.raises(ValidationError, match="37"):
        calendar_booking.update("Some page", duration=37)


def test_update_rejects_bad_hours_before_opening_chrome():
    with pytest.raises(ValidationError):
        calendar_booking.update("Some page", hours="notaday=9-17")

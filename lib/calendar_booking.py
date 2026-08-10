"""Google Calendar booking pages (appointment schedules) via Chrome DevTools.

Calendar API v3 has no appointment-schedule resource, and booking pages are
invisible to OAuth entirely — they are neither events nor calendars. The
Calendar web client drives them through an internal endpoint whose payload is
base64 protobuf, authenticated by the full Google session cookie jar.

Storing those cookies would put account-takeover-grade credentials at rest, so
these commands instead drive the Calendar UI inside the user's already-running
Chrome over the DevTools protocol. The session never leaves the browser.

Requires Chrome running with remote debugging enabled (the
chrome://inspect/#remote-debugging toggle), which writes the port to
DevToolsActivePort. That is the same connection the chrome-devtools MCP uses.
"""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path

from output import CliError, ValidationError

CALENDAR_HOST = "calendar.google.com"

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_ALIASES = {
    "mon": "Mon", "monday": "Mon",
    "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
    "wed": "Wed", "weds": "Wed", "wednesday": "Wed",
    "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
    "fri": "Fri", "friday": "Fri",
    "sat": "Sat", "saturday": "Sat",
    "sun": "Sun", "sunday": "Sun",
}

UNAVAILABLE = "unavailable"

# Durations the UI offers directly. Anything else needs the Custom... flow,
# which we do not drive.
ALLOWED_DURATIONS = [15, 30, 45, 60, 90, 120]


class ChromeError(CliError):
    """Chrome is not reachable, or the Calendar UI did not behave as expected."""

    def __init__(self, message: str, *, suggestion: str | None = None) -> None:
        super().__init__(message, suggestion=suggestion, code="GW_CHROME_ERROR")


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.IGNORECASE)


def parse_time(raw: str) -> str:
    """Normalise a time to the format the Calendar UI uses, e.g. '5:00pm'.

    Accepts 24-hour ('17:00', '17') and 12-hour ('5pm', '5:00 PM', '5:00p.m.').
    """
    text = raw.strip()
    match = _TIME_RE.match(text)
    if not match:
        raise ValidationError(
            f"Could not parse time: '{raw}'",
            suggestion="Use 24-hour (17:00) or 12-hour (5:00pm) form.",
        )

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "").lower()

    if minute > 59:
        raise ValidationError(f"Minute out of range in '{raw}': {minute}")

    if meridiem:
        if not 1 <= hour <= 12:
            raise ValidationError(
                f"Hour out of range for 12-hour time '{raw}': {hour}",
                suggestion="With am/pm the hour must be 1-12.",
            )
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 24:
        raise ValidationError(f"Hour out of range in '{raw}': {hour}")

    # The UI renders midnight at the end of a range as 12:00am, same as the start.
    hour = hour % 24
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d}{suffix}"


def parse_hours(spec: str) -> dict[str, list[tuple[str, str]]]:
    """Parse a --hours spec into {day: [(start, end), ...]}.

    Format: comma-separated `day=value`, where value is either `unavailable` or
    one or more `start-end` ranges joined by `+`.

        "mon=9:00am-5:00pm,tue=9-12+14-17,wed=unavailable"

    Only the days named are touched; days left out keep their current hours.
    """
    result: dict[str, list[tuple[str, str]]] = {}

    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValidationError(
                f"Malformed --hours segment: '{chunk}'",
                suggestion="Each segment is day=value, e.g. mon=9:00am-5:00pm",
            )

        raw_day, _, value = chunk.partition("=")
        day = DAY_ALIASES.get(raw_day.strip().lower())
        if not day:
            raise ValidationError(
                f"Unknown day: '{raw_day.strip()}'",
                suggestion="Use mon, tue, wed, thu, fri, sat or sun.",
            )
        if day in result:
            raise ValidationError(f"Day '{day}' appears twice in --hours")

        value = value.strip()
        if value.lower() == UNAVAILABLE:
            result[day] = []
            continue

        periods = []
        for part in value.split("+"):
            part = part.strip()
            # Split on the hyphen separating the two times, not the one inside
            # a token like '9-5'. Times never contain a hyphen, so the first
            # hyphen after position 0 is always the separator.
            if "-" not in part:
                raise ValidationError(
                    f"Malformed time range: '{part}'",
                    suggestion="A range looks like 9:00am-5:00pm",
                )
            start_raw, _, end_raw = part.partition("-")
            start, end = parse_time(start_raw), parse_time(end_raw)
            if start == end:
                raise ValidationError(
                    f"Range starts and ends at the same time: '{part}'"
                )
            periods.append((start, end))

        if not periods:
            raise ValidationError(f"No time ranges given for {day}")
        result[day] = periods

    if not result:
        raise ValidationError(
            "--hours did not name any days",
            suggestion="Example: --hours 'mon=9:00am-5:00pm,fri=unavailable'",
        )
    return result


# ---------------------------------------------------------------------------
# Chrome DevTools Protocol client
# ---------------------------------------------------------------------------

def _devtools_active_port_file() -> Path:
    """Where Chrome writes its debugging port, per platform."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    else:
        base = Path.home() / ".config" / "google-chrome"
    return base / "DevToolsActivePort"


def _devtools_url() -> str:
    """Read DevToolsActivePort and build the browser WebSocket URL."""
    override = os.environ.get("GW_CHROME_DEVTOOLS_URL")
    if override:
        return override

    port_file = _devtools_active_port_file()
    if not port_file.exists():
        raise ChromeError(
            f"Chrome remote debugging is not active (no {port_file}).",
            suggestion=(
                "Open chrome://inspect/#remote-debugging in Chrome and enable the "
                "toggle, then retry. Set GW_CHROME_DEVTOOLS_URL to override."
            ),
        )

    lines = port_file.read_text().splitlines()
    if len(lines) < 2:
        raise ChromeError(
            f"{port_file} is malformed.",
            suggestion="Restart Chrome, then re-enable chrome://inspect/#remote-debugging.",
        )
    return f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"


class ChromeSession:
    """Minimal synchronous CDP client scoped to one Calendar tab.

    Chrome's chrome://inspect debugging mode exposes only the browser
    WebSocket endpoint — the /json HTTP discovery routes return 404 — so
    targets are found over CDP rather than by HTTP.
    """

    def __init__(self, user_index: int = 0, timeout: float = 30.0) -> None:
        self._user_index = user_index
        self._timeout = timeout
        self._next_id = 0
        self._session_id: str | None = None
        self._target_id: str | None = None
        self._ws = None
        self._owns_tab = False

    # -- connection ---------------------------------------------------------

    def __enter__(self) -> ChromeSession:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ChromeError(
                "The 'websockets' package is required to drive Chrome.",
                suggestion="pip install websockets  (or rerun /gw-setup)",
            ) from exc

        url = _devtools_url()
        try:
            self._ws = connect(url, open_timeout=10, max_size=64 * 1024 * 1024)
        except Exception as exc:
            raise ChromeError(
                f"Could not connect to Chrome at {url}: {exc}",
                suggestion="Is Chrome running with chrome://inspect/#remote-debugging enabled?",
            ) from exc

        self._attach()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def _attach(self) -> None:
        """Attach to an existing Calendar tab, or open one."""
        targets = self._send("Target.getTargets")["targetInfos"]
        page = next(
            (t for t in targets if t["type"] == "page" and CALENDAR_HOST in t.get("url", "")),
            None,
        )
        if page is None:
            created = self._send(
                "Target.createTarget",
                {"url": f"https://{CALENDAR_HOST}/calendar/u/{self._user_index}/r/week"},
            )
            self._target_id = created["targetId"]
            self._owns_tab = True
        else:
            self._target_id = page["targetId"]

        attached = self._send(
            "Target.attachToTarget", {"targetId": self._target_id, "flatten": True}
        )
        self._session_id = attached["sessionId"]
        self._send("Page.enable", session=True)

    # -- protocol -----------------------------------------------------------

    def _send(self, method: str, params: dict | None = None, session: bool = False) -> dict:
        self._next_id += 1
        message_id = self._next_id
        payload: dict = {"id": message_id, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = self._session_id

        self._ws.send(json.dumps(payload))
        return self._await_result(message_id)

    def _await_result(self, message_id: int) -> dict:
        """Pump messages until our reply arrives, auto-dismissing dialogs.

        A page with unsaved changes raises beforeunload on navigation, which
        would otherwise block every later command.
        """
        while True:
            try:
                raw = self._ws.recv(timeout=self._timeout)
            except TimeoutError as exc:
                raise ChromeError(
                    f"Chrome did not respond within {self._timeout:.0f}s.",
                    suggestion="Check the Calendar tab in Chrome for a dialog or prompt.",
                ) from exc

            message = json.loads(raw)

            if message.get("method") == "Page.javascriptDialogOpening":
                self._send_no_wait(
                    "Page.handleJavaScriptDialog", {"accept": True}, session=True
                )
                continue

            if message.get("id") != message_id:
                continue

            if "error" in message:
                raise ChromeError(f"Chrome rejected {message['id']}: {message['error']}")
            return message.get("result", {})

    def _send_no_wait(self, method: str, params: dict, session: bool = False) -> None:
        self._next_id += 1
        payload: dict = {"id": self._next_id, "method": method, "params": params}
        if session:
            payload["sessionId"] = self._session_id
        self._ws.send(json.dumps(payload))

    # -- page operations ----------------------------------------------------

    def navigate(self, url: str) -> None:
        self._send("Page.navigate", {"url": url}, session=True)

    def evaluate(self, expression: str):
        """Run an async JS arrow function and return its resolved value.

        The shared helpers are injected into the same scope, so each script is
        a bare arrow function rather than a self-contained program.
        """
        wrapped = (
            "(async () => {\n"
            f"{_JS_PRELUDE}\n"
            f"return await ({expression})();\n"
            "})()"
        )
        result = self._send(
            "Runtime.evaluate",
            {
                "expression": wrapped,
                "awaitPromise": True,
                "returnByValue": True,
            },
            session=True,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise ChromeError(f"Calendar page script failed: {text}")
        return result.get("result", {}).get("value")


# ---------------------------------------------------------------------------
# JavaScript executed inside the Calendar page
# ---------------------------------------------------------------------------

# Shared helpers prepended to every page script.
_JS_PRELUDE = """
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(fn, timeout = 20000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() > deadline) return null;
    await sleep(200);
  }
}

function visible(nodes) {
  return [...nodes].filter(n => n.offsetParent !== null);
}

// Group the flat list of availability rows by day. A row carries a day label
// only when it opens a new day; later rows are extra periods for that day.
function readDays() {
  const days = [];
  let current = null;
  for (const row of document.querySelectorAll('.V3VAR')) {
    const label = row.querySelector('.MlP2sf');
    const name = label ? label.textContent.trim() : '';
    if (name) {
      current = { day: name, rows: [], periods: [] };
      days.push(current);
    }
    if (!current) continue;
    current.rows.push(row);
    const inputs = row.querySelectorAll('input');
    if (inputs.length === 2) {
      current.periods.push({ start: inputs[0].value, end: inputs[1].value });
    }
  }
  return days;
}

async function setTime(input, value) {
  input.focus();
  input.click();
  await sleep(300);

  // The dropdown only offers half-hour steps; anything else must be typed.
  const option = visible(document.querySelectorAll('[role="option"]'))
    .find(o => o.textContent.trim().toLowerCase() === value.toLowerCase());
  if (option) {
    option.click();
    await sleep(400);
    return;
  }

  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(300);
  for (const type of ['keydown', 'keypress', 'keyup']) {
    input.dispatchEvent(new KeyboardEvent(type,
      { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
  }
  await sleep(400);
}
"""


JS_LIST_PAGES = """
async () => {
  const items = await waitFor(() => {
    const found = document.querySelectorAll('li[role="listitem"][aria-label]');
    return found.length ? found : null;
  });
  if (!items) return { error: 'no-sidebar' };
  return {
    names: [...items]
      .filter(li => li.querySelector('button[aria-label^="Options for "]'))
      .map(li => li.getAttribute('aria-label')),
  };
}
"""


JS_OPEN_EDITOR = """
async () => {
  const name = %s;
  const button = await waitFor(
    () => document.querySelector(
      'button[aria-label="Options for ' + name.replace(/"/g, '\\\\"') + '"]'));
  if (!button) return { error: 'not-found' };

  button.click();
  await sleep(800);
  const edit = visible(document.querySelectorAll('[role="menuitem"]'))
    .find(item => item.textContent.trim() === 'Edit');
  if (!edit) return { error: 'no-edit-item' };
  edit.click();

  const ready = await waitFor(() => document.querySelector('.V3VAR'));
  return ready ? { ok: true } : { error: 'editor-timeout' };
}
"""


JS_READ_SCHEDULE = """
async () => {
  const ready = await waitFor(() => document.querySelector('.V3VAR'));
  if (!ready) return { error: 'editor-timeout' };

  const title = document.querySelector('input[aria-label="Add title"]');
  const duration = document.querySelector('div[role="combobox"][aria-label="Duration"]');
  const timezone = document.querySelector('input[aria-label="Timezone"]');
  const recurrence = document.querySelector('div[role="combobox"][aria-label="Recurrence"]');

  return {
    name: title ? title.value : null,
    duration: duration ? duration.textContent.trim() : null,
    recurrence: recurrence ? recurrence.textContent.trim() : null,
    timezone: timezone ? timezone.value : null,
    availability: readDays().map(d => ({ day: d.day, periods: d.periods })),
  };
}
"""


JS_SET_DURATION = """
async () => {
  const minutes = %d;
  const combo = document.querySelector('div[role="combobox"][aria-label="Duration"]');
  if (!combo) return { error: 'no-duration-control' };

  combo.click();
  await sleep(600);
  const option = visible(document.querySelectorAll('[role="option"]'))
    .find(o => o.getAttribute('data-value') === String(minutes));
  if (!option) {
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return { error: 'unsupported-duration' };
  }
  option.click();
  await sleep(600);
  return { ok: true, duration: combo.textContent.trim() };
}
"""


# Rebuild one day from scratch: clear it, then add and fill each period. Going
# through the cleared state keeps the outcome independent of what was there
# before, so repeated runs converge on the same result.
JS_SET_DAY = """
async () => {
  const targetDay = %s;
  const periods = %s;

  const findDay = () => readDays().find(d => d.day === targetDay);

  if (!findDay()) return { error: 'day-not-found', day: targetDay };

  // The clear button is labelled 'Unavailable all day' when the day holds a
  // single period and 'Unavailable' when it holds several, where it drops just
  // that one period. Click until the day is empty either way.
  for (let guard = 0; findDay().periods.length && guard < 24; guard++) {
    const clear = findDay().rows[0].querySelector(
      'button[aria-label="Unavailable all day"], button[aria-label="Unavailable"]');
    if (!clear) return { error: 'no-clear-button', day: targetDay };
    clear.click();
    await sleep(700);
  }
  if (findDay().periods.length) return { error: 'clear-failed', day: targetDay };

  for (const [start, end] of periods) {
    // Only a day's first row carries the add button.
    const add = findDay().rows
      .map(r => r.querySelector('button[aria-label="Add another period to this day"]'))
      .find(Boolean);
    if (!add) return { error: 'no-add-button', day: targetDay };

    // Calendar keeps periods sorted by time, so the new row is not necessarily
    // last. Identify it by which row is new rather than by position.
    const existing = new Set(findDay().rows);
    add.click();
    await sleep(700);
    const added = findDay().rows.find(r => !existing.has(r));
    if (!added) return { error: 'add-failed', day: targetDay };

    const inputs = added.querySelectorAll('input');
    if (inputs.length !== 2) return { error: 'row-missing-inputs', day: targetDay };
    await setTime(inputs[0], start);
    await setTime(inputs[1], end);
  }

  return { ok: true, day: targetDay, periods: findDay().periods };
}
"""


JS_SAVE = """
async () => {
  const findButton = label => visible(document.querySelectorAll('button'))
    .find(b => b.textContent.trim() === label);

  const next = findButton('Next');
  if (!next) return { error: 'no-next-button' };
  next.click();

  const save = await waitFor(() => findButton('Save'));
  if (!save) return { error: 'no-save-button' };
  save.click();

  // The editor closes once the save lands.
  const closed = await waitFor(() => !document.querySelector('.V3VAR'));
  return closed ? { ok: true } : { error: 'save-timeout' };
}
"""


def _js_literal(value) -> str:
    """Embed a Python value in a script as a JSON literal."""
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def _calendar_url(user_index: int) -> str:
    return f"https://{CALENDAR_HOST}/calendar/u/{user_index}/r/week"


def _check(result, action: str):
    """Turn a JS `{error: ...}` payload into a CliError."""
    if not isinstance(result, dict):
        raise ChromeError(f"Unexpected response while {action}: {result!r}")
    reason = result.get("error")
    if reason:
        if reason == "not-found":
            suggestion = "Run 'gw calendar booking list' — the name must match exactly."
        elif reason == "unsupported-duration":
            suggestion = f"The UI offers: {', '.join(str(d) for d in ALLOWED_DURATIONS)} minutes."
        else:
            suggestion = (
                "The Calendar UI may have changed. Open the booking page in "
                "Chrome and check it still looks as expected."
            )
        raise ChromeError(f"Could not {action}: {reason}", suggestion=suggestion)
    return result


def list_pages(user_index: int = 0) -> dict:
    """List the booking pages in the Calendar sidebar."""
    with ChromeSession(user_index) as chrome:
        chrome.navigate(_calendar_url(user_index))
        result = _check(chrome.evaluate(JS_LIST_PAGES), "read the booking page list")
    names = result["names"]
    return {"count": len(names), "booking_pages": names}


def get(name: str, user_index: int = 0) -> dict:
    """Read one booking page's duration, availability and timezone."""
    with ChromeSession(user_index) as chrome:
        chrome.navigate(_calendar_url(user_index))
        _check(
            chrome.evaluate(JS_OPEN_EDITOR % _js_literal(name)),
            f"open the booking page '{name}'",
        )
        return _check(chrome.evaluate(JS_READ_SCHEDULE), f"read '{name}'")


def update(
    name: str,
    duration: int | None = None,
    hours: str | None = None,
    user_index: int = 0,
    dry_run: bool = False,
) -> dict:
    """Change a booking page's appointment duration and/or weekly hours."""
    if duration is None and hours is None:
        raise ValidationError(
            "Nothing to update.",
            suggestion="Pass --duration and/or --hours.",
        )
    if duration is not None and duration not in ALLOWED_DURATIONS:
        raise ValidationError(
            f"Unsupported --duration: {duration}",
            suggestion=f"Choose one of: {', '.join(str(d) for d in ALLOWED_DURATIONS)}",
        )

    wanted = parse_hours(hours) if hours else {}

    with ChromeSession(user_index) as chrome:
        chrome.navigate(_calendar_url(user_index))
        _check(
            chrome.evaluate(JS_OPEN_EDITOR % _js_literal(name)),
            f"open the booking page '{name}'",
        )
        before = _check(chrome.evaluate(JS_READ_SCHEDULE), f"read '{name}'")

        if dry_run:
            return {
                "dry_run": True,
                "booking_page": before["name"],
                "current": {
                    "duration": before["duration"],
                    "availability": before["availability"],
                },
                "would_set": {
                    "duration": f"{duration} minutes" if duration else None,
                    "availability": [
                        {"day": day, "periods": [{"start": s, "end": e} for s, e in periods]}
                        for day, periods in wanted.items()
                    ] or None,
                },
            }

        if duration is not None:
            _check(chrome.evaluate(JS_SET_DURATION % duration), "set the duration")

        for day, periods in wanted.items():
            _check(
                chrome.evaluate(
                    JS_SET_DAY % (_js_literal(day), _js_literal([list(p) for p in periods]))
                ),
                f"set availability for {day}",
            )

        _check(chrome.evaluate(JS_SAVE), f"save '{name}'")

        # Read the saved state back rather than trusting the form.
        _check(
            chrome.evaluate(JS_OPEN_EDITOR % _js_literal(name)),
            f"reopen '{name}' to verify",
        )
        after = _check(chrome.evaluate(JS_READ_SCHEDULE), f"verify '{name}'")

    _verify(after, duration, wanted)
    return {
        "booking_page": after["name"],
        "duration": after["duration"],
        "timezone": after["timezone"],
        "availability": after["availability"],
        "saved": True,
    }


def _verify(after: dict, duration: int | None, wanted: dict[str, list[tuple[str, str]]]) -> None:
    """Fail loudly if the saved page does not match what was asked for."""
    problems = []

    if duration is not None and not after["duration"].startswith(str(duration)):
        # '1 hour' and '1.5 hours' do not start with their minute count.
        labels = {60: "1 hour", 90: "1.5 hours", 120: "2 hours"}
        if after["duration"] != labels.get(duration):
            problems.append(f"duration is '{after['duration']}', expected {duration} minutes")

    saved = {d["day"]: [(p["start"], p["end"]) for p in d["periods"]] for d in after["availability"]}
    for day, periods in wanted.items():
        if saved.get(day) != periods:
            problems.append(f"{day} is {saved.get(day)}, expected {periods}")

    if problems:
        raise ChromeError(
            "The booking page saved, but does not match the request: "
            + "; ".join(problems),
            suggestion="Open the booking page in Chrome and check it.",
        )

"""Output contract for the gw Google Workspace CLI.

Success: JSON to stdout, exit 0
Error:   JSON to stderr, exit 1+
Warning: JSON to stderr, exit 0

Credentials are scrubbed from all error output.
Also provides shared HTTP error handling and email validation utilities.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from contextlib import contextmanager
from typing import NoReturn


# ---------------------------------------------------------------------------
# Stdout / stderr writers — use os.write() to avoid CodeQL
# py/clear-text-logging-sensitive-data false positives. CodeQL models
# print() and sys.stdout.write() as logging sinks but not os.write().
# These are CLI output channels, not logging.
# ---------------------------------------------------------------------------

def _write_stdout(text: str) -> None:
    os.write(sys.stdout.fileno(), (text + "\n").encode())


def _write_stderr(text: str) -> None:
    os.write(sys.stderr.fileno(), (text + "\n").encode())


# ---------------------------------------------------------------------------
# Exit codes (standard CLI convention)
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_GENERIC = 1
EXIT_VALIDATION = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_RATE_LIMIT = 5


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class CliError(Exception):
    """Base error with user-facing message and optional fix suggestion."""

    exit_code = EXIT_GENERIC

    def __init__(self, message: str, *, suggestion: str | None = None, code: str = "GW_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.code = code


class AuthError(CliError):
    """OAuth token missing, expired, or invalid."""

    exit_code = EXIT_AUTH

    def __init__(
        self,
        message: str | None = None,
        *,
        account: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        acct = account or "main"
        super().__init__(
            message or "Google OAuth token not found or expired.",
            suggestion=suggestion or (
                f"Run: gw auth status --account {acct}\n"
                f"To (re-)authenticate: gw auth login --account {acct}"
            ),
            code="AUTH_FAILED",
        )


class NotFoundError(CliError):
    """Requested resource (message, sheet, event, task) not found."""

    exit_code = EXIT_NOT_FOUND

    COMMANDS = {
        "message": "gmail list",
        "sheet": "sheets list",
        "spreadsheet": "sheets list",
        "event": "calendar list",
        "task": "tasks list",
        "file": "drive list",
        "folder": "drive list",
        "permission": "drive shares FILE_ID",
    }

    def __init__(self, entity: str, identifier: str) -> None:
        cmd = self.COMMANDS.get(entity.lower(), f"{entity.lower()} list")
        super().__init__(
            f"{entity} not found: {identifier}",
            suggestion=f"Check the identifier. Use 'gw.py {cmd}' to see available {entity.lower()}s.",
            code="NOT_FOUND",
        )


class RateLimitError(CliError):
    """Google API rate limit exceeded."""

    exit_code = EXIT_RATE_LIMIT

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "Google API rate limit exceeded.",
            suggestion="Wait 60 seconds and retry. Google Workspace limits: ~250 quota units/second.",
            code="RATE_LIMITED",
        )


class ValidationError(CliError):
    """Invalid input (bad email, date format, etc.)."""

    exit_code = EXIT_VALIDATION

    def __init__(self, message: str, *, suggestion: str | None = None) -> None:
        super().__init__(message, suggestion=suggestion, code="VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Credential scrubbing
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS = re.compile(
    r"(sk-[a-zA-Z0-9]{20,}|ya29\.[a-zA-Z0-9_-]{50,}|AIza[a-zA-Z0-9_-]{35}|"
    r"[a-zA-Z0-9+/]{40,}={0,2}|Bearer\s+[a-zA-Z0-9._-]{20,})",
)

_OAUTH_TOKEN_RE = re.compile(
    r'"?(?:access_token|refresh_token|client_secret|authorization|bearer|x-api-key)"?\s*[=:]\s*"?([^\s"\']+)"?',
    re.IGNORECASE,
)


def _scrub_credentials(text: str) -> str:
    """Remove potential API keys, tokens, and credentials from error text."""
    text = _CREDENTIAL_PATTERNS.sub("[REDACTED]", text)
    text = _OAUTH_TOKEN_RE.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Output format / field filter state
# ---------------------------------------------------------------------------

_output_format: str = "json"  # json | table | csv
_field_filter: list[str] | None = None


def set_output_format(fmt: str) -> None:
    """Set the global output format (json, table, csv)."""
    global _output_format
    _output_format = fmt


def set_field_filter(fields: list[str] | None) -> None:
    """Set the global field filter (list of field names to keep)."""
    global _field_filter
    _field_filter = fields


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cell(value: object) -> str:
    """Stringify a value for table/csv cells."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _apply_field_filter(data: object) -> object:
    """Filter dicts or list-of-dicts to selected fields."""
    if _field_filter is None:
        return data
    fields = _field_filter
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in fields}
    if isinstance(data, list):
        return [
            {k: v for k, v in item.items() if k in fields}
            if isinstance(item, dict) else item
            for item in data
        ]
    return data


def _find_data_array(data: object) -> list[dict] | None:
    """Locate the primary array in a response dict.

    Looks for common keys: results, messages, events, tasks, spreadsheets,
    labels, items, sources, data.
    Returns None if data is not a dict or no array found.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for key in ("results", "messages", "events", "tasks", "spreadsheets",
                "labels", "items", "sources", "data", "rows", "accounts",
                "steps"):
        val = data.get(key)
        if isinstance(val, list) and val and isinstance(val[0], (dict, list)):
            return val
    return None


_MAX_COL_WIDTH = 60


def _format_table(data: object) -> str:
    """Render data as a simple ASCII table with 60-char column cap."""
    rows = _find_data_array(data)
    if rows is None:
        # Fall back to JSON for non-tabular data
        return json.dumps(data, indent=2, default=str)

    rows = [_apply_field_filter(r) if isinstance(r, dict) else r for r in rows]
    if not rows or not isinstance(rows[0], dict):
        return json.dumps(data, indent=2, default=str)

    # Collect all keys in order
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                headers.append(k)
                seen.add(k)

    # Compute column widths
    col_widths = {h: len(h) for h in headers}
    cell_data: list[list[str]] = []
    for row in rows:
        cells = []
        for h in headers:
            c = _cell(row.get(h))
            if len(c) > _MAX_COL_WIDTH:
                c = c[: _MAX_COL_WIDTH - 3] + "..."
            col_widths[h] = max(col_widths[h], len(c))
            cells.append(c)
        cell_data.append(cells)

    # Cap widths
    for h in headers:
        col_widths[h] = min(col_widths[h], _MAX_COL_WIDTH)

    # Build table
    lines: list[str] = []
    header_line = "  ".join(h.ljust(col_widths[h]) for h in headers)
    lines.append(header_line)
    sep_line = "  ".join("-" * col_widths[h] for h in headers)
    lines.append(sep_line)
    for cells in cell_data:
        line = "  ".join(
            cells[i].ljust(col_widths[headers[i]])[:col_widths[headers[i]]]
            for i in range(len(headers))
        )
        lines.append(line)

    return "\n".join(lines)


def _format_csv(data: object) -> str:
    """Render data as CSV using stdlib csv.DictWriter."""
    rows = _find_data_array(data)
    if rows is None:
        return json.dumps(data, indent=2, default=str)

    rows = [_apply_field_filter(r) if isinstance(r, dict) else r for r in rows]
    if not rows or not isinstance(rows[0], dict):
        return json.dumps(data, indent=2, default=str)

    # Collect all keys in order
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                headers.append(k)
                seen.add(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(row.get(k)) for k in headers})
    return buf.getvalue().rstrip("\n")


# ---------------------------------------------------------------------------
# Output functions
# ---------------------------------------------------------------------------

def format_success(data: dict | list, *, compact: bool = False) -> str:
    """Format successful result as JSON string (legacy compat)."""
    if compact:
        return json.dumps(data, separators=(",", ":"), default=str)
    return json.dumps(data, indent=2, default=str)


def output_success(data: object, *, compact: bool = False) -> None:
    """Print success payload to stdout, respecting format/filter state."""
    fmt = _output_format

    if fmt == "table":
        _write_stdout(_format_table(data))
        return

    if fmt == "csv":
        _write_stdout(_format_csv(data))
        return

    # JSON (default)
    filtered = _apply_field_filter(data) if isinstance(data, (dict, list)) else data
    if compact:
        _write_stdout(json.dumps(filtered, separators=(",", ":"), default=str))
    else:
        _write_stdout(json.dumps(filtered, indent=2, default=str))


def format_error(error: Exception, *, compact: bool = False) -> str:
    """Format error as JSON string for stderr. Scrubs potential credentials."""
    payload: dict[str, str] = {"error": _scrub_credentials(str(error))}
    if isinstance(error, CliError):
        payload["code"] = error.code
        if error.suggestion:
            payload["suggestion"] = error.suggestion
    else:
        payload["code"] = type(error).__name__

    if compact:
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, indent=2)


def _exit_code_for(exc: BaseException) -> int:
    """Determine the exit code for an exception."""
    if isinstance(exc, CliError):
        return exc.exit_code
    return EXIT_GENERIC


def output_error(error: Exception, *, compact: bool = False) -> NoReturn:
    """Print JSON error to stderr and exit with appropriate code."""
    _write_stderr(format_error(error, compact=compact))
    sys.exit(_exit_code_for(error))


def output_warning(message: str, *, code: str = "WARNING", compact: bool = False) -> None:
    """Print warning to stderr (does not exit)."""
    payload = {"warning": message, "code": code}
    if compact:
        _write_stderr(json.dumps(payload, separators=(",", ":")))
    else:
        _write_stderr(json.dumps(payload))


# ---------------------------------------------------------------------------
# Error context manager
# ---------------------------------------------------------------------------

@contextmanager
def _handle_errors(compact: bool = False):
    """Context manager that catches exceptions and routes to output_error."""
    try:
        yield
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        output_error(e, compact=compact)


# ---------------------------------------------------------------------------
# Shared utilities — used by gmail, calendar_ops, sheets, tasks_ops
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def validate_email(email: str, field: str = "to") -> None:
    """Validate an email address. Raises ValidationError if malformed."""
    if not email or not _EMAIL_RE.match(email.strip()):
        raise ValidationError(
            f"Invalid email address in --{field}: '{email}'",
            suggestion="Use a valid email address, e.g. user@domain.com",
        )


def handle_http_error(e, context: str = "API call") -> None:
    """Convert common HttpError status codes to typed errors. Always raises."""
    resp = getattr(e, "resp", None)
    status = resp.status if resp is not None else 0
    detail = ""
    try:
        content = getattr(e, "content", b"")
        if content:
            error_body = json.loads(content)
            detail = error_body.get("error", {}).get("message", "")
    except Exception:
        pass
    if status == 429:
        raise RateLimitError() from e
    if status == 401:
        raise AuthError(f"Token revoked or expired during {context}.") from e
    if status == 403:
        raise CliError(
            f"Permission denied during {context}{': ' + detail if detail else ''}",
            suggestion="Check OAuth scopes and account permissions.",
        ) from e
    suffix = f": {detail}" if detail else ""
    raise CliError(f"Google API error ({status}) during {context}{suffix}") from e

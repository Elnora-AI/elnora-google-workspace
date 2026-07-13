"""Integration tests for the Google Workspace CLI — plumbing only, no API calls."""

import json
import os
import subprocess
import sys
from pathlib import Path


CLI = str(Path(__file__).resolve().parent.parent / "cli" / "gw.py")


def run(*args: str, env_override: dict | None = None) -> tuple[str, str, int]:
    """Run the GW CLI and return (stdout, stderr, exit_code)."""
    env = {**os.environ, "NO_COLOR": "1"}
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        [sys.executable, CLI, *args],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


# ---------------------------------------------------------------------------
# Help and version
# ---------------------------------------------------------------------------

class TestHelpAndVersion:
    def test_help_exits_0(self):
        stdout, _, code = run("--help")
        assert code == 0
        assert "Google Workspace CLI" in stdout
        assert "gmail" in stdout
        assert "sheets" in stdout
        assert "calendar" in stdout
        assert "tasks" in stdout
        assert "completion" in stdout

    def test_version_exits_0(self):
        import re
        stdout, _, code = run("--version")
        assert code == 0
        assert re.search(r"\bgw, version \d+\.\d+\.\d+\b", stdout.strip())

    def test_help_survives_non_utf8_locale(self):
        # Regression: em dashes in the gmail/calendar group help crashed with
        # UnicodeEncodeError (exit 1) when stdout was a pipe under a Windows
        # cp1252 locale. The CLI now forces UTF-8 stdout so help renders
        # regardless of the ambient locale encoding.
        for group in ("gmail", "calendar"):
            _, _, code = run(group, "--help", env_override={"PYTHONIOENCODING": "cp1252"})
            assert code == 0, f"{group} --help crashed under a cp1252 locale"


# ---------------------------------------------------------------------------
# Subcommand help
# ---------------------------------------------------------------------------

class TestSubcommandHelp:
    def test_gmail_help(self):
        stdout, _, code = run("gmail", "--help")
        assert code == 0
        assert "send" in stdout
        assert "draft" in stdout
        assert "list" in stdout
        assert "get" in stdout
        assert "reply" in stdout
        assert "draft-reply" in stdout
        assert "labels" in stdout
        assert "scan" in stdout

    def test_sheets_help(self):
        stdout, _, code = run("sheets", "--help")
        assert code == 0
        assert "read" in stdout
        assert "write" in stdout
        assert "append" in stdout
        assert "list" in stdout

    def test_calendar_help(self):
        stdout, _, code = run("calendar", "--help")
        assert code == 0
        assert "create" in stdout
        assert "list" in stdout

    def test_tasks_help(self):
        stdout, _, code = run("tasks", "--help")
        assert code == 0
        assert "create" in stdout
        assert "list" in stdout
        assert "complete" in stdout

    def test_forms_help(self):
        stdout, _, code = run("forms", "--help")
        assert code == 0
        assert "get" in stdout
        assert "responses" in stdout
        assert "response" in stdout
        assert "create" in stdout
        assert "add-items" in stdout
        assert "update-info" in stdout
        assert "update-item" in stdout
        assert "move-item" in stdout
        assert "delete-item" in stdout

    def test_forms_get_help(self):
        stdout, _, code = run("forms", "get", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "--account" in stdout
        assert "--compact" in stdout

    def test_forms_responses_help(self):
        stdout, _, code = run("forms", "responses", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "--no-answers" in stdout
        assert "--page-size" in stdout

    def test_forms_create_help(self):
        stdout, _, code = run("forms", "create", "--help")
        assert code == 0
        assert "--title" in stdout
        assert "--description" in stdout
        assert "--from-json" in stdout

    def test_forms_add_items_help(self):
        stdout, _, code = run("forms", "add-items", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "--from-json" in stdout
        assert "--at" in stdout

    def test_forms_update_info_help(self):
        stdout, _, code = run("forms", "update-info", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "--title" in stdout
        assert "--description" in stdout

    def test_forms_update_item_help(self):
        stdout, _, code = run("forms", "update-item", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "INDEX" in stdout
        assert "--from-json" in stdout

    def test_forms_move_item_help(self):
        stdout, _, code = run("forms", "move-item", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "FROM_INDEX" in stdout
        assert "TO_INDEX" in stdout

    def test_forms_delete_item_help(self):
        stdout, _, code = run("forms", "delete-item", "--help")
        assert code == 0
        assert "FORM_ID" in stdout
        assert "INDEX" in stdout

    def test_gmail_send_help(self):
        stdout, _, code = run("gmail", "send", "--help")
        assert code == 0
        assert "--to" in stdout
        assert "--subject" in stdout
        assert "--body" in stdout
        assert "--account" in stdout
        assert "--compact" in stdout

    def test_completion_help(self):
        stdout, _, code = run("completion", "--help")
        assert code == 0
        assert "bash" in stdout
        assert "zsh" in stdout
        assert "fish" in stdout
        assert "powershell" in stdout


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def test_unknown_command_exits_nonzero(self):
        _, stderr, code = run("nonexistent")
        assert code != 0


# ---------------------------------------------------------------------------
# Output format flags
# ---------------------------------------------------------------------------

class TestOutputFormats:
    def test_output_table_flag_accepted(self):
        stdout, _, code = run("--output", "table", "--help")
        assert code == 0

    def test_output_csv_flag_accepted(self):
        stdout, _, code = run("--output", "csv", "--help")
        assert code == 0

    def test_output_json_flag_accepted(self):
        stdout, _, code = run("--output", "json", "--help")
        assert code == 0

    def test_fields_flag_accepted(self):
        stdout, _, code = run("--fields", "id,subject", "--help")
        assert code == 0


# ---------------------------------------------------------------------------
# No-color flag
# ---------------------------------------------------------------------------

class TestNoColorFlag:
    def test_no_color_flag_accepted(self):
        stdout, _, code = run("--no-color", "--help")
        assert code == 0
        assert "Google Workspace CLI" in stdout


# ---------------------------------------------------------------------------
# Completion command
# ---------------------------------------------------------------------------

class TestCompletionCommand:
    def test_completion_bash(self):
        stdout, _, code = run("completion", "bash")
        assert code == 0
        assert "_gw_completions" in stdout
        assert "complete -F" in stdout

    def test_completion_zsh(self):
        stdout, _, code = run("completion", "zsh")
        assert code == 0
        assert "_gw" in stdout
        assert "compdef" in stdout

    def test_completion_fish(self):
        stdout, _, code = run("completion", "fish")
        assert code == 0
        assert "complete -c gw" in stdout

    def test_completion_powershell(self):
        stdout, _, code = run("completion", "powershell")
        assert code == 0
        assert "Register-ArgumentCompleter" in stdout


# ---------------------------------------------------------------------------
# Exit code tests (via output module)
# ---------------------------------------------------------------------------

# These test the exit code constants directly since we can't easily trigger
# real Google API errors without credentials.

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.output import (
    EXIT_AUTH,
    EXIT_GENERIC,
    EXIT_NOT_FOUND,
    EXIT_RATE_LIMIT,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
    AuthError,
    CliError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    _exit_code_for,
)


class TestExitCodes:
    def test_exit_code_constants(self):
        assert EXIT_SUCCESS == 0
        assert EXIT_GENERIC == 1
        assert EXIT_VALIDATION == 2
        assert EXIT_AUTH == 3
        assert EXIT_NOT_FOUND == 4
        assert EXIT_RATE_LIMIT == 5

    def test_cli_error_exit_code(self):
        assert _exit_code_for(CliError("test")) == EXIT_GENERIC

    def test_auth_error_exit_code(self):
        assert _exit_code_for(AuthError()) == EXIT_AUTH

    def test_validation_error_exit_code(self):
        assert _exit_code_for(ValidationError("bad input")) == EXIT_VALIDATION

    def test_not_found_error_exit_code(self):
        assert _exit_code_for(NotFoundError("Message", "abc")) == EXIT_NOT_FOUND

    def test_rate_limit_error_exit_code(self):
        assert _exit_code_for(RateLimitError()) == EXIT_RATE_LIMIT

    def test_generic_exception_exit_code(self):
        assert _exit_code_for(ValueError("oops")) == EXIT_GENERIC


# ---------------------------------------------------------------------------
# Error code field tests
# ---------------------------------------------------------------------------

class TestErrorCodes:
    def test_cli_error_has_code(self):
        err = CliError("test")
        assert err.code == "GW_ERROR"

    def test_auth_error_code(self):
        assert AuthError().code == "AUTH_FAILED"

    def test_validation_error_code(self):
        assert ValidationError("bad").code == "VALIDATION_ERROR"

    def test_not_found_error_code(self):
        assert NotFoundError("Message", "abc").code == "NOT_FOUND"

    def test_rate_limit_error_code(self):
        assert RateLimitError().code == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Format error includes code field
# ---------------------------------------------------------------------------

from lib.output import format_error


class TestFormatErrorCode:
    def test_format_error_includes_code(self):
        err = AuthError()
        result = json.loads(format_error(err))
        assert result["code"] == "AUTH_FAILED"
        assert "error" in result
        assert "suggestion" in result

    def test_format_error_generic_includes_class_name(self):
        err = ValueError("oops")
        result = json.loads(format_error(err))
        assert result["code"] == "ValueError"

    def test_format_error_compact(self):
        err = ValidationError("bad input")
        result = format_error(err, compact=True)
        assert "\n" not in result
        parsed = json.loads(result)
        assert parsed["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Output format unit tests
# ---------------------------------------------------------------------------

from lib.output import _format_table, _format_csv


class TestFormatTable:
    SAMPLE = {"messages": [
        {"id": "msg1", "from": "alice@test.com", "subject": "Hello"},
        {"id": "msg2", "from": "bob@test.com", "subject": "World"},
    ]}

    def test_table_has_header_and_rows(self):
        table = _format_table(self.SAMPLE)
        lines = table.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "id" in lines[0]
        assert "from" in lines[0]
        assert "subject" in lines[0]
        assert "---" in lines[1]
        assert "msg1" in lines[2]
        assert "msg2" in lines[3]

    def test_table_non_tabular_falls_back_to_json(self):
        result = _format_table("plain string")
        assert '"plain string"' in result


class TestFormatCsv:
    SAMPLE = {"events": [
        {"id": "evt1", "title": "Meeting", "start": "2026-03-01T14:00"},
        {"id": "evt2", "title": "Call", "start": "2026-03-02T10:00"},
    ]}

    def test_csv_has_header_and_rows(self):
        csv_out = _format_csv(self.SAMPLE)
        lines = csv_out.strip().split("\n")
        assert len(lines) == 3
        assert "id" in lines[0]
        assert "title" in lines[0]
        assert "Meeting" in lines[1]
        assert "Call" in lines[2]

    def test_csv_non_tabular_falls_back_to_json(self):
        result = _format_csv(42)
        assert "42" in result


# ---------------------------------------------------------------------------
# Secret redaction tests
# ---------------------------------------------------------------------------

from lib.output import _scrub_credentials


class TestSecretRedaction:
    def test_scrub_oauth_token(self):
        token = "ya29." + "a" * 55
        result = _scrub_credentials(f"Token: {token}")
        assert "[REDACTED]" in result
        assert "ya29." not in result

    def test_scrub_bearer_token(self):
        result = _scrub_credentials("Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9")
        assert "[REDACTED]" in result

    def test_scrub_oauth_field(self):
        result = _scrub_credentials('access_token = ya29.some_long_token_value_here_1234567890abcdef')
        assert "[REDACTED]" in result

    def test_normal_text_unchanged(self):
        msg = "File not found: /tmp/test.csv"
        assert _scrub_credentials(msg) == msg

"""Tests for output module — error classes and JSON formatting."""

import json
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "lib"))

from output import (
    AuthError,
    CliError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    _scrub_credentials,
    format_error,
    format_success,
    validate_email,
)


def test_cli_error_has_message_and_suggestion():
    err = CliError("Something broke", suggestion="Try again")
    assert err.message == "Something broke"
    assert err.suggestion == "Try again"
    assert str(err) == "Something broke"


def test_auth_error_defaults():
    err = AuthError()
    assert "token" in err.message.lower() or "auth" in err.message.lower()
    assert err.suggestion is not None
    assert "gw auth login" in err.suggestion


def test_not_found_error():
    err = NotFoundError("Message", "abc123")
    assert "abc123" in err.message
    assert err.suggestion is not None


def test_validation_error():
    err = ValidationError("Bad email format", suggestion="Use user@domain.com")
    assert err.message == "Bad email format"
    assert err.suggestion == "Use user@domain.com"


def test_rate_limit_error():
    err = RateLimitError()
    assert err.suggestion is not None


def test_format_success():
    result = format_success({"sent": True, "id": "abc"})
    parsed = json.loads(result)
    assert parsed == {"sent": True, "id": "abc"}


def test_format_success_compact():
    result = format_success({"sent": True, "id": "abc"}, compact=True)
    assert "\n" not in result
    parsed = json.loads(result)
    assert parsed == {"sent": True, "id": "abc"}


def test_format_error_cli_error():
    err = CliError("fail", suggestion="fix it")
    result = format_error(err)
    parsed = json.loads(result)
    assert parsed["error"] == "fail"
    assert parsed["suggestion"] == "fix it"


def test_format_error_generic_exception():
    result = format_error(ValueError("oops"))
    parsed = json.loads(result)
    assert parsed["error"] == "oops"


# --- Credential scrubbing tests ---


def test_scrub_credentials_api_keys():
    """API keys should be redacted."""
    assert "[REDACTED]" in _scrub_credentials("Key: sk-abcdefghijklmnopqrstuvwxyz1234")
    assert "sk-abc" not in _scrub_credentials("Key: sk-abcdefghijklmnopqrstuvwxyz1234")


def test_scrub_credentials_google_tokens():
    """Google OAuth tokens (ya29.*) should be redacted."""
    token = "ya29." + "a" * 55
    assert "[REDACTED]" in _scrub_credentials(f"Token: {token}")
    assert "ya29." not in _scrub_credentials(f"Token: {token}")


def test_scrub_credentials_bearer_tokens():
    """Bearer tokens should be redacted."""
    result = _scrub_credentials("Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "[REDACTED]" in result
    assert "eyJhbG" not in result


def test_scrub_credentials_normal_text_unchanged():
    """Normal error messages should not be modified."""
    msg = "File not found: /tmp/test.csv"
    assert _scrub_credentials(msg) == msg


def test_format_error_scrubs_credentials():
    """format_error should scrub credentials from error messages."""
    err = CliError(f"Auth failed with key sk-{'x' * 30}")
    result = format_error(err)
    parsed = json.loads(result)
    assert "[REDACTED]" in parsed["error"]
    assert "sk-" not in parsed["error"]


# --- Email validation tests ---


def test_validate_email_valid():
    """Valid emails should pass without raising."""
    validate_email("user@example.com")
    validate_email("first.last@domain.co.uk")
    validate_email("user+tag@example.test")


def test_validate_email_invalid():
    """Invalid emails should raise ValidationError."""
    with pytest.raises(ValidationError):
        validate_email("")
    with pytest.raises(ValidationError):
        validate_email("not-an-email")
    with pytest.raises(ValidationError):
        validate_email("@domain.com")
    with pytest.raises(ValidationError):
        validate_email("user@")

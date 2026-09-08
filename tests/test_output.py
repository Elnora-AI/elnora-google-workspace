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


class TestScrubDoesNotEatUrls:
    """The generic base64 arm of the credential pattern also matches an ordinary
    URL path, which redacted the most useful part of a Google API error: the
    link naming the valid field names."""

    def test_documentation_url_survives(self):
        from output import _scrub_credentials
        url = "https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema"
        assert _scrub_credentials(f"see {url} for names") == f"see {url} for names"

    def test_api_key_in_a_url_is_still_redacted(self):
        from output import _scrub_credentials
        out = _scrub_credentials("https://x.com/v1?key=AIzaSyA1234567890123456789012345678901234")
        assert "AIzaSyA1234567890123456789012345678901234" not in out
        assert "[REDACTED]" in out

    def test_oauth_token_in_a_url_is_still_redacted(self):
        from output import _scrub_credentials
        out = _scrub_credentials("https://x.com/cb#access_token=ya29." + "a" * 60)
        assert "ya29." + "a" * 60 not in out

    def test_bare_base64_blob_is_still_redacted(self):
        from output import _scrub_credentials
        assert "[REDACTED]" in _scrub_credentials("token " + "A" * 50 + "==")


class TestFindDataArrayFallsBackToTheOnlyArray:
    """--output csv used to hand back JSON for every command whose collection is
    not on the hardcoded key list -- analytics properties, searchconsole sites,
    sitemaps -- without saying it had ignored the flag."""

    def test_single_unlisted_array_is_found(self):
        from output import _find_data_array
        data = {"properties": [{"property_id": "1", "display_name": "x"}], "count": 1}
        assert _find_data_array(data) == [{"property_id": "1", "display_name": "x"}]

    def test_known_key_still_wins_over_a_later_array(self):
        from output import _find_data_array
        data = {"other": [{"a": 1}], "rows": [{"b": 2}]}
        assert _find_data_array(data) == [{"b": 2}]

    def test_two_candidate_arrays_stay_ambiguous(self):
        from output import _find_data_array
        data = {"dimensions": [{"name": "date"}], "metrics": [{"name": "sessions"}]}
        assert _find_data_array(data) is None

    def test_no_array_is_still_none(self):
        from output import _find_data_array
        assert _find_data_array({"site": "x", "count": 0}) is None


class TestScrubUrlComponents:
    """Sparing whole URLs from the generic pattern would let a credential ride
    through in a path segment, so it is applied per URL component instead."""

    def test_blob_in_a_path_segment_is_redacted(self):
        from output import _scrub_credentials
        blob = "A" * 50
        out = _scrub_credentials(f"https://x.com/files/{blob}/download")
        assert blob not in out
        assert "[REDACTED]" in out
        assert out.startswith("https://x.com/files/")

    def test_blob_in_an_unnamed_query_value_is_redacted(self):
        from output import _scrub_credentials
        blob = "B" * 44
        out = _scrub_credentials(f"https://x.com/v1?t={blob}&page=2")
        assert blob not in out
        assert "page=2" in out

    def test_long_doc_path_still_survives(self):
        from output import _scrub_credentials
        url = "https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema"
        assert _scrub_credentials(url) == url

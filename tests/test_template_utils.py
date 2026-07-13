"""Tests for template_utils module — shared template loading and personalization."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import CliError
from template_utils import (
    TEMPLATES_DIR,
    load_template,
    parse_template,
    safe_format,
)


def test_parse_template_cold_outreach():
    """Test parsing the real cold-outreach-v1 template."""
    subject, body = parse_template("cold-outreach-v1")
    assert "{subject_line}" in subject
    assert "{first_name}" in body
    assert "{opening_line}" in body


def test_load_template_exists():
    content = load_template("cold-outreach-v1")
    assert "## Template" in content


def test_load_template_not_found():
    with pytest.raises(CliError, match="Template not found"):
        load_template("nonexistent-template-xyz")


def test_load_template_path_traversal():
    with pytest.raises(CliError, match="Invalid template name"):
        load_template("../../etc/passwd")


def test_load_template_backslash_traversal():
    with pytest.raises(CliError, match="Invalid template name"):
        load_template("..\\..\\etc\\passwd")


def test_safe_format_basic():
    result = safe_format("Hello {name}, from {company}", {"name": "Alice", "company": "Acme"})
    assert result == "Hello Alice, from Acme"


def test_safe_format_missing_key():
    result = safe_format("Hello {name}, {missing}", {"name": "Alice"})
    assert "[MISSING]" in result


def test_safe_format_no_attribute_access():
    """Ensure {name.__class__} is NOT expanded (security)."""
    result = safe_format("{name.__class__}", {"name": "Alice"})
    # The regex \w+ won't match name.__class__, so it stays as-is
    assert "__class__" in result


def test_safe_format_empty_template():
    result = safe_format("", {"name": "Alice"})
    assert result == ""


def test_parse_template_empty_body(tmp_path):
    """Template with empty body after Subject: line should raise CliError."""
    template_file = tmp_path / "empty-body.md"
    template_file.write_text(
        "# Empty Template\n\n## Template\n```\nSubject: Test\n\n```\n",
        encoding="utf-8",
    )
    from unittest.mock import patch
    with patch.object(
        __import__("template_utils"), "_resolve_template_path", return_value=template_file
    ):
        # Clear cache to avoid stale entries
        __import__("template_utils")._template_cache.pop("empty-body", None)
        with pytest.raises(CliError, match="empty body"):
            parse_template("empty-body")


def test_load_template_caching():
    """Repeated calls to load_template should return cached content."""
    import template_utils
    # Clear cache
    template_utils._template_cache.clear()
    content1 = load_template("cold-outreach-v1")
    content2 = load_template("cold-outreach-v1")
    assert content1 is content2  # Same object (cached)

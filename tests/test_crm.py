"""Tests for crm module — CSV operations, config caching, formula injection prevention."""

import csv
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import CliError

# Reset module-level cache before importing crm
import crm

crm._cached_config = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the module-level config cache before each test."""
    crm._cached_config = None
    yield
    crm._cached_config = None


@pytest.fixture
def tmp_crm(tmp_path):
    """Create a temporary CRM directory structure with sample CSVs."""
    vault = tmp_path / "vault" / "company" / "crm"
    vault.mkdir(parents=True)
    (vault / "campaigns").mkdir()
    (vault / "contacts").mkdir()
    (vault / "companies").mkdir()
    (vault / "templates").mkdir()

    # Create contacts.csv
    contacts = vault / "contacts.csv"
    contacts.write_text(
        "slug,name,email,company,stage\n"
        "jane-doe,Jane Doe,jane@example.test,Acme,prospect\n"
        "bob-smith,Bob Smith,bob@example.test,Corp,contacted\n",
        encoding="utf-8",
    )

    # Create a campaign CSV
    campaign = vault / "campaigns" / "test-campaign.csv"
    campaign.write_text(
        "slug,email,status,first_name,last_name\n"
        "jane-doe,jane@example.test,pending,Jane,Doe\n"
        "bob-smith,bob@example.test,sent,Bob,Smith\n",
        encoding="utf-8",
    )

    return vault


@pytest.fixture
def mock_config(tmp_crm, tmp_path):
    """Mock load_config to return paths pointing to tmp_crm."""
    config = {
        "vault_path": str(tmp_path / "vault"),
        "company_dir": "company",
        "crm_dir": "crm",
    }
    with patch.object(crm, "load_config", return_value=config):
        with patch.object(crm, "_cached_config", config):
            yield config


# ---------------------------------------------------------------------------
# _sanitize_csv_value tests
# ---------------------------------------------------------------------------


def test_sanitize_csv_value_normal():
    """Normal values pass through unchanged."""
    assert crm._sanitize_csv_value("Hello world") == "Hello world"
    assert crm._sanitize_csv_value("jane@example.test") == "jane@example.test"
    assert crm._sanitize_csv_value("") == ""


def test_sanitize_csv_value_formula_injection():
    """Dangerous prefixes get a leading single quote."""
    assert crm._sanitize_csv_value("=CMD()") == "'=CMD()"
    assert crm._sanitize_csv_value("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert crm._sanitize_csv_value("\tcmd") == "'\tcmd"
    assert crm._sanitize_csv_value("\rcmd") == "'\rcmd"
    # + and - are NOT sanitized — they appear in normal data (phone numbers, bullets)
    assert crm._sanitize_csv_value("+1+2") == "+1+2"
    assert crm._sanitize_csv_value("-1-2") == "-1-2"


# ---------------------------------------------------------------------------
# batch_update_campaign_rows tests
# ---------------------------------------------------------------------------


def test_batch_update_campaign_rows_updates_multiple(mock_config, tmp_crm):
    """Batch update should modify multiple rows in a single operation."""
    updates = {
        "jane-doe": {"status": "sent"},
        "bob-smith": {"status": "replied", "reply_sentiment": "positive"},
    }
    crm.batch_update_campaign_rows("test-campaign", updates)

    rows = crm.read_campaign_csv("test-campaign")
    jane = next(r for r in rows if r["slug"] == "jane-doe")
    bob = next(r for r in rows if r["slug"] == "bob-smith")
    assert jane["status"] == "sent"
    assert bob["status"] == "replied"
    assert bob["reply_sentiment"] == "positive"


def test_batch_update_campaign_rows_empty_noop(mock_config, tmp_crm):
    """Empty updates dict should be a no-op (no file I/O)."""
    crm.batch_update_campaign_rows("test-campaign", {})
    # Should not raise — verify file is unchanged
    rows = crm.read_campaign_csv("test-campaign")
    assert len(rows) == 2


def test_batch_update_campaign_rows_path_traversal():
    """Path traversal in campaign name should raise CliError."""
    with pytest.raises(CliError, match="Invalid campaign name"):
        crm.batch_update_campaign_rows("../etc/passwd", {"slug": {"status": "x"}})


def test_batch_update_campaign_rows_ignores_unknown_slugs(mock_config, tmp_crm):
    """Slugs not in the CSV should be silently ignored."""
    updates = {
        "jane-doe": {"status": "sent"},
        "unknown-person": {"status": "replied"},
    }
    crm.batch_update_campaign_rows("test-campaign", updates)
    rows = crm.read_campaign_csv("test-campaign")
    jane = next(r for r in rows if r["slug"] == "jane-doe")
    assert jane["status"] == "sent"
    assert not any(r["slug"] == "unknown-person" for r in rows)


# ---------------------------------------------------------------------------
# load_config caching tests
# ---------------------------------------------------------------------------


def test_load_config_caching(tmp_path):
    """Config should be cached after first successful load."""
    config_file = tmp_path / ".claude" / "knowledge-base.local.md"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        "---\nvault_path: /tmp/vault\ncompany_dir: co\ncrm_dir: crm\n---\n",
        encoding="utf-8",
    )

    crm._cached_config = None
    with patch.object(crm.gw_config, "find_kb_config", return_value=config_file):
        result1 = crm.load_config()
        result2 = crm.load_config()
        assert result1 is result2  # Same object (cached)


def test_load_config_missing_file():
    """No knowledge-base config should raise CliError."""
    crm._cached_config = None
    with patch.object(crm.gw_config, "find_kb_config", return_value=None):
        with pytest.raises(CliError, match="not configured"):
            crm.load_config()


def test_load_config_missing_key(tmp_path):
    """Missing required key should raise CliError."""
    config_file = tmp_path / "kb.md"
    config_file.write_text("---\nvault_path: /tmp/vault\n---\n", encoding="utf-8")

    crm._cached_config = None
    with patch.object(crm.gw_config, "find_kb_config", return_value=config_file):
        with pytest.raises(CliError, match="Missing"):
            crm.load_config()


# ---------------------------------------------------------------------------
# _write_csv sanitization integration test
# ---------------------------------------------------------------------------


def test_write_csv_sanitizes_values(tmp_path):
    """Values written via _write_csv should be sanitized."""
    path = tmp_path / "test.csv"
    rows = [
        {"name": "=EVIL()", "email": "ok@example.test"},
        {"name": "Normal", "email": "+safe"},
    ]
    crm._write_csv(path, rows)

    content = path.read_text(encoding="utf-8")
    assert "'=EVIL()" in content
    assert "+safe" in content  # + is not sanitized (normal in phone numbers etc.)
    assert "Normal" in content


# ---------------------------------------------------------------------------
# read_campaign_csv path traversal test
# ---------------------------------------------------------------------------


def test_read_campaign_csv_path_traversal():
    """Path traversal in campaign name should raise CliError."""
    with pytest.raises(CliError, match="Invalid campaign name"):
        crm.read_campaign_csv("../../etc/passwd")

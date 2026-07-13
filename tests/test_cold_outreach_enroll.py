"""Tests for the cold outreach agent 'enroll' command.

Tests enrollment of Apollo export contacts into campaign CSVs with
deduplication against contacts.csv and investor-contacts.csv.
"""

import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import crm

crm._cached_config = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


CAMPAIGN_COLUMNS = [
    "slug", "email", "first_name", "last_name", "company", "role",
    "status", "step", "sequence", "next_send_at",
    "step1_message_id", "step1_sent_at",
    "step2_message_id", "step2_sent_at",
    "step3_message_id", "step3_sent_at",
    "reply_date", "reply_sentiment", "reply_snippet", "batch", "notes",
]


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset CRM config cache between tests."""
    crm._cached_config = None
    yield
    crm._cached_config = None


@pytest.fixture
def tmp_crm(tmp_path):
    """Create a temporary CRM with contacts, investor-contacts, and sequences."""
    vault = tmp_path / "vault" / "company" / "crm"
    vault.mkdir(parents=True)
    (vault / "campaigns").mkdir()
    (vault / "contacts").mkdir()
    (vault / "companies").mkdir()
    (vault / "templates").mkdir()

    # contacts.csv — existing contacts (dedup source)
    contacts = vault / "contacts.csv"
    contacts.write_text(
        "slug,first_name,last_name,email,company,role,stage\n"
        "existing-contact,Jane,Doe,jane@acme.com,Acme Corp,VP R&D,contacted\n",
        encoding="utf-8",
    )

    # investor-contacts.csv — do_not_email list
    investor_contacts = vault / "investor-contacts.csv"
    investor_contacts.write_text(
        "slug,first_name,last_name,email,do_not_email,linkedin_url,twitter,fund_slug,role,is_primary_contact,notes\n"
        "investor-1,Bob,Investor,bob@fund.com,true,,,fund-1,Partner,true,\n"
        "investor-2,Alice,Friendly,alice@fund.com,false,,,fund-2,MD,true,\n",
        encoding="utf-8",
    )

    # Sequence config
    seq_dir = vault / "templates" / "sequences" / "cold-outreach-pharma-vp"
    seq_dir.mkdir(parents=True)
    (seq_dir / "sequence.json").write_text(json.dumps({
        "name": "cold-outreach-pharma-vp",
        "description": "test",
        "steps": [
            {"step": 1, "template": "step-1-intro.md", "delay_days": 0},
            {"step": 2, "template": "step-2-follow-up.md", "delay_days": 3},
            {"step": 3, "template": "step-3-breakup.md", "delay_days": 5},
        ],
        "send_window": {"start_hour": 8, "end_hour": 11, "timezone": "America/New_York"},
        "max_per_day": 30,
        "from_account": "mail",
    }), encoding="utf-8")

    return vault


@pytest.fixture
def mock_config(tmp_crm, tmp_path):
    """Mock CRM config to point to tmp_crm."""
    config = {
        "vault_path": str(tmp_path / "vault"),
        "company_dir": "company",
        "crm_dir": "crm",
    }
    with patch.object(crm, "load_config", return_value=config):
        with patch.object(crm, "_cached_config", config):
            yield config


@pytest.fixture
def apollo_json_file(tmp_path):
    """Create a test Apollo JSON export with 3 contacts."""
    contacts = [
        {
            "first_name": "Sarah",
            "last_name": "Chen",
            "email": "sarah@pharma.com",
            "organization_name": "Big Pharma Inc",
            "title": "VP of R&D",
        },
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@acme.com",  # Already in contacts.csv
            "organization_name": "Acme Corp",
            "title": "VP R&D",
        },
        {
            "first_name": "Bob",
            "last_name": "Investor",
            "email": "bob@fund.com",  # do_not_email in investor-contacts
            "organization_name": "Fund Co",
            "title": "Partner",
        },
    ]
    path = tmp_path / "apollo-export.json"
    path.write_text(json.dumps(contacts), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Import the enroll helpers (after sys.path setup)
# ---------------------------------------------------------------------------

# We import the agent module to test its internal functions.
# The agent file adds lib/ to sys.path itself, so we just need agents/ on path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from cold_outreach_agent import _load_apollo_contacts, _build_dedup_sets, _enroll_contacts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadApolloContacts:
    """Test loading contacts from Apollo JSON export."""

    def test_loads_json_contacts(self, apollo_json_file):
        contacts = _load_apollo_contacts(str(apollo_json_file))
        assert len(contacts) == 3
        assert contacts[0]["first_name"] == "Sarah"
        assert contacts[0]["email"] == "sarah@pharma.com"

    def test_skips_contacts_without_email(self, tmp_path):
        data = [
            {"first_name": "NoEmail", "last_name": "Person", "email": "", "organization_name": "Co", "title": "VP"},
            {"first_name": "Has", "last_name": "Email", "email": "has@email.com", "organization_name": "Co", "title": "VP"},
        ]
        path = tmp_path / "test.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        contacts = _load_apollo_contacts(str(path))
        assert len(contacts) == 1
        assert contacts[0]["email"] == "has@email.com"


class TestBuildDedupSets:
    """Test dedup set construction from contacts.csv and investor-contacts.csv."""

    def test_builds_dedup_sets(self, mock_config, tmp_crm):
        existing_emails, dne_emails = _build_dedup_sets()
        assert "jane@acme.com" in existing_emails
        assert "bob@fund.com" in dne_emails
        # alice@fund.com has do_not_email=false, should NOT be in dne set
        assert "alice@fund.com" not in dne_emails


class TestEnrollContacts:
    """Test the enrollment logic — dedup, slug generation, CSV creation."""

    def test_creates_campaign_csv(self, mock_config, tmp_crm, apollo_json_file):
        apollo_contacts = _load_apollo_contacts(str(apollo_json_file))
        existing_emails, dne_emails = _build_dedup_sets()

        result = _enroll_contacts(
            contacts=apollo_contacts,
            campaign_name="test-enroll",
            sequence_name="cold-outreach-pharma-vp",
            existing_emails=existing_emails,
            dne_emails=dne_emails,
            dry_run=False,
        )

        assert result["enrolled"] == 1  # Only Sarah — Jane is dupe, Bob is dne
        assert result["skipped_existing"] == 1  # Jane
        assert result["skipped_dne"] == 1  # Bob

        # Verify the CSV was created
        rows = crm.read_campaign_csv("test-enroll")
        assert len(rows) == 1
        assert rows[0]["email"] == "sarah@pharma.com"
        assert rows[0]["first_name"] == "Sarah"
        assert rows[0]["status"] == "scheduled"
        assert rows[0]["step"] == "0"
        assert rows[0]["sequence"] == "cold-outreach-pharma-vp"

    def test_dry_run_does_not_create_file(self, mock_config, tmp_crm, apollo_json_file):
        apollo_contacts = _load_apollo_contacts(str(apollo_json_file))
        existing_emails, dne_emails = _build_dedup_sets()

        result = _enroll_contacts(
            contacts=apollo_contacts,
            campaign_name="test-dry-run",
            sequence_name="cold-outreach-pharma-vp",
            existing_emails=existing_emails,
            dne_emails=dne_emails,
            dry_run=True,
        )

        assert result["enrolled"] == 1
        assert result["dry_run"] is True

        # CSV should NOT exist
        csv_path = crm.campaigns_dir() / "test-dry-run.csv"
        assert not csv_path.exists()

    def test_appends_to_existing_campaign(self, mock_config, tmp_crm):
        # Create existing campaign CSV
        existing_csv = crm.campaigns_dir() / "append-test.csv"
        existing_csv.write_text(
            ",".join(CAMPAIGN_COLUMNS) + "\n"
            "old-contact,old@example.com,Old,Contact,OldCo,VP,scheduled,0,cold-outreach-pharma-vp,2026-03-03T15:00:00Z,,,,,,,,,,batch1,existing\n",
            encoding="utf-8",
        )

        new_contacts = [
            {
                "first_name": "New",
                "last_name": "Person",
                "email": "new@example.com",
                "organization_name": "NewCo",
                "title": "Director",
            },
        ]

        result = _enroll_contacts(
            contacts=new_contacts,
            campaign_name="append-test",
            sequence_name="cold-outreach-pharma-vp",
            existing_emails=set(),
            dne_emails=set(),
            dry_run=False,
        )

        assert result["enrolled"] == 1

        rows = crm.read_campaign_csv("append-test")
        assert len(rows) == 2  # Old + new
        assert rows[0]["slug"] == "old-contact"
        assert rows[1]["email"] == "new@example.com"

    def test_dedup_within_campaign(self, mock_config, tmp_crm):
        """Contacts already in the campaign CSV should be skipped."""
        existing_csv = crm.campaigns_dir() / "dedup-test.csv"
        existing_csv.write_text(
            ",".join(CAMPAIGN_COLUMNS) + "\n"
            "sarah-chen,sarah@pharma.com,Sarah,Chen,Big Pharma Inc,VP of R&D,scheduled,0,cold-outreach-pharma-vp,2026-03-03T15:00:00Z,,,,,,,,,,batch1,\n",
            encoding="utf-8",
        )

        new_contacts = [
            {
                "first_name": "Sarah",
                "last_name": "Chen",
                "email": "sarah@pharma.com",
                "organization_name": "Big Pharma Inc",
                "title": "VP of R&D",
            },
        ]

        result = _enroll_contacts(
            contacts=new_contacts,
            campaign_name="dedup-test",
            sequence_name="cold-outreach-pharma-vp",
            existing_emails=set(),
            dne_emails=set(),
            dry_run=False,
        )

        assert result["enrolled"] == 0
        assert result["skipped_existing"] == 1  # Already in campaign

        rows = crm.read_campaign_csv("dedup-test")
        assert len(rows) == 1  # Not duplicated

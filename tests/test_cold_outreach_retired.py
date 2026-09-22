"""The cold outreach sender is retired: nothing in the agent may send or write.

``run``, ``scan`` and ``enroll`` refuse with a non-zero exit, ``send_outreach_email``
raises before any Gmail call, and ``status`` stays read-only.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

import cold_outreach_agent as agent
import crm
import output as output_mod
from output import CliError


@pytest.fixture(autouse=True)
def reset_config_cache():
    crm._cached_config = None
    yield
    crm._cached_config = None


@pytest.fixture
def gmail_mocks():
    """Mock every Gmail call the agent could make, so a leak shows as a call."""
    with patch.object(agent.gmail, "send") as send, \
         patch.object(agent.gmail, "draft") as draft:
        yield send, draft


def _invoke(args):
    """Run the agent CLI, capturing the JSON it writes to stdout and stderr."""
    out: list[str] = []
    err: list[str] = []
    with patch.object(output_mod, "_write_stdout", side_effect=out.append), \
         patch.object(output_mod, "_write_stderr", side_effect=err.append):
        result = CliRunner().invoke(agent.cli, args, catch_exceptions=False)
    return result, "".join(out), "".join(err)


@pytest.mark.parametrize("args", [
    ["run"],
    ["run", "--campaign", "demo", "--batch-size", "5"],
    ["run", "--help"],
    ["scan", "--since", "1d"],
    ["scan", "--campaign", "demo"],
    ["enroll"],
    ["enroll", "--campaign", "demo", "--sequence", "seq", "--input", "contacts.json"],
])
def test_retired_verbs_refuse(args, gmail_mocks):
    """Each retired verb exits non-zero, points at the changelog, and never sends."""
    send, draft = gmail_mocks
    result, _, err = _invoke(args)

    assert result.exit_code != 0
    assert "retired with the sender in 1.3.2" in err
    assert "CHANGELOG.md" in err
    send.assert_not_called()
    draft.assert_not_called()


@pytest.mark.parametrize("as_draft", [False, True])
def test_send_outreach_email_raises(as_draft, gmail_mocks):
    """Calling the send function directly raises before any Gmail call."""
    send, draft = gmail_mocks
    contact = {"email": "jane@example.com", "name": "Jane Doe"}

    with pytest.raises(CliError, match="retired with the sender"):
        agent.send_outreach_email(contact, "Subject", "Body", as_draft=as_draft)

    send.assert_not_called()
    draft.assert_not_called()


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_status_writes_nothing(tmp_path, gmail_mocks):
    """status reads the CRM and a campaign CSV and leaves the vault byte-for-byte unchanged."""
    send, draft = gmail_mocks
    vault = tmp_path / "vault"
    crm_dir = vault / "crm"
    (crm_dir / "campaigns").mkdir(parents=True)
    (crm_dir / "contacts.csv").write_text(
        "slug,first_name,last_name,email,company,role,stage\n"
        "jane-doe,Jane,Doe,jane@acme.com,Acme,VP,contacted\n"
        "john-roe,John,Roe,john@globex.com,Globex,CTO,lead\n",
        encoding="utf-8",
    )
    (crm_dir / "campaigns" / "demo.csv").write_text(
        "slug,email,first_name,last_name,status,batch\n"
        "jane-doe,jane@acme.com,Jane,Doe,sent,1\n"
        "john-roe,john@globex.com,John,Roe,pending,\n",
        encoding="utf-8",
    )
    config = {"vault_path": str(vault), "company_dir": "", "crm_dir": "crm"}
    before = _snapshot(vault)

    with patch.object(crm, "load_config", return_value=config):
        for args in (["status"], ["status", "--campaign", "demo"],
                     ["status", "--campaign", "demo", "--compact"]):
            result, out, _ = _invoke(args)
            assert result.exit_code == 0, result.output
            assert '"total"' in out

    assert _snapshot(vault) == before
    send.assert_not_called()
    draft.assert_not_called()

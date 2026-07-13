#!/usr/bin/env python3
"""Pre-meeting brief — summarize who you're about to meet.

Part of the OPTIONAL knowledge-base connector. Pulls a calendar event, matches
external attendees against the knowledge base CRM (contacts.csv / companies.csv),
optionally scans recent meeting transcripts, and prints 3-5 bullet points. When
``GW_SLACK_USER_ID`` and ``GW_SLACK_CLI_BIN`` are set, it also DMs the brief on
Slack; otherwise it prints to stdout.

Requires a knowledge base (see gw_config). With none configured it no-ops with a
clear message. Usage:

    python3 cli/prep_meeting.py <event-id> [--no-slack]
    python3 cli/prep_meeting.py <event-title-substring> [--no-slack]

If the argument doesn't look like an event ID (long opaque string), it is treated
as a title substring and the next 3 days of calendar events are searched.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))

import crm  # type: ignore[import-not-found]
import gw_config  # type: ignore[import-not-found]
from gw_config import parse_frontmatter  # type: ignore[import-not-found]


def _kb_config() -> dict:
    path = gw_config.find_kb_config()
    if path is None:
        return {}
    return gw_config.parse_frontmatter(path.read_text("utf-8"))


def _vault_company() -> Path:
    cfg = _kb_config()
    return Path(cfg["vault_path"]) / cfg["company_dir"]


# ---------------------------------------------------------------------------
# Calendar event lookup
# ---------------------------------------------------------------------------

def _resolve_event(arg: str) -> dict | None:
    """If arg is an event id (>20 chars, alnum), fetch directly. Else search title."""
    import calendar_ops  # type: ignore[import-not-found]

    if re.match(r"^[a-z0-9]{20,}$", arg):
        try:
            svc = calendar_ops._get_service(None)
            ev = svc.events().get(calendarId="primary", eventId=arg).execute()
            return ev
        except Exception:
            pass

    result = calendar_ops.list_events(days=3)
    for ev in result.get("events", []):
        if arg.lower() in ev.get("title", "").lower():
            try:
                svc = calendar_ops._get_service(None)
                full = svc.events().get(calendarId="primary", eventId=ev["id"]).execute()
                return full
            except Exception:
                return ev
    return None


# ---------------------------------------------------------------------------
# CRM lookup
# ---------------------------------------------------------------------------

def _load_crm_index() -> tuple[dict[str, dict], dict[str, str]]:
    """email → contact-row, company-name-lower → company-slug."""
    contacts: dict[str, dict] = {}
    companies: dict[str, str] = {}
    contacts_csv = crm.contacts_csv_path()
    companies_csv = crm.companies_csv_path()
    if contacts_csv.exists():
        with open(contacts_csv, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                email = r.get("email", "").strip().lower()
                if email:
                    contacts[email] = r
    if companies_csv.exists():
        with open(companies_csv, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                name = r.get("name", "").strip().lower()
                slug = r.get("slug", "").strip()
                if name and slug:
                    companies[name] = slug
    return contacts, companies


# ---------------------------------------------------------------------------
# Transcript hunt (optional — configured via GW_TRANSCRIPT_DIRS)
# ---------------------------------------------------------------------------

def _find_recent_transcripts(emails: list[str], limit: int = 2) -> list[Path]:
    """Find the most recent transcripts (in GW_TRANSCRIPT_DIRS) mentioning any email."""
    dirs = gw_config.transcript_dirs()
    if not emails or not dirs:
        return []
    company = _vault_company()
    candidates: list[tuple[Path, float]] = []
    for sub in dirs:
        d = Path(sub) if Path(sub).is_absolute() else company / sub
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                content = p.read_text("utf-8")
                if any(email in content.lower() for email in emails):
                    candidates.append((p, p.stat().st_mtime))
            except OSError:
                continue
    candidates.sort(key=lambda x: -x[1])
    return [p for p, _ in candidates[:limit]]


def _summarize_transcript(path: Path, max_chars: int = 240) -> str:
    """Pull the description line from frontmatter or the first 240 chars of body."""
    try:
        content = path.read_text("utf-8")
        fm = parse_frontmatter(content)
        desc = (fm.get("description") or "").strip()
        if desc:
            return desc[:max_chars]
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
        for line in body.splitlines()[:30]:
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:max_chars]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------

def build_brief(event: dict) -> dict:
    """Return a dict with summary, attendees, transcripts, customer_notes."""
    title = event.get("summary") or event.get("title") or "(untitled event)"
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date") or ""
    location = event.get("location") or ""
    internal = gw_config.internal_domains()

    attendees_raw = event.get("attendees") or []
    if attendees_raw and isinstance(attendees_raw[0], dict):
        attendee_emails = [a.get("email", "").lower() for a in attendees_raw if a.get("email")]
    else:
        attendee_emails = [e.lower() for e in attendees_raw if e and "@" in str(e)]
    external = [e for e in attendee_emails
                if e and e.split("@", 1)[-1] not in internal]

    contacts_idx, companies_idx = _load_crm_index()

    matched_contacts: list[dict] = []
    customer_files: list[Path] = []
    company = _vault_company()
    for email in external:
        contact = contacts_idx.get(email)
        if contact:
            matched_contacts.append(contact)
            company_name = (contact.get("company") or "").strip()
            company_slug = companies_idx.get(company_name.lower(), "")
            if company_slug:
                cf = crm.companies_dir() / f"{company_slug}.md"
                if cf.exists():
                    customer_files.append(cf)

    transcript_paths = _find_recent_transcripts(external, limit=2)

    return {
        "title": title,
        "start": start,
        "location": location,
        "attendees": external,
        "contacts": matched_contacts,
        "customer_files": [str(p.relative_to(company)) for p in customer_files],
        "transcripts": [
            {
                "path": str(p.relative_to(company)) if str(p).startswith(str(company)) else p.name,
                "summary": _summarize_transcript(p),
            }
            for p in transcript_paths
        ],
    }


def format_brief(brief: dict) -> str:
    """Compress the brief to 3-5 bullets — chat-friendly, not full-page."""
    lines: list[str] = []
    title = brief["title"]
    when = ""
    if brief["start"]:
        try:
            dt = datetime.fromisoformat(brief["start"].replace("Z", "+00:00"))
            when = dt.astimezone().strftime("%a %b %d, %H:%M")
        except (ValueError, TypeError):
            when = brief["start"]
    lines.append(f"Pre-meeting brief: {title}")
    if when:
        lines.append(f"{when}" + (f" / {brief['location']}" if brief["location"] else ""))

    if not brief["contacts"]:
        lines.append("")
        lines.append("No CRM contact match for the external attendees.")
        if brief["attendees"]:
            lines.append(f"External attendees on event: {', '.join(brief['attendees'][:5])}")
        return "\n".join(lines)

    primary = brief["contacts"][0]
    name = f"{primary.get('first_name','')} {primary.get('last_name','')}".strip()
    company = primary.get("company", "")
    role = primary.get("role", "")
    last_contact = primary.get("last_contact_date", "-")
    last_channel = primary.get("last_contact_channel", "")
    next_action = primary.get("next_action", "").strip()
    next_action_date = primary.get("next_action_date", "").strip()

    lines.append("")
    role_str = f", {role}" if role else ""
    lines.append(f"Who: {name}{role_str} ({company})")
    if last_contact and last_contact != "-":
        lines.append(f"Last touch: {last_contact} ({last_channel or 'unknown channel'})")
    if next_action:
        date_part = f", due {next_action_date}" if next_action_date else ""
        lines.append(f"Next action: {next_action.replace('_', ' ')}{date_part}")

    if len(brief["contacts"]) > 1:
        others = ", ".join(
            f"{c.get('first_name','')} {c.get('last_name','')}".strip()
            for c in brief["contacts"][1:5]
        )
        lines.append(f"Also on call: {others}")

    if brief["customer_files"]:
        lines.append(f"Customer file: {brief['customer_files'][0]}")

    if brief["transcripts"]:
        lines.append("")
        lines.append("Recent transcripts:")
        for t in brief["transcripts"]:
            sum_short = t["summary"][:140] + ("..." if len(t["summary"]) > 140 else "")
            lines.append(f"- {t['path'].split('/')[-1]}: {sum_short}")

    return "\n".join(lines)


def send_slack_dm(text: str) -> bool:
    """DM the brief on Slack, if GW_SLACK_USER_ID + GW_SLACK_CLI_BIN are configured."""
    user_id = gw_config.slack_user_id()
    cli_bin = gw_config.slack_cli_bin()
    if not user_id or not cli_bin or not cli_bin.exists():
        return False
    try:
        open_proc = subprocess.run([
            "node", str(cli_bin),
            "conversations", "open",
            "--users", user_id,
            "--return-im",
        ], capture_output=True, text=True, timeout=10)
        try:
            channel_id = json.loads(open_proc.stdout)["channel"]["id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return False
        result = subprocess.run([
            "node", str(cli_bin),
            "chat", "postMessage",
            "--channel", channel_id,
            "--text", text,
        ], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.argument("event_arg")
@click.option("--no-slack", is_flag=True, help="Print to stdout only, don't DM Slack")
@click.option("--compact", is_flag=True, help="Print JSON brief")
def cli(event_arg: str, no_slack: bool, compact: bool) -> None:
    """Pre-meeting brief: look up attendees in CRM + transcripts, then print/DM it."""
    if not gw_config.kb_configured():
        click.echo(click.style(gw_config.KB_NOT_CONFIGURED, fg="yellow"))
        return

    event = _resolve_event(event_arg)
    if not event:
        click.echo(click.style(f"ERROR: event not found for '{event_arg}'", fg="red"))
        sys.exit(1)

    brief = build_brief(event)
    if compact:
        click.echo(json.dumps(brief, indent=2))
        return

    text = format_brief(brief)

    click.echo(text)
    click.echo()
    if no_slack:
        click.echo(click.style("(--no-slack set; not posting to Slack)", fg="cyan"))
        return

    if send_slack_dm(text):
        click.echo(click.style("Slack DM sent", fg="green"))
    else:
        click.echo(click.style("Slack DM not sent (printed above)", fg="yellow"))


if __name__ == "__main__":
    cli()

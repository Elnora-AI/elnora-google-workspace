"""Cold outreach campaign agent — send, scan, and track outreach emails.

Three modes:
  run     Send outreach emails from contacts (vault CSV or Google Sheets)
  scan    Monitor inbox for replies and update statuses
  status  Show campaign statistics

Contact sources:
  vault   Read/write contacts from vault CRM CSV (default, recommended)
  sheets  Read/write contacts from Google Sheets (legacy, backward compat)

Uses Gmail for sending/scanning and vault CRM for contact management.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

# Add lib/ to sys.path so direct imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import click

import crm
import gmail
import sheets
from output import AuthError, CliError, RateLimitError, output_error, output_success
from reward import (
    SENTIMENT_TO_CRM_STAGE,
    UNSUBSCRIBE_KEYWORDS,
    classify_reply,
)
from template_utils import parse_template, safe_format

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BATCH_SIZE = 50  # Hard cap per invocation — matches SKILL.md rate limit

# Column names we look for in the header row (case-insensitive)
EXPECTED_COLUMNS = ["name", "email", "company", "role", "status", "sent_date", "reply_date", "notes", "campaign"]


def _validate_account_opt(ctx, param, value):
    """Click callback: accept any config-driven account name, but validate it
    the same way the auth layer does (rejects path-traversal names)."""
    if value is None:
        return value
    import auth
    try:
        return auth._validate_account_name(value)
    except AuthError as err:
        raise click.BadParameter(err.message)


# Retry settings for transient API errors
MAX_RETRIES = 3
RETRY_BASE_DELAY = 30  # seconds — matches Google rate-limit guidance

# Default personalization fills, used only when a template references
# {value_prop} or {cta} but neither the contact nor the template supplies them.
# Set your own copy via GW_OUTREACH_VALUE_PROP / GW_OUTREACH_CTA. Empty by
# default — provide these in your template or env before sending real outreach.
DEFAULT_VALUE_PROP = os.environ.get("GW_OUTREACH_VALUE_PROP", "")
DEFAULT_CTA = os.environ.get("GW_OUTREACH_CTA", "")


def _default_subject_line(company: str) -> str:
    return f"Quick note for {company}" if company else "Quick note"


def _default_opening_line(company: str) -> str:
    if company:
        return f"I came across {company} and wanted to reach out."
    return "I wanted to reach out about something that may be relevant to your team."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send_with_retry(
    contact: dict,
    subject: str,
    body: str,
    account: str | None,
    as_draft: bool = False,
) -> dict:
    """Send or draft an email with retry on transient errors.

    Retries on RateLimitError with exponential backoff.
    Re-raises AuthError immediately (fatal — token is dead).
    Other CliErrors are retried once, then raised as non-fatal.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            if as_draft:
                result = gmail.draft(to=contact["email"], subject=subject, body=body, account=account)
                return {"action": "drafted", "result": result}
            else:
                result = gmail.send(to=contact["email"], subject=subject, body=body, account=account)
                return {"action": "sent", "result": result}
        except AuthError:
            raise  # Fatal — no point retrying with a dead token
        except RateLimitError as exc:
            last_exc = exc
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            click.echo(f"  Rate limited — waiting {delay}s before retry {attempt + 1}/{MAX_RETRIES}...", err=True)
            time.sleep(delay)
        except CliError as exc:
            # Other API errors (5xx, permission, validation) — retry once
            if attempt == 0:
                last_exc = exc
                click.echo(f"  API error — retrying in {RETRY_BASE_DELAY}s...", err=True)
                time.sleep(RETRY_BASE_DELAY)
            else:
                raise
    # All retries exhausted
    if last_exc is None:
        raise CliError("All retries exhausted with no recorded exception")
    raise last_exc


def _find_columns(header_row: list[str]) -> dict[str, int]:
    """Map expected column names to their indices in the header row."""
    lower_header = [h.strip().lower().replace(" ", "_") for h in header_row]
    col_map: dict[str, int] = {}
    for col in EXPECTED_COLUMNS:
        try:
            col_map[col] = lower_header.index(col)
        except ValueError:
            pass
    return col_map


def _row_to_contact(row: list[str], col_map: dict[str, int]) -> dict:
    """Convert a sheet row to a contact dict using the column map."""

    def _get(col: str) -> str:
        idx = col_map.get(col)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    return {
        "name": _get("name"),
        "email": _get("email"),
        "company": _get("company"),
        "role": _get("role"),
        "status": _get("status").lower() or "pending",
        "sent_date": _get("sent_date"),
        "notes": _get("notes"),
        "campaign": _get("campaign"),
    }


def _first_name(full_name: str) -> str:
    """Extract the first name from a full name."""
    return full_name.split()[0] if full_name.strip() else ""


def _personalize_v1(
    subject_template: str,
    body_template: str,
    contact: dict,
) -> tuple[str, str]:
    """Fill template placeholders with contact data (v1: simple string format)."""
    first = _first_name(contact.get("name", ""))
    company = contact.get("company", "")
    role = contact.get("role", "")

    # Guard against empty first name — would produce "Hi ," greeting
    if not first:
        raise CliError(
            f"Contact {contact.get('email', '?')} has no name — cannot personalize",
            suggestion="Add a name to this contact or skip them.",
        )

    # Build default fills for v1
    fills = {
        "first_name": first,
        "company": company,
        "role": role,
        "subject_line": _default_subject_line(company),
        "opening_line": _default_opening_line(company),
        "value_prop": DEFAULT_VALUE_PROP,
        "cta": DEFAULT_CTA,
    }

    try:
        subject = safe_format(subject_template, fills)
        body = safe_format(body_template, fills)
    except (ValueError, IndexError) as e:
        raise CliError(
            f"Template formatting error: {e}",
            suggestion="Check template for invalid placeholder syntax (e.g., nested braces).",
        )
    # Guard against unresolved placeholders reaching actual emails
    if "[MISSING]" in subject or "[MISSING]" in body:
        raise CliError(
            f"Template has unresolved placeholders for contact {contact.get('email', '?')}",
            suggestion="Ensure template only uses: first_name, company, role, subject_line, opening_line, value_prop, cta",
        )
    return subject, body


def _col_to_letter(n: int) -> str:
    """Convert 0-based column index to A1 notation letter (A, B, ..., Z, AA, AB, ...)."""
    result = ""
    n += 1  # make 1-based
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(r + ord("A")) + result
    return result


def _update_sheet_cell(
    sheet_id: str,
    row_index: int,
    col_index: int,
    value: str,
    account: str | None,
) -> None:
    """Update a single cell in the contact sheet (1-indexed row in A1 notation)."""
    col_letter = _col_to_letter(col_index)
    cell_range = f"Sheet1!{col_letter}{row_index}"
    sheets.write(sheet_id, range=cell_range, value=value, account=account)


# ---------------------------------------------------------------------------
# Vault CRM helpers
# ---------------------------------------------------------------------------


def _assemble_name(row: dict[str, str]) -> str:
    """Build full name from first_name + last_name CSV fields."""
    return f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()


def _vault_csv_to_contact(row: dict[str, str]) -> dict:
    """Convert a vault CSV row (dict) to the contact dict format used by the agent."""
    name = _assemble_name(row)
    return {
        "slug": row.get("slug", ""),
        "name": name,
        "email": row.get("email", ""),
        "company": row.get("company", ""),
        "role": row.get("role", ""),
        "status": row.get("stage", "lead"),
        "sent_date": row.get("last_contact_date", ""),
        "notes": row.get("notes", ""),
        "campaign": row.get("campaign", ""),
    }


def _load_vault_contacts(stage_filter: str | None = None) -> list[dict]:
    """Load contacts from vault CRM CSV, optionally filtered by stage."""
    csv_rows = crm.read_contacts_csv()
    contacts = [_vault_csv_to_contact(row) for row in csv_rows]
    if stage_filter:
        contacts = [c for c in contacts if c["status"] == stage_filter]
    return contacts


def _update_vault_contact(slug: str, updates: dict[str, str]) -> None:
    """Update a contact in the vault CRM CSV."""
    crm.update_contact(slug, updates)


# ---------------------------------------------------------------------------
# Campaign CSV helpers
# ---------------------------------------------------------------------------


def _campaign_csv_to_contact(row: dict[str, str]) -> dict:
    """Convert a campaign CSV row (dict) to the contact dict format."""
    name = _assemble_name(row)
    return {
        "slug": row.get("slug", ""),
        "name": name,
        "email": row.get("email", ""),
        "company": row.get("fund", ""),  # Campaign CSV uses 'fund' not 'company'
        "role": "",
        "status": row.get("status", "pending"),
        "sent_at": row.get("sent_at", ""),
        "message_id": row.get("message_id", ""),
        "reply_sentiment": row.get("reply_sentiment", ""),
        "reply_snippet": row.get("reply_snippet", ""),
        "batch": row.get("batch", ""),
    }


def _parse_template_goal(template_name: str | None = None, content: str | None = None) -> str:
    """Extract campaign goal from template frontmatter (e.g. 'rsvp', 'demo_booked').

    Accepts either a template name (loads from disk) or pre-loaded content
    to avoid duplicate file reads when parse_template was already called.
    """
    if content is None:
        if template_name is None:
            return ""
        from template_utils import load_template
        content = load_template(template_name)
    match = re.search(r"^goal:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Core function — structured for future @agl.rollout wrapping
# ---------------------------------------------------------------------------


def send_outreach_email(
    contact: dict,
    subject: str,
    body: str,
    as_draft: bool,
    account: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Send or draft a single outreach email.

    Uses _send_with_retry for automatic retry on transient errors.

    Returns:
        dict with keys: action ("sent"|"drafted"|"dry_run"), result (API response or None)
    """
    if dry_run:
        return {"action": "dry_run", "result": None}

    return _send_with_retry(contact, subject, body, account, as_draft=as_draft)


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


@click.group()
def cli():
    """Cold outreach campaign agent."""
    pass


@cli.command()
@click.option("--source", default="vault", type=click.Choice(["vault", "sheets"]), help="Contact source: vault CRM CSV (default) or Google Sheets")
@click.option("--sheet", default=None, help="Google Sheets spreadsheet ID (required if --source=sheets)")
@click.option("--campaign", default=None, help="Campaign CSV name (e.g. q3-outreach). Overrides --source.")
@click.option("--stage", default="lead", help="Filter vault contacts by stage (default: lead). Only used with --source=vault")
@click.option("--template", default="cold-outreach-v1", help="Template name (without .md)")
@click.option("--account", default="mail", type=str, callback=_validate_account_opt, help="Google account name from accounts.json or a legacy token (default: mail for outreach)")
@click.option("--batch-size", default=25, type=click.IntRange(1, 50), help="Max emails per run (default: 25, max: 50)")
@click.option("--draft-first", default=None, type=click.IntRange(1, 50), help="Draft first N contacts for review, then send remaining directly (default: all drafts)")
@click.option("--dry-run", is_flag=True, help="Print what would be sent without sending (drafts first 3 for review)")
@click.option("--compact", is_flag=True, help="Compact JSON output (saves tokens)")
def run(source: str, sheet: str | None, campaign: str | None, stage: str, template: str, account: str | None, batch_size: int, draft_first: int | None, dry_run: bool, compact: bool):
    """Send outreach emails from contacts (vault CSV, Google Sheets, or campaign CSV)."""
    try:
        # Campaign mode — uses campaign CSV directly
        if campaign:
            _run_campaign(campaign, template, account, batch_size, dry_run, compact)
            return

        # Load template
        subject_template, body_template = parse_template(template)

        # Load contacts from source
        if source == "vault":
            contacts = _load_vault_contacts(stage_filter=stage)
            if not contacts:
                output_success({"sent": 0, "drafted": 0, "skipped": 0, "errors": 0, "message": f"No contacts with stage '{stage}' found"})
                return
        else:
            # Legacy sheets mode
            if not sheet:
                raise CliError(
                    "Missing --sheet option",
                    suggestion="Provide a Google Sheets spreadsheet ID with --source=sheets --sheet=ID",
                )
            data = sheets.read(sheet, range="Sheet1", account=account)
            rows = data.get("rows", [])
            if len(rows) < 2:
                output_success({"sent": 0, "drafted": 0, "skipped": 0, "errors": 0, "message": "No data rows found"})
                return
            header = rows[0]
            col_map = _find_columns(header)
            for required in ["name", "email", "company", "status"]:
                if required not in col_map:
                    raise CliError(
                        f"Missing required column: '{required}' in sheet header",
                        suggestion=f"Expected columns: {', '.join(EXPECTED_COLUMNS)}",
                    )
            contacts = [_row_to_contact(row, col_map) for row in rows[1:]]

        # Track results
        sent_count = 0
        drafted_count = 0
        skipped_count = 0
        error_count = 0
        pending_index = 0
        vault_updates: dict[str, dict[str, str]] = {}  # slug → updates (batch write at end)

        for row_idx, contact in enumerate(contacts, start=2):
            # For vault source, contacts are already filtered by stage
            # For sheets source, filter by "pending" status
            if source == "sheets" and contact["status"] != "pending":
                skipped_count += 1
                continue

            # Skip contacts without email
            if not contact.get("email"):
                skipped_count += 1
                continue

            try:
                # Personalize the email
                subject, body = _personalize_v1(subject_template, body_template, contact)

                # Decide: draft vs send (default: all drafts unless --draft-first set)
                as_draft = True if draft_first is None else pending_index < draft_first

                # Send or draft
                outcome = send_outreach_email(
                    contact=contact,
                    subject=subject,
                    body=body,
                    as_draft=as_draft,
                    account=account,
                    dry_run=dry_run,
                )

                action = outcome["action"]

                if dry_run:
                    click.echo(
                        f"[DRY RUN] Would {'draft' if as_draft else 'send'} to "
                        f"{contact['name']} <{contact['email']}>: {subject}",
                        err=True,
                    )
                    skipped_count += 1
                else:
                    new_status = "drafted" if action == "drafted" else "sent"
                    new_stage = "contacted"

                    if source == "vault":
                        # Accumulate vault update (single batch write at end)
                        vault_updates[contact["slug"]] = {
                            "stage": new_stage,
                            "last_contact_date": date.today().isoformat(),
                            "last_contact_channel": "email",
                        }
                    else:
                        # Update Google Sheet
                        if "status" in col_map:
                            _update_sheet_cell(sheet, row_idx, col_map["status"], new_status, account)
                        if "sent_date" in col_map:
                            _update_sheet_cell(sheet, row_idx, col_map["sent_date"], date.today().isoformat(), account)

                    if action == "drafted":
                        drafted_count += 1
                    else:
                        sent_count += 1

                    pending_index += 1

                    if pending_index >= batch_size:
                        click.echo(json.dumps({"info": f"Batch limit reached ({batch_size}). Run again to continue."}), err=True)
                        break

            except AuthError:
                # Flush accumulated vault updates before crashing out
                if vault_updates:
                    crm.batch_update_contacts(vault_updates)
                raise  # Fatal — token is dead, stop immediately
            except CliError as exc:
                error_count += 1
                click.echo(json.dumps({"warning": f"Error processing {contact.get('email', 'unknown')}: {exc.message}"}), err=True)
            except Exception as exc:
                error_count += 1
                click.echo(json.dumps({"warning": f"Error processing {contact.get('email', 'unknown')}: {type(exc).__name__}"}), err=True)

        # Batch-write all vault contact updates at once (single CSV read+write)
        if vault_updates:
            crm.batch_update_contacts(vault_updates)

        output_success({
            "sent": sent_count,
            "drafted": drafted_count,
            "skipped": skipped_count,
            "errors": error_count,
        }, compact=compact)

    except CliError as e:
        output_error(e, compact=compact)
    except Exception as e:
        output_error(e, compact=compact)


def _run_campaign(campaign: str, template: str, account: str | None, batch_size: int, dry_run: bool, compact: bool) -> None:
    """Run outreach for a campaign CSV — send and track emails."""
    # Load campaign contacts
    csv_rows = crm.read_campaign_csv(campaign)
    contacts = [_campaign_csv_to_contact(row) for row in csv_rows]
    pending = [c for c in contacts if c["status"] == "pending"]

    if not pending:
        output_success({"sent": 0, "drafted": 0, "skipped": 0, "errors": 0, "message": "No pending contacts in campaign"}, compact=compact)
        return

    # Load template once and extract goal from same content
    subject_template, body_template = parse_template(template)
    campaign_goal = _parse_template_goal(template)

    # Determine batch number (max existing batch + 1)
    existing_batches = [int(c["batch"]) for c in contacts if c.get("batch") and c["batch"].isdigit()]
    batch_num = max(existing_batches, default=0) + 1

    # Limit to batch_size
    batch = pending[:batch_size]

    if dry_run:
        # Dry run: draft first 3 for review (creates real Gmail drafts)
        draft_count = 0
        for contact in batch[:3]:
            try:
                subject, body = _personalize_v1(subject_template, body_template, contact)
                gmail.draft(to=contact["email"], subject=subject, body=body, account=account)
                draft_count += 1
                click.echo(f"[DRY RUN] Drafted to {contact['name']} <{contact['email']}>", err=True)
            except CliError as exc:
                click.echo(json.dumps({"warning": f"Failed to draft for {contact.get('email', '?')}: {exc.message}"}), err=True)
            except Exception as exc:
                click.echo(json.dumps({"warning": f"Failed to draft for {contact.get('email', '?')}: {type(exc).__name__}"}), err=True)

        # Print batch plan and sample email (skip when --compact for agent consumption)
        if not compact:
            total = len(contacts)
            total_pending = len(pending)
            batches_remaining = (total_pending + batch_size - 1) // batch_size
            click.echo("", err=True)
            click.echo(f"Campaign: {campaign}", err=True)
            click.echo(f"Template: {template}", err=True)
            click.echo(f"Total contacts:  {total}", err=True)
            click.echo(f"Pending:         {total_pending}", err=True)
            click.echo(f"Batch size:      {batch_size}", err=True)
            click.echo(f"Batches needed:  {batches_remaining}", err=True)
            click.echo(f"Next batch:      {len(batch)} emails (batch {batch_num})", err=True)
            click.echo("", err=True)

            if batch:
                sample = batch[0]
                try:
                    sample_subject, sample_body = _personalize_v1(subject_template, body_template, sample)
                except CliError:
                    sample_subject, sample_body = subject_template, body_template
                click.echo("--- Sample email ---", err=True)
                click.echo(f"To: {sample['name']} <{sample['email']}>", err=True)
                click.echo(f"Subject: {sample_subject}", err=True)
                click.echo(f"Body:\n{sample_body}", err=True)
                click.echo("--- End sample ---", err=True)

        output_success({"drafted": draft_count, "sent": 0, "skipped": len(batch) - draft_count, "errors": 0, "dry_run": True}, compact=compact)
        return

    # Acquire campaign lock to prevent duplicate sends from concurrent invocations.
    # Uses exclusive create (O_EXCL) to avoid TOCTOU race conditions.
    # The lock file is removed when this run completes (or on crash via finally).
    lock_path = crm.campaigns_dir() / f".{campaign}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        # Lock exists — check if stale (> 2 hours old)
        try:
            lock_age = time.time() - lock_path.stat().st_mtime
        except OSError:
            lock_age = 0  # File vanished between check and stat
        if lock_age < 7200:
            raise CliError(
                f"Campaign '{campaign}' is already being sent (lock file exists, age {lock_age / 60:.0f}m).",
                suggestion="Wait for the current batch to finish, or remove the lock file if the process crashed.",
            )
        click.echo(f"Warning: stale lock file ({lock_age / 60:.0f}m old) — overriding", err=True)
        lock_path.write_text(str(os.getpid()), encoding="utf-8")

    # Live send — with random delays between emails to avoid spam filters
    sent_count = 0
    error_count = 0
    csv_updates: dict[str, dict[str, str]] = {}  # slug → updates (batch write at end)

    # Calculate delay range: if --send-by is set, spread evenly; otherwise 30-90s
    # Default: 30-90s random delay between each email
    delay_min = 30.0
    delay_max = 90.0

    try:
        for i, contact in enumerate(batch):
            # Random delay between sends (skip before first email)
            if i > 0:
                delay = random.uniform(delay_min, delay_max)
                click.echo(f"  [{i + 1}/{len(batch)}] Waiting {delay:.0f}s before next send...", err=True)
                time.sleep(delay)

            try:
                subject, body = _personalize_v1(subject_template, body_template, contact)

                outcome = _send_with_retry(contact, subject, body, account)
                result = outcome["result"]

                # Accumulate CSV update (single batch write at end)
                now = datetime.now(timezone.utc).isoformat()
                msg_id = result.get("id", "") if result else ""
                csv_updates[contact["slug"]] = {
                    "status": "sent",
                    "sent_at": now,
                    "message_id": msg_id,
                    "batch": str(batch_num),
                }

                sent_count += 1
                click.echo(f"  [{sent_count}/{len(batch)}] Sent to {contact['name']} <{contact['email']}>", err=True)

            except AuthError:
                # Flush accumulated updates before crashing out
                if csv_updates:
                    crm.batch_update_campaign_rows(campaign, csv_updates)
                raise  # Fatal — token is dead, stop immediately
            except CliError as exc:
                error_count += 1
                click.echo(json.dumps({"warning": f"Error sending to {contact.get('email', 'unknown')}: {exc.message}"}), err=True)
            except Exception as exc:
                error_count += 1
                click.echo(json.dumps({"warning": f"Error sending to {contact.get('email', 'unknown')}: {type(exc).__name__}"}), err=True)

        # Batch-write all CSV updates at once (single read + write instead of N)
        if csv_updates:
            crm.batch_update_campaign_rows(campaign, csv_updates)
    finally:
        # Always release campaign lock, even on unexpected exceptions
        lock_path.unlink(missing_ok=True)

    output_success({
        "sent": sent_count,
        "errors": error_count,
        "batch": batch_num,
    }, compact=compact)


@cli.command()
@click.option("--source", default="vault", type=click.Choice(["vault", "sheets"]), help="Contact source: vault CRM CSV (default) or Google Sheets")
@click.option("--sheet", default=None, help="Google Sheets spreadsheet ID (required if --source=sheets)")
@click.option("--campaign", default=None, help="Campaign CSV name. Overrides --source.")
@click.option("--template", default=None, help="Template name for campaign goal lookup (e.g. cold-outreach-v1)")
@click.option("--account", default="mail", type=str, callback=_validate_account_opt, help="Google account name from accounts.json or a legacy token (default: mail for outreach)")
@click.option("--since", default="2d", help="How far back to scan (e.g. 1d, 2d, 1w)")
@click.option("--compact", is_flag=True, help="Compact JSON output (saves tokens)")
def scan(source: str, sheet: str | None, campaign: str | None, template: str | None, account: str | None, since: str, compact: bool):
    """Monitor inbox for replies and update contact statuses."""
    try:
        # Campaign mode
        if campaign:
            _scan_campaign(campaign, account, since, compact, template=template)
            return

        # Build lookup: email → contact dict
        outreach_contacts: dict[str, tuple[dict, int]] = {}

        if source == "vault":
            contacts = _load_vault_contacts(stage_filter="contacted")
            for idx, contact in enumerate(contacts):
                if contact.get("email"):
                    outreach_contacts[contact["email"].lower()] = (contact, idx)
        else:
            if not sheet:
                raise CliError("Missing --sheet option", suggestion="Provide a Google Sheets ID with --source=sheets")
            data = sheets.read(sheet, range="Sheet1", account=account)
            rows = data.get("rows", [])
            if len(rows) < 2:
                output_success({"scanned": 0, "replies_found": 0, "bounced": 0, "positive": 0})
                return
            header = rows[0]
            col_map = _find_columns(header)
            for row_idx, row in enumerate(rows[1:], start=2):
                contact = _row_to_contact(row, col_map)
                if contact["status"] in ("sent", "drafted"):
                    outreach_contacts[contact["email"].lower()] = (contact, row_idx)

        if not outreach_contacts:
            output_success({"scanned": 0, "replies_found": 0, "bounced": 0, "positive": 0})
            return

        # Scan inbox
        inbox_result = gmail.scan(since=since, account=account)
        messages = inbox_result.get("messages", [])

        scanned = len(messages)
        replies_found = 0
        bounced = 0
        positive = 0

        for msg_summary in messages:
            # Extract sender email from "From" header
            from_field = msg_summary.get("from", "")
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_field.lower())
            if not email_match:
                continue
            sender_email = email_match.group(0)

            if sender_email not in outreach_contacts:
                continue

            thread_id = msg_summary.get("threadId", "")
            if not thread_id:
                continue

            contact, row_idx = outreach_contacts[sender_email]
            replies_found += 1

            try:
                # Get full message body
                full_msg = gmail.get(msg_summary["id"], account=account)
                body_text = full_msg.get("body", "")

                # Classify reply
                sentiment = classify_reply(body_text)

                # Map sentiment to CRM stage
                new_stage = SENTIMENT_TO_CRM_STAGE.get(sentiment, "replied")

                if source == "vault":
                    _update_vault_contact(contact["slug"], {
                        "stage": new_stage,
                        "last_contact_date": date.today().isoformat(),
                        "last_contact_channel": "email",
                    })
                else:
                    new_status = sentiment  # Use sentiment directly for sheet status
                    if "status" in col_map:
                        _update_sheet_cell(sheet, row_idx, col_map["status"], new_status, account)
                    if "reply_date" in col_map:
                        _update_sheet_cell(sheet, row_idx, col_map["reply_date"], date.today().isoformat(), account)

                if sentiment == "bounce":
                    bounced += 1
                elif sentiment in ("positive", "rsvp"):
                    positive += 1
                    click.echo(
                        f"{'RSVP' if sentiment == 'rsvp' else 'Positive'} reply from {contact['name']} <{contact['email']}>",
                        err=True,
                    )

            except AuthError:
                raise
            except Exception as exc:
                click.echo(json.dumps({"warning": f"Error processing reply from {sender_email}: {type(exc).__name__}"}), err=True)

        output_success({
            "scanned": scanned,
            "replies_found": replies_found,
            "bounced": bounced,
            "positive": positive,
        }, compact=compact)

    except CliError as e:
        output_error(e, compact=compact)
    except Exception as e:
        output_error(e, compact=compact)


def _scan_bounces(campaign: str, account: str | None, sent_contacts: dict[str, dict]) -> int:
    """Detect bounce notifications from mailer-daemon and update campaign CSV.

    Searches inbox for delivery failure notifications, extracts the failed
    email address, matches against campaign contacts, marks them as bounced,
    and trashes the bounce notification to keep the inbox clean.

    Side effect: removes bounced entries from sent_contacts to prevent
    double-processing in the subsequent reply scan.

    Returns the number of bounced contacts found.
    """
    bounce_result = gmail.list_messages(
        query="from:mailer-daemon subject:\"Delivery Status Notification\"",
        limit=50,
        account=account,
    )
    bounce_msgs = bounce_result.get("messages", [])
    bounced_count = 0
    csv_updates: dict[str, dict[str, str]] = {}

    for msg_summary in bounce_msgs:
        snippet = msg_summary.get("snippet", "")
        # Extract the failed email address from the snippet
        # Gmail bounce snippets vary widely:
        #   "Your message to user@domain.com couldn't be delivered"
        #   "Delivery to user@domain.com has been suspended"
        #   "wasn't delivered to user@domain.com"
        #   "couldn't be delivered to user@domain.com"
        #   "Message not delivered to user@domain.com"
        email_match = re.search(
            r"(?:message to|delivered to|delivery to|not delivered to)\s+(?:\w+\s+)*([\w.+-]+@[\w.-]+\.\w+)",
            snippet.lower(),
        )
        if not email_match:
            # Fallback: just find any email in the snippet
            email_match = re.search(r"([\w.+-]+@[\w.-]+\.\w+)", snippet.lower())
        if not email_match:
            continue

        failed_email = email_match.group(1)
        if failed_email not in sent_contacts:
            continue

        contact = sent_contacts[failed_email]
        csv_updates[contact["slug"]] = {
            "status": "bounced",
            "reply_sentiment": "bounce",
            "reply_snippet": snippet[:100].replace("\n", " ").strip(),
        }
        bounced_count += 1
        click.echo(f"  Bounce: {contact['name']} <{contact['email']}> — marked as bounced", err=True)

        # Remove from lookup to avoid double-processing in reply scan
        del sent_contacts[failed_email]

        # Trash the bounce notification to keep inbox clean
        try:
            gmail.trash(msg_summary["id"], account=account)
        except Exception:
            pass  # Non-critical — inbox cleanup is best-effort

    # Batch-write all bounce updates at once (single CSV read+write)
    if csv_updates:
        crm.batch_update_campaign_rows(campaign, csv_updates)

    return bounced_count


def _scan_campaign(campaign: str, account: str | None, since: str, compact: bool, template: str | None = None) -> None:
    """Scan inbox for replies to a campaign and update the campaign CSV."""
    csv_rows = crm.read_campaign_csv(campaign)
    contacts = [_campaign_csv_to_contact(row) for row in csv_rows]

    # Build lookup: email → contact (only sent contacts)
    sent_contacts: dict[str, dict] = {}
    for contact in contacts:
        if contact["status"] == "sent" and contact.get("email"):
            sent_contacts[contact["email"].lower()] = contact

    if not sent_contacts:
        output_success({"scanned": 0, "replies_found": 0, "rsvp": 0, "positive": 0, "bounced": 0, "unsubscribed": 0}, compact=compact)
        return

    # Phase 1: Detect bounces from mailer-daemon notifications
    phase1_bounced = _scan_bounces(campaign, account, sent_contacts)

    # Phase 2: Scan inbox for actual replies from contacts
    inbox_result = gmail.scan(since=since, account=account)
    messages = inbox_result.get("messages", [])

    scanned = len(messages)
    replies_found = 0
    rsvp_count = 0
    positive_count = 0
    bounced_count = phase1_bounced
    unsubscribed_count = 0
    csv_updates: dict[str, dict[str, str]] = {}  # slug → updates (batch write at end)

    for msg_summary in messages:
        from_field = msg_summary.get("from", "")
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_field.lower())
        if not email_match:
            continue
        sender_email = email_match.group(0)

        if sender_email not in sent_contacts:
            continue

        contact = sent_contacts[sender_email]
        replies_found += 1

        try:
            full_msg = gmail.get(msg_summary["id"], account=account)
            body_text = full_msg.get("body", "")

            sentiment = classify_reply(body_text)

            # Check for explicit opt-out requests (uses canonical list from reward module)
            body_lower = body_text.lower()
            is_unsub = any(kw in body_lower for kw in UNSUBSCRIBE_KEYWORDS)

            if is_unsub:
                new_status = "unsubscribed"
                unsubscribed_count += 1
            elif sentiment == "negative":
                new_status = "declined"
            elif sentiment == "bounce":
                new_status = "bounced"
            else:
                new_status = sentiment

            snippet = body_text[:100].replace("\n", " ").strip() if body_text else ""

            # Accumulate update (single batch write at end)
            csv_updates[contact["slug"]] = {
                "status": new_status,
                "reply_sentiment": sentiment,
                "reply_snippet": snippet,
            }

            if sentiment == "bounce":
                bounced_count += 1
            elif sentiment == "rsvp":
                rsvp_count += 1
                click.echo(f"  RSVP from {contact['name']} <{contact['email']}>", err=True)
            elif sentiment == "positive":
                positive_count += 1
                click.echo(f"  Positive reply from {contact['name']} <{contact['email']}> — review and respond manually", err=True)
            elif sentiment == "neutral":
                click.echo(f"  Reply from {contact['name']} <{contact['email']}> — review manually", err=True)

            # Remove from lookup to avoid double-processing
            del sent_contacts[sender_email]

        except AuthError:
            # Flush accumulated updates before crashing out
            if csv_updates:
                crm.batch_update_campaign_rows(campaign, csv_updates)
            raise
        except Exception as exc:
            click.echo(json.dumps({"warning": f"Error processing reply from {sender_email}: {type(exc).__name__}"}), err=True)

    # Batch-write all reply updates at once (single CSV read+write)
    if csv_updates:
        crm.batch_update_campaign_rows(campaign, csv_updates)

    output_success({
        "scanned": scanned,
        "replies_found": replies_found,
        "rsvp": rsvp_count,
        "positive": positive_count,
        "bounced": bounced_count,
        "unsubscribed": unsubscribed_count,
    }, compact=compact)


@cli.command()
@click.option("--source", default="vault", type=click.Choice(["vault", "sheets"]), help="Contact source: vault CRM CSV (default) or Google Sheets")
@click.option("--sheet", default=None, help="Google Sheets spreadsheet ID (required if --source=sheets)")
@click.option("--campaign", default=None, help="Campaign CSV name. Overrides --source.")
@click.option("--account", default="mail", type=str, callback=_validate_account_opt, help="Google account name from accounts.json or a legacy token (default: mail for outreach)")
@click.option("--compact", is_flag=True, help="Compact JSON output (saves tokens)")
def status(source: str, sheet: str | None, campaign: str | None, account: str | None, compact: bool):
    """Show CRM pipeline or campaign statistics."""
    try:
        # Campaign mode
        if campaign:
            _status_campaign(campaign, compact)
            return

        if source == "vault":
            contacts = [_vault_csv_to_contact(row) for row in crm.read_contacts_csv()]
            if not contacts:
                output_success({"total": 0, "message": "No contacts in CRM"})
                return

            # Count by stage (vault uses stage, not status)
            counts: dict[str, int] = {}
            total = len(contacts)
            for contact in contacts:
                stage = contact.get("status", "unknown") or "unknown"
                counts[stage] = counts.get(stage, 0) + 1

            # Compute rates using vault stage names
            contacted_or_later = sum(
                counts.get(s, 0)
                for s in ("contacted", "replied", "meeting_booked", "demo_done", "negotiation", "customer", "lost", "churned")
            )
            replied_stages = sum(counts.get(s, 0) for s in ("replied", "meeting_booked", "demo_done", "negotiation", "customer"))
            positive_stages = sum(counts.get(s, 0) for s in ("meeting_booked", "demo_done", "negotiation", "customer"))

            reply_rate = f"{replied_stages / contacted_or_later * 100:.0f}%" if contacted_or_later > 0 else "0%"
            positive_rate = f"{positive_stages / contacted_or_later * 100:.0f}%" if contacted_or_later > 0 else "0%"

            output_success({
                "total": total,
                "lead": counts.get("lead", 0),
                "contacted": counts.get("contacted", 0),
                "replied": counts.get("replied", 0),
                "meeting_booked": counts.get("meeting_booked", 0),
                "demo_done": counts.get("demo_done", 0),
                "negotiation": counts.get("negotiation", 0),
                "customer": counts.get("customer", 0),
                "lost": counts.get("lost", 0),
                "churned": counts.get("churned", 0),
                "reply_rate": reply_rate,
                "positive_rate": positive_rate,
            }, compact=compact)

        else:
            # Legacy sheets mode
            if not sheet:
                raise CliError("Missing --sheet option", suggestion="Provide a Google Sheets ID with --source=sheets")
            data = sheets.read(sheet, range="Sheet1", account=account)
            rows = data.get("rows", [])
            if len(rows) < 2:
                output_success({"total": 0, "message": "No data rows found"})
                return

            header = rows[0]
            col_map = _find_columns(header)

            counts: dict[str, int] = {}
            total = 0
            for row in rows[1:]:
                contact = _row_to_contact(row, col_map)
                status_val = contact.get("status") or "unknown"
                counts[status_val] = counts.get(status_val, 0) + 1
                total += 1

            sent_or_later = sum(
                counts.get(s, 0)
                for s in ("sent", "replied", "positive", "booked", "bounced", "declined")
            )
            replied_statuses = sum(counts.get(s, 0) for s in ("replied", "positive", "booked", "declined"))
            positive_statuses = sum(counts.get(s, 0) for s in ("positive", "booked"))

            reply_rate = f"{replied_statuses / sent_or_later * 100:.0f}%" if sent_or_later > 0 else "0%"
            positive_rate = f"{positive_statuses / sent_or_later * 100:.0f}%" if sent_or_later > 0 else "0%"
            completion_rate = f"{(total - counts.get('pending', 0)) / total * 100:.0f}%" if total > 0 else "0%"

            output_success({
                "total": total,
                "pending": counts.get("pending", 0),
                "drafted": counts.get("drafted", 0),
                "sent": counts.get("sent", 0),
                "replied": counts.get("replied", 0),
                "positive": counts.get("positive", 0),
                "booked": counts.get("booked", 0),
                "bounced": counts.get("bounced", 0),
                "declined": counts.get("declined", 0),
                "completion_rate": completion_rate,
                "reply_rate": reply_rate,
                "positive_rate": positive_rate,
            }, compact=compact)

    except CliError as e:
        output_error(e, compact=compact)
    except Exception as e:
        output_error(e, compact=compact)


# ---------------------------------------------------------------------------
# Enrollment helpers (Apollo → campaign CSV)
# ---------------------------------------------------------------------------


def _load_apollo_contacts(input_path: str) -> list[dict]:
    """Load contacts from an Apollo JSON export file.

    Expects a JSON array of objects with: first_name, last_name, email,
    organization_name, title.  Skips entries without an email.
    """
    path = Path(input_path)
    if not path.exists():
        raise CliError(
            f"Input file not found: {input_path}",
            suggestion="Provide a valid path to an Apollo JSON export.",
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise CliError(
            "Expected a JSON array of contacts",
            suggestion="Apollo export should be a top-level JSON array.",
        )

    # Filter out entries without email
    return [c for c in data if c.get("email", "").strip()]


def _build_dedup_sets() -> tuple[set[str], set[str]]:
    """Build dedup email sets from contacts.csv and investor-contacts.csv.

    Returns:
        (existing_emails, dne_emails) — both sets of lowercased email addresses.
        existing_emails: already contacted or in CRM.
        dne_emails: investor contacts with do_not_email=true.
    """
    # Existing CRM contacts
    existing_emails: set[str] = set()
    try:
        for row in crm.read_contacts_csv():
            email = row.get("email", "").strip().lower()
            if email:
                existing_emails.add(email)
    except CliError:
        pass  # contacts.csv may not exist yet

    # Investor contacts with do_not_email flag
    dne_emails: set[str] = set()
    investor_csv = crm.crm_path() / "investor-contacts.csv"
    if investor_csv.exists():
        import csv as csv_mod
        with open(investor_csv, encoding="utf-8", newline="") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if row.get("do_not_email", "").strip().lower() == "true":
                    email = row.get("email", "").strip().lower()
                    if email:
                        dne_emails.add(email)

    return existing_emails, dne_emails


def _make_slug(first_name: str, last_name: str) -> str:
    """Generate a URL-safe slug from first + last name."""
    slug = f"{first_name}-{last_name}".lower().strip()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _enroll_contacts(
    contacts: list[dict],
    campaign_name: str,
    sequence_name: str,
    existing_emails: set[str],
    dne_emails: set[str],
    dry_run: bool = False,
) -> dict:
    """Enroll Apollo contacts into a campaign CSV.

    Deduplicates against existing_emails, dne_emails, and any emails
    already present in the target campaign CSV (if it exists).

    Returns summary dict with enrolled, skipped_existing, skipped_dne counts.
    """
    # Load existing campaign rows if the CSV already exists
    campaign_emails: set[str] = set()
    existing_rows: list[dict[str, str]] = []
    campaign_path = crm.campaigns_dir() / f"{campaign_name}.csv"
    if campaign_path.exists():
        existing_rows = crm.read_campaign_csv(campaign_name)
        for row in existing_rows:
            email = row.get("email", "").strip().lower()
            if email:
                campaign_emails.add(email)

    # Load sequence config for send window
    seq_dir = crm.templates_dir() / "sequences" / sequence_name
    seq_config_path = seq_dir / "sequence.json"
    if seq_config_path.exists():
        with open(seq_config_path, encoding="utf-8") as f:
            seq_config = json.load(f)
    else:
        seq_config = {
            "send_window": {"start_hour": 8, "end_hour": 11, "timezone": "America/New_York"},
        }

    # Calculate next_send_at: tomorrow at start_hour in the send window timezone
    send_window = seq_config.get("send_window", {})
    start_hour = send_window.get("start_hour", 8)
    # Use a simple UTC approximation: tomorrow at start_hour EST ≈ start_hour+5 UTC
    tomorrow = date.today().isoformat()
    next_send_at = f"{tomorrow}T{start_hour + 5:02d}:00:00Z"

    enrolled = 0
    skipped_existing = 0
    skipped_dne = 0
    new_rows: list[dict[str, str]] = []

    for contact in contacts:
        email = contact.get("email", "").strip().lower()
        if not email:
            continue

        # Dedup: skip if in CRM contacts or already in this campaign
        if email in existing_emails or email in campaign_emails:
            skipped_existing += 1
            continue

        # Dedup: skip if do_not_email
        if email in dne_emails:
            skipped_dne += 1
            continue

        first_name = contact.get("first_name", "").strip()
        last_name = contact.get("last_name", "").strip()
        company = contact.get("organization_name", "").strip()
        role = contact.get("title", "").strip()
        slug = _make_slug(first_name, last_name)

        # Ensure slug uniqueness within batch + existing campaign rows
        base_slug = slug
        counter = 2
        used_slugs = {r.get("slug", "").strip().lower() for r in existing_rows + new_rows}
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1

        row: dict[str, str] = {
            "slug": slug,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "role": role,
            "status": "scheduled",
            "step": "0",
            "sequence": sequence_name,
            "next_send_at": next_send_at,
            "step1_message_id": "",
            "step1_sent_at": "",
            "step2_message_id": "",
            "step2_sent_at": "",
            "step3_message_id": "",
            "step3_sent_at": "",
            "reply_date": "",
            "reply_sentiment": "",
            "reply_snippet": "",
            "batch": "",
            "notes": "",
        }

        new_rows.append(row)
        campaign_emails.add(email)
        enrolled += 1

    # Write the CSV (create or append)
    if not dry_run and new_rows:
        all_rows = existing_rows + new_rows
        crm._write_csv(campaign_path, all_rows)

    return {
        "enrolled": enrolled,
        "skipped_existing": skipped_existing,
        "skipped_dne": skipped_dne,
        "dry_run": dry_run,
        "campaign": campaign_name,
        "sequence": sequence_name,
    }


def _status_campaign(campaign: str, compact: bool) -> None:
    """Show campaign-specific dashboard."""
    csv_rows = crm.read_campaign_csv(campaign)
    contacts = [_campaign_csv_to_contact(row) for row in csv_rows]
    total = len(contacts)

    if not contacts:
        output_success({"total": 0, "message": "No contacts in campaign"}, compact=compact)
        return

    # Count by status
    counts: dict[str, int] = {}
    for contact in contacts:
        s = contact.get("status", "pending") or "pending"
        counts[s] = counts.get(s, 0) + 1

    # Determine next batch info
    pending = counts.get("pending", 0)
    existing_batches = [int(c["batch"]) for c in contacts if c.get("batch") and c["batch"].isdigit()]
    next_batch_num = max(existing_batches, default=0) + 1
    next_batch_size = min(pending, 25)

    sent = counts.get("sent", 0)
    replied = counts.get("replied", 0)
    rsvp = counts.get("rsvp", 0)
    declined = counts.get("declined", 0)
    unsubscribed = counts.get("unsubscribed", 0)
    bounced = counts.get("bounced", 0)

    # Human-readable dashboard (skip when --compact for agent consumption)
    if not compact:
        def pct(n: int) -> str:
            return f"{n / total * 100:.0f}%" if total > 0 else "0%"

        title = campaign.replace("-", " ").title()
        click.echo("", err=True)
        click.echo(f"  {title} Campaign Status", err=True)
        click.echo(f"  {'─' * 40}", err=True)
        click.echo(f"  Total contacts:    {total:>4}", err=True)
        click.echo(f"  Sent:              {sent:>4}   ({pct(sent)})", err=True)
        click.echo(f"  Pending:           {pending:>4}   ({pct(pending)})", err=True)
        click.echo(f"  Replied:           {replied:>4}   ({pct(replied)})", err=True)
        click.echo(f"  RSVP'd:            {rsvp:>4}   ({pct(rsvp)})", err=True)
        click.echo(f"  Declined:          {declined:>4}   ({pct(declined)})", err=True)
        click.echo(f"  Unsubscribed:      {unsubscribed:>4}   ({pct(unsubscribed)})", err=True)
        click.echo(f"  Bounced:           {bounced:>4}   ({pct(bounced)})", err=True)
        click.echo(f"  {'─' * 40}", err=True)
        if pending > 0:
            click.echo(f"  Next batch:        {next_batch_size} emails (batch {next_batch_num})", err=True)
        else:
            click.echo(f"  All emails sent!", err=True)
        click.echo("", err=True)

    # JSON output for agents
    output_success({
        "campaign": campaign,
        "total": total,
        "pending": pending,
        "sent": sent,
        "replied": replied,
        "rsvp": rsvp,
        "declined": declined,
        "unsubscribed": unsubscribed,
        "bounced": bounced,
        "next_batch": next_batch_size,
        "next_batch_num": next_batch_num,
    }, compact=compact)


@cli.command()
@click.option("--campaign", required=True, help="Campaign CSV name (e.g. pharma-vp-2026-03)")
@click.option("--sequence", required=True, help="Sequence name (e.g. cold-outreach-pharma-vp)")
@click.option("--input", "input_path", required=True, help="Path to Apollo JSON export file")
@click.option("--dry-run", is_flag=True, help="Preview enrollment without creating/modifying CSV")
@click.option("--compact", is_flag=True, help="Compact JSON output (saves tokens)")
def enroll(campaign: str, sequence: str, input_path: str, dry_run: bool, compact: bool):
    """Enroll Apollo contacts into a campaign CSV for multi-step outreach.

    Reads an Apollo JSON export, deduplicates against contacts.csv and
    investor-contacts.csv (do_not_email), and creates/appends to a campaign
    CSV with the multi-step sequence schema.
    """
    try:
        # Load Apollo contacts
        apollo_contacts = _load_apollo_contacts(input_path)
        if not apollo_contacts:
            output_success({"enrolled": 0, "message": "No contacts with email in input file"}, compact=compact)
            return

        # Build dedup sets
        existing_emails, dne_emails = _build_dedup_sets()

        # Enroll
        result = _enroll_contacts(
            contacts=apollo_contacts,
            campaign_name=campaign,
            sequence_name=sequence,
            existing_emails=existing_emails,
            dne_emails=dne_emails,
            dry_run=dry_run,
        )

        # Summary output
        if not compact:
            click.echo("", err=True)
            click.echo(f"  Enrollment Summary", err=True)
            click.echo(f"  {'─' * 40}", err=True)
            click.echo(f"  Campaign:          {campaign}", err=True)
            click.echo(f"  Sequence:          {sequence}", err=True)
            click.echo(f"  Input contacts:    {len(apollo_contacts)}", err=True)
            click.echo(f"  Enrolled:          {result['enrolled']}", err=True)
            click.echo(f"  Skipped (existing):{result['skipped_existing']}", err=True)
            click.echo(f"  Skipped (dne):     {result['skipped_dne']}", err=True)
            if dry_run:
                click.echo(f"  Mode:              DRY RUN (no CSV written)", err=True)
            click.echo("", err=True)

        output_success(result, compact=compact)

    except CliError as e:
        output_error(e, compact=compact)
    except Exception as e:
        output_error(e, compact=compact)


if __name__ == "__main__":
    try:
        cli()
    except CliError as e:
        output_error(e)
    except Exception as e:
        output_error(e)

"""Gmail-to-CRM auto-sync — keep last_contact_date fresh from email activity.

Scans recent Gmail threads, matches each external participant (From/To/Cc) to
``contacts.csv`` by email, and updates ``last_contact_date`` and
``last_contact_channel`` for matched contacts.

Design:
- **Never creates new contacts.** Calendar sync owns contact creation; this
  module is read-mostly + targeted updates only.
- **Last-write-wins by date.** A contact's ``last_contact_date`` is only
  bumped if the email date is later than the existing value.
- **State file** in ``cache/email-crm-sync-state.json`` tracks processed
  message IDs (Gmail message id is stable). Re-running is idempotent.
- **Atomic CSV write** (temp + rename) like ``crm_linker.update_crm_contacts``.
- **Internal emails skipped.** Messages where every party is on a configured
  internal domain (``GW_INTERNAL_DOMAINS``) are ignored — they don't reflect
  external relationship freshness. With no internal domains set, nothing is
  skipped on that basis.

Follows the calendar_crm_sync architecture pattern.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, getaddresses
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — import siblings + repo cache
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_GW_LIB = _PLUGIN_ROOT / "lib"

if str(_GW_LIB) not in sys.path:
    sys.path.insert(0, str(_GW_LIB))

import crm  # type: ignore[import-not-found]
import gmail  # type: ignore[import-not-found]
import gw_config  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Email domains treated as internal (skipped by sync). Empty by default.
INTERNAL_DOMAINS = gw_config.internal_domains()
STATE_FILE = gw_config.cache_dir() / "email-crm-sync-state.json"
DEFAULT_LOOKBACK_DAYS = 2

# CRM contact columns — canonical schema lives in crm.py so the writer here and
# `gw crm init` can never drift.
_CONTACTS_COLUMNS = crm.CONTACTS_COLUMNS

# Stages that promote to 'replied' on the first inbound message we observe.
# Past 'replied' is judgment territory — auto-promotion stops there.
_PROMOTABLE_STAGES = {"lead", "contacted"}


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"processed_message_ids": [], "last_sync": None, "total_synced": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"processed_message_ids": [], "last_sync": None, "total_synced": 0}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_csv_value(value: str) -> str:
    if value and value[0] in ("=", "@", "\t", "\r"):
        return f"'{value}"
    return value


def _extract_emails(*headers: str) -> list[str]:
    """Parse all email addresses out of From/To/Cc header strings.

    Handles forms like ``"Name <a@b.com>, c@d.com"`` and returns lowercased
    emails. Falls back to a regex if email.utils chokes on malformed input.
    """
    addrs: list[str] = []
    for h in headers:
        if not h:
            continue
        try:
            for _, addr in getaddresses([h]):
                addr = addr.strip().lower()
                if addr and "@" in addr:
                    addrs.append(addr)
        except Exception:
            for m in re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", h):
                addrs.append(m.lower())
    return addrs


def _is_internal(emails: list[str]) -> bool:
    """True if every address is on a configured internal domain.

    With no internal domains configured, this is always False (nothing skipped).
    """
    if not emails or not INTERNAL_DOMAINS:
        return False
    return all(e.split("@", 1)[-1] in INTERNAL_DOMAINS for e in emails)


def _parse_message_date(date_header: str) -> str:
    """Convert an RFC 2822 Date header to a YYYY-MM-DD string in **local time**.

    All CRM date fields (``last_contact_date``, ``last_meeting_date``, etc.) are
    expressed in the system's local timezone — whatever it is currently set to.
    ``dt.astimezone()`` with no argument converts to the system local TZ, so no
    timezone is hardcoded. Returns '' on failure.
    """
    if not date_header:
        return ""
    try:
        dt = parsedate_to_datetime(date_header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _build_email_index() -> dict[str, str]:
    """Lowercased email → contact slug, from contacts.csv."""
    index: dict[str, str] = {}
    csv_path = crm.contacts_csv_path()
    if not csv_path.exists():
        return index
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            email = row.get("email", "").strip().lower()
            slug = row.get("slug", "").strip()
            if email and slug:
                index[email] = slug
    return index


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------

def sync(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    dry_run: bool = False,
    account: str | None = None,
    limit: int = 0,
) -> dict:
    """Scan recent Gmail messages and bump last_contact_date for known contacts.

    Args:
        lookback_days: How many days back to scan (default 2).
        dry_run: If True, log changes without writing CSV.
        account: Google account override (main/mail), forwarded to gmail lib.
        limit: Maximum messages to process per run (0 = unlimited).

    Returns:
        Stats dict: ``{processed, skipped_internal, skipped_unknown,
        skipped_already_processed, contacts_updated, dry_run}``.
    """
    state = _load_state()
    processed_ids = set(state.get("processed_message_ids", []))

    # 1. Fetch recent messages (metadata only; we don't need bodies for date+headers)
    query = f"newer_than:{lookback_days}d -in:chats -in:spam -in:trash"
    fetch_limit = limit if limit > 0 else 500
    response = gmail.list_messages(query=query, limit=fetch_limit, account=account)
    messages = response.get("messages", [])

    if not messages:
        return {
            "processed": 0, "skipped_internal": 0, "skipped_unknown": 0,
            "skipped_already_processed": 0, "contacts_updated": 0,
            "dry_run": dry_run, "lookback_days": lookback_days,
        }

    email_index = _build_email_index()
    if not email_index:
        # Distinguish a missing CRM (actionable) from an empty-but-valid one
        # (a fresh `crm init` with no contacts yet — a clean no-op, not an error).
        result = {
            "processed": 0, "skipped_internal": 0, "skipped_unknown": 0,
            "skipped_already_processed": 0, "contacts_updated": 0,
            "dry_run": dry_run,
        }
        if not crm.contacts_csv_path().exists():
            result["error"] = "contacts.csv not found — run `gw crm init` to scaffold it"
        return result

    # 2. Walk each message, distinguishing inbound (sender external) from
    # outbound (sender internal). Inbound messages can promote stage; both
    # update last_contact_date with the appropriate channel.
    slug_latest_inbound: dict[str, str] = {}   # known contact replied
    slug_latest_outbound: dict[str, str] = {}  # we emailed a known contact
    stats = {
        "processed": 0, "skipped_internal": 0, "skipped_unknown": 0,
        "skipped_already_processed": 0, "contacts_updated": 0,
        "stages_promoted": 0,
        "dry_run": dry_run, "lookback_days": lookback_days,
    }
    seen_message_ids: list[str] = []

    for msg in messages:
        mid = msg.get("id", "")
        if not mid:
            continue
        if mid in processed_ids:
            stats["skipped_already_processed"] += 1
            continue

        seen_message_ids.append(mid)

        from_emails = _extract_emails(msg.get("from", ""))
        to_emails = _extract_emails(msg.get("to", ""))
        cc_emails = _extract_emails(msg.get("cc", ""))
        all_emails = from_emails + to_emails + cc_emails

        if _is_internal(all_emails):
            stats["skipped_internal"] += 1
            continue

        msg_date = _parse_message_date(msg.get("date", ""))
        if not msg_date:
            continue
        stats["processed"] += 1

        sender_is_internal = bool(from_emails) and all(
            e.split("@", 1)[-1] in INTERNAL_DOMAINS for e in from_emails
        )

        if sender_is_internal:
            # Outbound — bump dates for known recipients (To+Cc only, not us)
            recipients = [e for e in (to_emails + cc_emails)
                          if e.split("@", 1)[-1] not in INTERNAL_DOMAINS]
            matched_any = False
            for email in recipients:
                slug = email_index.get(email)
                if not slug:
                    continue
                matched_any = True
                existing = slug_latest_outbound.get(slug, "")
                if not existing or msg_date > existing:
                    slug_latest_outbound[slug] = msg_date
            if not matched_any:
                stats["skipped_unknown"] += 1
        else:
            # Inbound — sender is the external party. Bump THEIR date and
            # consider stage promotion.
            matched_any = False
            for email in from_emails:
                if email.split("@", 1)[-1] in INTERNAL_DOMAINS:
                    continue
                slug = email_index.get(email)
                if not slug:
                    continue
                matched_any = True
                existing = slug_latest_inbound.get(slug, "")
                if not existing or msg_date > existing:
                    slug_latest_inbound[slug] = msg_date
            if not matched_any:
                stats["skipped_unknown"] += 1

    # 3. Apply updates atomically. Inbound applied first (with stage promotion),
    # then outbound (last-write-wins date guard means they coexist cleanly).
    if not dry_run:
        if slug_latest_inbound:
            inbound_updated, promoted = _apply_inbound_updates(slug_latest_inbound)
            stats["contacts_updated"] += inbound_updated
            stats["stages_promoted"] = promoted
        if slug_latest_outbound:
            stats["contacts_updated"] += _apply_updates_with_channel(
                slug_latest_outbound, "email-out",
            )

    # 4. Persist state (always, so a dry run still records what was seen if --commit-state)
    if not dry_run:
        new_processed = list(processed_ids | set(seen_message_ids))
        # Cap at 5,000 ids to bound state file size
        if len(new_processed) > 5000:
            new_processed = new_processed[-5000:]
        state["processed_message_ids"] = new_processed
        state["last_sync"] = datetime.now(timezone.utc).isoformat()
        state["total_synced"] = state.get("total_synced", 0) + stats["contacts_updated"]
        _save_state(state)
    else:
        # Report would-be updates for dry runs
        stats["would_update_inbound"] = dict(slug_latest_inbound)
        stats["would_update_outbound"] = dict(slug_latest_outbound)

    return stats


def _apply_inbound_updates(slug_latest: dict[str, str]) -> tuple[int, int]:
    """Bump last_contact_date AND promote stage lead/contacted → replied.

    Returns ``(rows_updated, stages_promoted)``.
    """
    csv_path = crm.contacts_csv_path()
    if not csv_path.exists():
        return 0, 0

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or _CONTACTS_COLUMNS
        rows = list(reader)

    updated = 0
    promoted = 0
    for row in rows:
        slug = row.get("slug", "").strip()
        if slug not in slug_latest:
            continue
        new_date = slug_latest[slug]
        existing = row.get("last_contact_date", "").strip()
        date_bumped = False
        if not existing or new_date > existing:
            row["last_contact_date"] = new_date
            row["last_contact_channel"] = "email"
            date_bumped = True

        # Stage promotion: only safe transitions, only if a date was bumped
        # this run (means the inbound message is genuinely new).
        if date_bumped:
            current_stage = row.get("stage", "").strip().lower()
            if current_stage in _PROMOTABLE_STAGES:
                row["stage"] = "replied"
                promoted += 1

        if date_bumped:
            updated += 1

    sanitized = [
        {k: _sanitize_csv_value(str(v)) for k, v in row.items()}
        for row in rows
    ]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(sanitized)
    tmp = csv_path.with_suffix(".tmp")
    tmp.write_text(out.getvalue(), encoding="utf-8")
    tmp.replace(csv_path)

    return updated, promoted


def status() -> dict:
    """Return last sync stats for `gw gmail sync-crm-status`."""
    state = _load_state()
    return {
        "last_sync": state.get("last_sync"),
        "total_synced_lifetime": state.get("total_synced", 0),
        "processed_message_ids_tracked": len(state.get("processed_message_ids", [])),
        "state_file": str(STATE_FILE),
    }


# ---------------------------------------------------------------------------
# Outbound hook — called from gmail.send/reply/reply_all on every successful send
# ---------------------------------------------------------------------------

def bump_recipients_last_contact(
    to: str | None,
    cc: str | None = None,
    channel: str = "email-out",
) -> dict:
    """Update last_contact_date for known CRM recipients of an outbound email.

    Called by ``gmail.send()``, ``gmail.reply()``, and ``gmail.reply_all()``
    immediately after a successful send. Never raises — a CRM failure must
    not break the email path. Errors are swallowed and reflected in the
    returned stats dict.

    Args:
        to: To-header string (may be ``"Name <a@b.com>, c@d.com"``).
        cc: Cc-header string (same form).
        channel: ``last_contact_channel`` value, default ``"email-out"`` to
                 distinguish outbound activity from inbound replies.

    Returns:
        ``{updated: int, matched: int, error: str | None}``.
    """
    try:
        recipients = _extract_emails(to or "", cc or "")
        # Filter out internal addresses (we don't track our own dates)
        external = [e for e in recipients if e.split("@", 1)[-1] not in INTERNAL_DOMAINS]
        if not external:
            return {"updated": 0, "matched": 0, "error": None}

        email_index = _build_email_index()
        if not email_index:
            return {"updated": 0, "matched": 0, "error": "contacts.csv not loadable"}

        # CRM dates are user-facing and reflect the system's local time.
        today = datetime.now().strftime("%Y-%m-%d")
        slug_latest: dict[str, str] = {}
        for email in external:
            slug = email_index.get(email)
            if slug:
                slug_latest[slug] = today

        if not slug_latest:
            return {"updated": 0, "matched": 0, "error": None}

        updated = _apply_updates_with_channel(slug_latest, channel)
        return {"updated": updated, "matched": len(slug_latest), "error": None}
    except Exception as exc:
        return {"updated": 0, "matched": 0, "error": str(exc)}


def _apply_updates_with_channel(slug_latest: dict[str, str], channel: str) -> int:
    """Variant of _apply_updates that lets us tag a specific channel."""
    csv_path = crm.contacts_csv_path()
    if not csv_path.exists():
        return 0

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or _CONTACTS_COLUMNS
        rows = list(reader)

    updated = 0
    for row in rows:
        slug = row.get("slug", "").strip()
        if slug not in slug_latest:
            continue
        new_date = slug_latest[slug]
        existing = row.get("last_contact_date", "").strip()
        if not existing or new_date > existing:
            row["last_contact_date"] = new_date
            row["last_contact_channel"] = channel
            updated += 1

    sanitized = [
        {k: _sanitize_csv_value(str(v)) for k, v in row.items()}
        for row in rows
    ]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(sanitized)
    tmp = csv_path.with_suffix(".tmp")
    tmp.write_text(out.getvalue(), encoding="utf-8")
    tmp.replace(csv_path)

    return updated

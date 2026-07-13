"""Calendar-to-CRM auto-sync — detect appointment bookings, add to CRM, optionally enrich.

Part of the OPTIONAL knowledge-base connector. Processes events booked through
Google Calendar appointment schedules, identified by the extendedProperties
marker ``goo.createdBySet == "default_cita"`` that Google sets on appointment
bookings.

Booking form data (name, email, company, LinkedIn) is parsed directly from the
event description. Optional Exa enrichment (enabled via ``GW_EXA_LIB``) fills
remaining gaps (LinkedIn if not provided, company details).

Architecture: state file, batch processing, crash-safe per-event saves.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_GW_LIB = _PLUGIN_ROOT / "lib"

if str(_GW_LIB) not in sys.path:
    sys.path.insert(0, str(_GW_LIB))

import crm
import gw_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Email domains treated as internal (never turned into CRM contacts). Empty by default.
INTERNAL_DOMAINS = gw_config.internal_domains()
STATE_FILE = gw_config.cache_dir() / "calendar-crm-sync-state.json"
DEFAULT_LOOKBACK_DAYS = 2

# Free email providers — don't create company records for these
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "me.com", "live.com",
    "mail.com", "zoho.com", "yandex.com", "gmx.com",
}

# CRM contact columns (must match contacts.csv schema)
_CONTACTS_COLUMNS = [
    "slug", "first_name", "last_name", "email", "linkedin_url", "company",
    "role", "stage", "source", "campaign", "first_contact_date",
    "last_contact_date", "last_contact_channel", "last_meeting_date",
    "next_action", "next_action_date", "priority", "notes",
]

# CRM company columns (must match companies.csv schema)
_COMPANIES_COLUMNS = [
    "slug", "name", "website", "industry", "size", "location",
    "stage", "source", "notes",
]


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Read sync state file. Returns default state if missing."""
    if not STATE_FILE.exists():
        return {"processed_event_ids": [], "last_sync": None, "total_synced": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"processed_event_ids": [], "last_sync": None, "total_synced": 0}


def save_state(state: dict) -> None:
    """Write sync state file. Creates cache/ directory if needed."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Calendar event fetching — appointment bookings only
# ---------------------------------------------------------------------------

def fetch_booking_events(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Fetch ONLY appointment-booking events from the calendar.

    Uses the raw Google Calendar API to access extendedProperties which
    the standard list_events() doesn't return. Filters to events where
    extendedProperties.shared.goo.createdBySet == "default_cita"
    (Google's marker for appointment schedule bookings).
    """
    from auth import build_service
    from datetime import timezone as tz

    now = datetime.now(tz.utc)
    time_min = now - timedelta(days=lookback_days)
    time_max = now + timedelta(days=lookback_days)

    service = build_service("calendar", "v3")
    result = service.events().list(
        calendarId="primary",
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        maxResults=100,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    bookings: list[dict] = []
    for ev in result.get("items", []):
        # Check for appointment booking marker
        ext_props = ev.get("extendedProperties", {})
        shared = ext_props.get("shared", {})
        if shared.get("goo.createdBySet") != "default_cita":
            continue

        # Parse structured booking data from description
        booking_data = _parse_booking_description(ev.get("description", ""))

        bookings.append({
            "id": ev["id"],
            "title": ev.get("summary", "(no title)"),
            "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
            "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
            "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
            "meetLink": ev.get("hangoutLink", ""),
            "link": ev.get("htmlLink", ""),
            "booking_data": booking_data,
        })

    return bookings


def _parse_booking_description(description: str) -> dict[str, str]:
    """Parse structured data from Google Calendar appointment booking description.

    Booking descriptions follow this HTML pattern:
        <b>Booked by</b>
        Full Name
        email@domain.com
        <b>Company name</b>
        Company Inc.
        <b>LinkedIn profile</b>
        https://linkedin.com/in/person/
        ...boilerplate text...

    Returns dict with keys: full_name, email, company, linkedin_url (any may be empty).
    """
    if not description:
        return {"full_name": "", "email": "", "company": "", "linkedin_url": ""}

    # Normalize: replace <br> with newlines, strip HTML tags
    text = description.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    result: dict[str, str] = {"full_name": "", "email": "", "company": "", "linkedin_url": ""}

    # Find "Booked by" section — next two lines are name and email
    for i, line in enumerate(lines):
        line_lower = line.lower()

        if "booked by" in line_lower:
            # Next line = name, line after = email
            if i + 1 < len(lines):
                candidate_name = lines[i + 1]
                # Verify it's not another label or URL
                if "@" not in candidate_name and "http" not in candidate_name.lower():
                    result["full_name"] = candidate_name
            if i + 2 < len(lines):
                candidate_email = lines[i + 2]
                if "@" in candidate_email and " " not in candidate_email:
                    result["email"] = candidate_email.lower()

        elif "company name" in line_lower:
            if i + 1 < len(lines):
                candidate = lines[i + 1]
                if "@" not in candidate and "http" not in candidate.lower() and len(candidate) < 100:
                    result["company"] = candidate

        elif "linkedin" in line_lower and "profile" in line_lower:
            if i + 1 < len(lines):
                candidate = lines[i + 1]
                parsed = urlparse(candidate.strip())
                if parsed.scheme == "https" and parsed.hostname in ("linkedin.com", "www.linkedin.com"):
                    result["linkedin_url"] = candidate.strip()

    return result


def _email_to_domain(email: str) -> str:
    """Extract domain from email address."""
    if "@" in email:
        return email.lower().split("@")[-1]
    return ""


def _is_internal_email(email: str) -> bool:
    """Check if an email is empty or on a configured internal domain."""
    if not email:
        return True
    domain = _email_to_domain(email)
    return domain in INTERNAL_DOMAINS


def _make_slug(first_name: str, last_name: str) -> str:
    """Generate a URL-safe slug from name."""
    name = f"{first_name}-{last_name}".lower().strip("-")
    slug = re.sub(r"[^a-z0-9-]", "", name)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unknown"


def _make_company_slug(name: str) -> str:
    """Generate a URL-safe slug from company name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9 -]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return slug or "unknown"


def _name_from_email(email: str) -> tuple[str, str]:
    """Fallback name extraction from email address."""
    local = email.split("@")[0] if "@" in email else email
    for sep in (".", "_", "-"):
        if sep in local:
            parts = local.split(sep)
            if len(parts) >= 2:
                return parts[0].capitalize(), parts[-1].capitalize()
    return local.capitalize(), ""


# ---------------------------------------------------------------------------
# Exa enrichment
# ---------------------------------------------------------------------------

def _get_exa_client():
    """Get an Exa client instance, or None if enrichment is not configured.

    Enrichment is opt-in: set ``GW_EXA_LIB`` to the ``lib`` dir of an Exa CLI
    that exposes ``client.ExaClient.from_env()``. Unset (the default) or any
    import failure returns None, and sync proceeds without enrichment.
    """
    exa_lib = gw_config.exa_lib()
    if exa_lib is None:
        return None
    try:
        if str(exa_lib) not in sys.path:
            sys.path.insert(0, str(exa_lib))
        from client import ExaClient  # type: ignore[import-not-found]
        return ExaClient.from_env()
    except Exception:
        return None


def enrich_person_via_exa(
    first_name: str,
    last_name: str,
    company: str | None = None,
) -> dict[str, str]:
    """Search Exa for a person's LinkedIn profile and role.

    Only called when the booking form didn't provide LinkedIn.
    Returns dict with keys: linkedin_url, role (any may be empty).
    """
    enriched: dict[str, str] = {"linkedin_url": "", "role": ""}

    client = _get_exa_client()
    if not client:
        return enriched

    full_name = f"{first_name} {last_name}".strip()
    if not full_name:
        return enriched

    query = full_name
    if company:
        query += f" {company}"

    try:
        result = client.search(
            query,
            category="linkedin profile",
            num_results=3,
            text=False,
            highlights=False,
        )
        for r in result.get("results", []):
            url = r.get("url", "")
            if "linkedin.com/in/" in url:
                enriched["linkedin_url"] = url
                # Parse role from LinkedIn title: "Name - Title - Company | LinkedIn"
                title_str = r.get("title", "")
                if " - " in title_str:
                    parts = title_str.split(" - ")
                    if len(parts) >= 2:
                        enriched["role"] = parts[1].split("|")[0].strip()
                break
    except Exception:
        pass  # Best-effort — never block sync

    return enriched


def enrich_company_via_exa(domain: str) -> dict[str, str]:
    """Search Exa for company info by domain.

    Returns dict with keys: name, website, industry, size, location (any may be empty).
    """
    enriched: dict[str, str] = {
        "name": "", "website": "", "industry": "", "size": "", "location": "",
    }
    if not domain:
        return enriched

    client = _get_exa_client()
    if not client:
        return enriched

    try:
        result = client.search(
            domain,
            category="company",
            num_results=3,
            summary=True,
            summary_query=f"What does {domain} do? What industry, company size, and location?",
        )
        results = result.get("results", [])
        if results:
            top = results[0]
            enriched["name"] = top.get("title", "").split("|")[0].split("-")[0].strip()
            enriched["website"] = f"https://{domain}"
            summary = top.get("summary", "")
            if summary:
                summary_lower = summary.lower()
                for industry in [
                    "biotechnology", "pharmaceutical", "technology", "healthcare",
                    "life sciences", "software", "consulting", "manufacturing",
                    "finance", "education", "biotech", "pharma",
                ]:
                    if industry in summary_lower:
                        enriched["industry"] = industry.replace("biotech", "biotechnology").replace("pharma", "pharmaceutical")
                        break
    except Exception:
        pass

    return enriched


# ---------------------------------------------------------------------------
# CRM operations
# ---------------------------------------------------------------------------

def _contacts_by_email() -> dict[str, dict[str, str]]:
    """Build email -> contact lookup from CRM."""
    try:
        contacts = crm.read_contacts_csv()
    except Exception:
        return {}
    lookup: dict[str, dict[str, str]] = {}
    for c in contacts:
        email = c.get("email", "").strip().lower()
        if email:
            lookup[email] = c
    return lookup


def _companies_by_domain() -> dict[str, dict[str, str]]:
    """Build domain -> company lookup from CRM."""
    try:
        companies = crm.read_companies_csv()
    except Exception:
        return {}
    lookup: dict[str, dict[str, str]] = {}
    for c in companies:
        website = c.get("website", "").strip().lower()
        if website:
            domain = website.replace("https://", "").replace("http://", "")
            domain = domain.replace("www.", "").rstrip("/")
            lookup[domain] = c
    return lookup


def _append_company(company_data: dict[str, str]) -> None:
    """Append a new company to companies.csv."""
    csv_path = crm.companies_csv_path()
    if not csv_path.exists():
        return

    try:
        existing = crm.read_companies_csv()
    except Exception:
        return

    slug = company_data.get("slug", "")
    if any(c.get("slug") == slug for c in existing):
        return

    existing.append(company_data)

    if not existing:
        return
    seen: set[str] = set()
    fieldnames: list[str] = []
    for row in existing:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, lineterminator="\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    writer.writerows(existing)

    tmp_path = csv_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(str(tmp_path), str(csv_path))
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        csv_path.write_text(buf.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core sync logic — process one booking event
# ---------------------------------------------------------------------------

def sync_one_event(
    event: dict,
    contacts_lookup: dict[str, dict[str, str]],
    companies_lookup: dict[str, dict[str, str]],
    enrich: bool = True,
    dry_run: bool = False,
) -> dict:
    """Process a single appointment booking — add/update CRM contacts.

    Uses structured data from the booking form (name, email, company, LinkedIn)
    as the primary source. Falls back to Exa enrichment for missing fields.
    """
    event_id = event.get("id", "")
    title = event.get("title", "(no title)")
    booking = event.get("booking_data", {})

    # Extract event date
    start = event.get("start", "")
    if start:
        try:
            event_date = datetime.fromisoformat(start.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            event_date = datetime.now().strftime("%Y-%m-%d")
    else:
        event_date = datetime.now().strftime("%Y-%m-%d")

    new_contacts: list[str] = []
    updated_contacts: list[str] = []
    new_companies: list[str] = []
    updates_by_slug: dict[str, dict[str, str]] = {}

    # Primary: use booking form email. Fallback: scan attendees.
    booking_email = booking.get("email", "").strip().lower()
    attendee_emails = [e.strip().lower() for e in event.get("attendees", [])]

    # Determine the booker's email
    target_emails: list[str] = []
    if booking_email:
        target_emails.append(booking_email)
        # Also include other non-internal attendees (booker might have added colleagues)
        for e in attendee_emails:
            if e != booking_email and not _is_internal_email(e):
                target_emails.append(e)
    else:
        target_emails = [e for e in attendee_emails if not _is_internal_email(e)]

    for email in target_emails:
        if not email:
            continue

        domain = _email_to_domain(email)
        is_primary_booker = (email == booking_email)

        if email in contacts_lookup:
            # Existing contact — update meeting dates and fill missing fields
            contact = contacts_lookup[email]
            slug = contact.get("slug", "")
            if slug:
                updates: dict[str, str] = {
                    "last_contact_date": event_date,
                    "last_contact_channel": "meeting",
                    "last_meeting_date": event_date,
                }
                # Advance stage if early
                current_stage = contact.get("stage", "")
                if current_stage in {"lead", "contacted", "replied", ""}:
                    updates["stage"] = "meeting_booked"

                # Fill missing fields from booking data (for primary booker only)
                if is_primary_booker:
                    if not contact.get("linkedin_url") and booking.get("linkedin_url"):
                        updates["linkedin_url"] = booking["linkedin_url"]
                    if not contact.get("company") and booking.get("company"):
                        updates["company"] = booking["company"]

                updates_by_slug[slug] = updates
                display = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
                updated_contacts.append(f"{display} ({email})")
        else:
            # New contact — use booking form data first, then fallback
            if is_primary_booker and booking.get("full_name"):
                name_parts = booking["full_name"].split(None, 1)
                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""
            else:
                first_name, last_name = _name_from_email(email)

            slug = _make_slug(first_name, last_name)

            # Ensure unique slug
            existing_slugs = {c.get("slug") for c in contacts_lookup.values()}
            base_slug = slug
            counter = 2
            while slug in existing_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1

            # Determine booking type from title
            booking_type = "intro" if "intro" in title.lower() else "demo" if "demo" in title.lower() else "meeting"

            new_contact: dict[str, str] = {col: "" for col in _CONTACTS_COLUMNS}
            new_contact.update({
                "slug": slug,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "linkedin_url": booking.get("linkedin_url", "") if is_primary_booker else "",
                "company": booking.get("company", "") if is_primary_booker else "",
                "stage": "meeting_booked",
                "source": "inbound",
                "first_contact_date": event_date,
                "last_contact_date": event_date,
                "last_contact_channel": "meeting",
                "last_meeting_date": event_date,
                "priority": "medium",
                "notes": f"Booked {booking_type} via appointment link ({event_date})",
            })

            # Enrich via Exa only if booking form didn't provide the data
            if enrich and is_primary_booker:
                if not new_contact["linkedin_url"]:
                    person_data = enrich_person_via_exa(
                        first_name, last_name, booking.get("company") or domain,
                    )
                    if person_data.get("linkedin_url"):
                        new_contact["linkedin_url"] = person_data["linkedin_url"]
                    if person_data.get("role"):
                        new_contact["role"] = person_data["role"]

            if not dry_run:
                try:
                    crm.append_contact(new_contact)
                except Exception:
                    pass

            contacts_lookup[email] = new_contact
            display = f"{first_name} {last_name}".strip()
            new_contacts.append(f"{display} ({email})")

            # Create company if not exists (skip free email providers)
            company_name = booking.get("company", "") if is_primary_booker else ""
            if domain and domain not in companies_lookup and domain not in FREE_EMAIL_DOMAINS:
                company_data: dict[str, str] = {col: "" for col in _COMPANIES_COLUMNS}
                company_slug = _make_company_slug(company_name or domain.split(".")[0])

                company_data.update({
                    "slug": company_slug,
                    "name": company_name or domain.split(".")[0].capitalize(),
                    "website": f"https://{domain}",
                    "stage": "evaluating",
                    "source": "inbound",
                    "notes": f"Auto-added from {booking_type} booking ({event_date})",
                })

                # Enrich company via Exa
                if enrich:
                    co_data = enrich_company_via_exa(domain)
                    if co_data.get("name") and not company_name:
                        company_data["name"] = co_data["name"]
                    if co_data.get("industry"):
                        company_data["industry"] = co_data["industry"]
                    if co_data.get("size"):
                        company_data["size"] = co_data["size"]
                    if co_data.get("location"):
                        company_data["location"] = co_data["location"]

                if not dry_run:
                    _append_company(company_data)

                companies_lookup[domain] = company_data
                new_companies.append(company_data.get("name", domain))

    # Batch-update existing contacts
    if updates_by_slug and not dry_run:
        try:
            crm.batch_update_contacts(updates_by_slug)
        except Exception:
            pass

    return {
        "event_id": event_id,
        "title": title,
        "date": event_date,
        "booking_type": "intro" if "intro" in title.lower() else "demo" if "demo" in title.lower() else "meeting",
        "new_contacts": new_contacts,
        "updated_contacts": updated_contacts,
        "new_companies": new_companies,
    }

"""CRM utilities — path resolution and CSV operations for a knowledge-base CRM.

This is part of the OPTIONAL knowledge-base connector. It reads configuration
from the knowledge base's ``.claude/knowledge-base.local.md`` (discovered by
``gw_config.find_kb_config``) to resolve CRM paths — nothing is hardcoded. When
no knowledge base is configured, callers get a clear CliError and connector
commands no-op; the core Google services are unaffected.

CSV master tables are the single source of truth for structured CRM data.
Optional markdown files in contacts/ and companies/ provide rich context.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import gw_config
from output import CliError

# Backwards-compatible alias — the shared parser now lives in gw_config.
_parse_frontmatter = gw_config.parse_frontmatter


_cached_config: dict[str, str] | None = None


def load_config() -> dict[str, str]:
    """Load knowledge-base config and return as a dict.

    Expected keys: vault_path, company_dir, crm_dir
    Result is cached after first successful load.
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    config_path = gw_config.find_kb_config()
    if config_path is None:
        raise CliError(
            "Knowledge base not configured.",
            suggestion=gw_config.KB_NOT_CONFIGURED,
        )
    content = config_path.read_text(encoding="utf-8")
    config = gw_config.parse_frontmatter(content)

    for key in ("vault_path", "company_dir", "crm_dir"):
        if key not in config:
            raise CliError(
                f"Missing '{key}' in knowledge-base config",
                suggestion=f"Add '{key}' to {config_path}",
            )
    _cached_config = config
    return config


def crm_path() -> Path:
    """Resolve the full CRM directory path from config."""
    config = load_config()
    return Path(config["vault_path"]) / config["company_dir"] / config["crm_dir"]


def contacts_csv_path() -> Path:
    """Resolve the contacts.csv path."""
    return crm_path() / "contacts.csv"


def companies_csv_path() -> Path:
    """Resolve the companies.csv path."""
    return crm_path() / "companies.csv"


def campaigns_dir() -> Path:
    """Resolve the campaigns directory path."""
    return crm_path() / "campaigns"


def templates_dir() -> Path:
    """Resolve the CRM templates directory path."""
    return crm_path() / "templates"


def contacts_dir() -> Path:
    """Resolve the contacts markdown directory path."""
    return crm_path() / "contacts"


def companies_dir() -> Path:
    """Resolve the companies markdown directory path."""
    return crm_path() / "companies"


def read_contacts_csv() -> list[dict[str, str]]:
    """Read all contacts from the master CSV. Returns list of dicts."""
    path = contacts_csv_path()
    if not path.exists():
        raise CliError(
            f"Contacts CSV not found: {path}",
            suggestion="Initialize the CRM by creating contacts.csv",
        )
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def read_companies_csv() -> list[dict[str, str]]:
    """Read all companies from the master CSV. Returns list of dicts."""
    path = companies_csv_path()
    if not path.exists():
        raise CliError(
            f"Companies CSV not found: {path}",
            suggestion="Initialize the CRM by creating companies.csv",
        )
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def read_campaign_csv(campaign_name: str) -> list[dict[str, str]]:
    """Read a campaign CSV by name. Returns list of dicts."""
    if "/" in campaign_name or "\\" in campaign_name or ".." in campaign_name:
        raise CliError(
            f"Invalid campaign name: {campaign_name}",
            suggestion="Campaign name must not contain path separators.",
        )
    path = campaigns_dir() / f"{campaign_name}.csv"
    if not path.exists():
        raise CliError(
            f"Campaign CSV not found: {path}",
            suggestion=f"Check campaigns/ directory for available campaigns.",
        )
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def find_contact(slug: str) -> dict[str, str] | None:
    """Find a single contact by slug. Returns None if not found."""
    for contact in read_contacts_csv():
        if contact.get("slug") == slug:
            return contact
    return None


def find_contacts_by_stage(stage: str) -> list[dict[str, str]]:
    """Find all contacts at a given pipeline stage."""
    return [c for c in read_contacts_csv() if c.get("stage") == stage]


def find_contacts_by_company(company: str) -> list[dict[str, str]]:
    """Find all contacts at a given company (case-insensitive)."""
    company_lower = company.lower()
    return [c for c in read_contacts_csv() if c.get("company", "").lower() == company_lower]


def update_contact(slug: str, updates: dict[str, str]) -> None:
    """Update a contact's fields in the CSV by slug.

    Only updates fields that are present in the updates dict.
    Preserves all other fields and rows.
    """
    path = contacts_csv_path()
    contacts = read_contacts_csv()

    found = False
    for contact in contacts:
        if contact.get("slug") == slug:
            contact.update(updates)
            found = True
            break

    if not found:
        raise CliError(
            f"Contact not found: {slug}",
            suggestion="Check contacts.csv for valid slugs.",
        )

    _write_contacts_csv(contacts)


def batch_update_contacts(updates_by_slug: dict[str, dict[str, str]]) -> None:
    """Batch-update multiple contacts in contacts.csv — single read + single write.

    Much faster than calling update_contact() in a loop, which does
    a full file read and write per contact.
    """
    if not updates_by_slug:
        return
    path = contacts_csv_path()
    contacts = read_contacts_csv()

    for contact in contacts:
        slug = contact.get("slug", "")
        if slug in updates_by_slug:
            contact.update(updates_by_slug[slug])

    _write_contacts_csv(contacts)


def append_contact(contact: dict[str, str]) -> None:
    """Append a new contact row to contacts.csv."""
    path = contacts_csv_path()
    existing = read_contacts_csv()

    # Check for duplicate slug
    slug = contact.get("slug", "")
    if any(c.get("slug") == slug for c in existing):
        raise CliError(
            f"Contact with slug '{slug}' already exists",
            suggestion="Use update_contact() to modify existing contacts.",
        )

    existing.append(contact)
    _write_contacts_csv(existing)


def update_campaign_row(campaign_name: str, slug: str, updates: dict[str, str]) -> None:
    """Update a row in a campaign CSV by contact slug."""
    if "/" in campaign_name or "\\" in campaign_name or ".." in campaign_name:
        raise CliError(
            f"Invalid campaign name: {campaign_name}",
            suggestion="Campaign name must not contain path separators.",
        )
    path = campaigns_dir() / f"{campaign_name}.csv"
    rows = read_campaign_csv(campaign_name)

    found = False
    for row in rows:
        if row.get("slug") == slug:
            row.update(updates)
            found = True
            break

    if not found:
        raise CliError(
            f"Contact '{slug}' not found in campaign '{campaign_name}'",
        )

    _write_csv(path, rows)


def batch_update_campaign_rows(campaign_name: str, updates_by_slug: dict[str, dict[str, str]]) -> None:
    """Batch-update multiple rows in a campaign CSV — single read + single write.

    Much faster than calling update_campaign_row() in a loop, which does
    a full file read and write per row.
    """
    if not updates_by_slug:
        return
    if "/" in campaign_name or "\\" in campaign_name or ".." in campaign_name:
        raise CliError(
            f"Invalid campaign name: {campaign_name}",
            suggestion="Campaign name must not contain path separators.",
        )
    path = campaigns_dir() / f"{campaign_name}.csv"
    rows = read_campaign_csv(campaign_name)

    for row in rows:
        slug = row.get("slug", "")
        if slug in updates_by_slug:
            row.update(updates_by_slug[slug])

    _write_csv(path, rows)


def contact_has_detail(slug: str) -> bool:
    """Check if a contact has a markdown detail file."""
    return (contacts_dir() / f"{slug}.md").exists()


def company_has_detail(slug: str) -> bool:
    """Check if a company has a markdown detail file."""
    return (companies_dir() / f"{slug}.md").exists()


def _write_contacts_csv(contacts: list[dict[str, str]]) -> None:
    """Write the full contacts list back to contacts.csv."""
    _write_csv(contacts_csv_path(), contacts)


def _sanitize_csv_value(value: str) -> str:
    """Prevent CSV formula injection by escaping dangerous prefixes.

    Values starting with =, @, tab, or carriage return can be
    interpreted as formulas by spreadsheet applications. We prefix
    them with a single quote to neutralize injection.

    Note: + and - are excluded because they appear in normal data
    (phone numbers, markdown bullets) and the single-quote prefix
    is not stripped by Python's csv.DictReader on read-back.
    """
    if value and value[0] in ("=", "@", "\t", "\r"):
        return f"'{value}"
    return value


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a list of dicts to a CSV file, preserving column order from the first row.

    Uses atomic write pattern: write to temp file then os.replace().
    On POSIX this is truly atomic; on Windows it is best-effort but
    still prevents data loss from interrupted writes.
    All fields are quoted to safely handle special characters.
    """
    if not rows:
        return

    # Collect fieldnames from all rows — preserves order from first row,
    # then appends any new fields added via update operations.
    seen: set[str] = set()
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    # Sanitize values that could trigger spreadsheet formula execution
    sanitized_rows = [
        {k: _sanitize_csv_value(str(v)) for k, v in row.items()}
        for row in rows
    ]

    # Write to string buffer first — quote all fields for safe round-tripping
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, lineterminator="\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    writer.writerows(sanitized_rows)
    content = buf.getvalue()

    # Atomic write: temp file in same directory → os.replace()
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except OSError:
        # Fallback: direct write (Windows edge cases)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        path.write_text(content, encoding="utf-8")

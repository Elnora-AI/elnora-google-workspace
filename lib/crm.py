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
import uuid
from pathlib import Path

import gw_config
from output import CliError

# Backwards-compatible alias — the shared parser now lives in gw_config.
_parse_frontmatter = gw_config.parse_frontmatter


_cached_config: dict[str, str] | None = None

# Canonical contacts.csv schema — the single source of truth for the CRM table.
# `gw crm init` scaffolds this header; email_crm_sync imports it so the writer and
# the scaffolder can never drift. Keep additive: append new columns at the end.
CONTACTS_COLUMNS = [
    "slug", "first_name", "last_name", "email", "linkedin_url", "company",
    "role", "stage", "source", "campaign", "first_contact_date",
    "last_contact_date", "last_contact_channel", "last_meeting_date",
    "next_action", "next_action_date", "priority", "notes",
]

# Minimal companies.csv schema, scaffolded alongside contacts.csv.
COMPANIES_COLUMNS = ["slug", "name", "domain", "industry", "stage", "notes"]


def load_config() -> dict[str, str]:
    """Load knowledge-base config and return as a dict.

    Only ``vault_path`` is required — the file that any knowledge-vault-style
    plugin writes. ``company_dir`` defaults to empty (CRM lives directly under
    the vault) and ``crm_dir`` defaults to ``crm``, so a fresh vault gets a
    working CRM at ``<vault>/crm`` with zero extra configuration. Configs that
    set those keys explicitly (to nest the CRM elsewhere in the vault) keep
    working unchanged. ``investors_dir`` is opt-in with no default: it names
    the vault folder that holds ``investor-contacts.csv`` (a sibling of the CRM
    dir, not a child of it), and when it is unset no investor file is read.
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

    if "vault_path" not in config:
        raise CliError(
            "Missing 'vault_path' in knowledge-base config",
            suggestion=f"Add 'vault_path' to {config_path}",
        )
    config.setdefault("company_dir", "")
    config.setdefault("crm_dir", "crm")
    _cached_config = config
    return config


def init_crm() -> dict[str, list[str]]:
    """Scaffold an empty CRM (``contacts.csv`` + ``companies.csv``) under the vault.

    Creates the CRM directory and header-only CSVs if they don't already exist.
    Idempotent: existing files are left untouched (never overwritten). Returns
    ``{"created": [...], "existing": [...]}`` of absolute paths for reporting.
    """
    created: list[str] = []
    existing: list[str] = []
    crm_path().mkdir(parents=True, exist_ok=True)
    for path, columns in (
        (contacts_csv_path(), CONTACTS_COLUMNS),
        (companies_csv_path(), COMPANIES_COLUMNS),
    ):
        if path.exists():
            existing.append(str(path))
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_ALL).writerow(columns)
        created.append(str(path))
    return {"created": created, "existing": existing}


def crm_path() -> Path:
    """Resolve the full CRM directory path from config."""
    config = load_config()
    return Path(config["vault_path"]) / config["company_dir"] / config["crm_dir"]


def require_crm_dir() -> None:
    """Refuse a CRM sync unless ``crm_dir`` is set in the knowledge-base config.

    Readers and ``gw crm init`` fall back to ``<vault>/crm``. The sync jobs write
    the CSVs unattended, so they write only to a CRM folder the config names.
    """
    config_path = gw_config.find_kb_config()
    content = config_path.read_text(encoding="utf-8") if config_path else ""
    if not gw_config.parse_frontmatter(content).get("crm_dir"):
        raise CliError(
            "CRM sync refused: no CRM CSV path is configured. The sync writes "
            "contacts.csv and companies.csv only to a folder you name explicitly.",
            suggestion=(
                "Add 'crm_dir: <folder>' (e.g. 'crm_dir: crm') to "
                f"{config_path or '.claude/knowledge-base.local.md'}, "
                "or leave CRM sync off."
            ),
        )


def contacts_csv_path() -> Path:
    """Resolve the contacts.csv path."""
    return crm_path() / "contacts.csv"


def companies_csv_path() -> Path:
    """Resolve the companies.csv path."""
    return crm_path() / "companies.csv"


def investor_contacts_csv_path() -> Path | None:
    """Resolve the investor-contacts.csv path inside ``investors_dir``.

    Investor contacts live beside the CRM dir, not inside it: they are their
    own table (it carries ``do_not_email``) and outreach reads it only as a
    suppression list. Returns None when ``investors_dir`` is not configured.
    """
    config = load_config()
    investors_dir = config.get("investors_dir", "")
    if not investors_dir:
        return None
    return (
        Path(config["vault_path"]) / config["company_dir"]
        / investors_dir / "investor-contacts.csv"
    )


def suppression_csv_path() -> Path:
    """Resolve the global do-not-contact list inside ``crm_dir``.

    One file for the whole estate, deliberately not per-campaign: an opt-out
    covers all commercial mail from the sender (CAN-SPAM) and carries no
    expiry (GDPR Art. 21(3)), so a campaign-scoped list would let the next
    campaign re-contact someone who asked to be left alone.
    """
    return crm_path() / "suppression.csv"


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
            suggestion="Check campaigns/ directory for available campaigns.",
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
    contacts = read_contacts_csv()

    for contact in contacts:
        slug = contact.get("slug", "")
        if slug in updates_by_slug:
            contact.update(updates_by_slug[slug])

    _write_contacts_csv(contacts)


def append_contact(contact: dict[str, str]) -> None:
    """Append a new contact row to contacts.csv."""
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


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a per-process temp file, then os.replace().

    The temp name carries the writer's pid and a random suffix, so two CRM
    writers running at the same time never share a scratch file and can never
    promote each other's half-written bytes into the real CSV. On POSIX the
    replace is atomic; on Windows it is best-effort but still prevents data
    loss from an interrupted write.

    This is the single write path for every CRM CSV in the plugin — callers
    render their own content (each keeps its own sanitisation and quoting) and
    hand it here.
    """
    tmp_path = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except OSError:
        # Never fall back to writing the real file in place. A direct write
        # truncates before it fills, so a failure partway through leaves the
        # CSV short, which is the loss this function exists to prevent. The
        # vault sits on a Drive File Stream mount where OSError is an ordinary
        # transient, so that fallback would fire exactly when it does the most
        # damage. Drop the scratch file and let the caller see the failure.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a list of dicts to a CSV file, preserving column order from the first row.

    Persisted through ``atomic_write_text`` (unique temp file → os.replace).
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

    atomic_write_text(path, buf.getvalue())

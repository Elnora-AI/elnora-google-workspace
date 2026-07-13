"""Optional knowledge-base connector configuration.

The core Google services (Gmail, Calendar, Drive, Docs, Sheets, Forms, Tasks,
and ``gw api``) need NOTHING here — they work fully without any configuration.
These helpers only gate the OPTIONAL CRM / meeting-prep / outreach connector,
which links this plugin to a knowledge base such as the ``knowledge-vault``
plugin. Every setting is optional and every connector feature degrades to a
clean no-op when it is unset, so the core CLI is unaffected.

Environment variables (all optional):
  GW_INTERNAL_DOMAINS  Comma-separated email domains treated as "internal" and
                       skipped by CRM sync (e.g. ``acme.com,mail.acme.com``).
                       Default: empty — nothing is treated as internal.
  GW_SLACK_USER_ID     Slack user id to DM meeting briefs to. Default: unset —
                       briefs print to stdout only.
  GW_SLACK_CLI_BIN     Path to a Slack CLI entry point used to deliver the DM.
                       Default: unset — no Slack delivery.
  GW_TRANSCRIPT_DIRS   Comma-separated meeting-transcript directories (relative
                       to the knowledge base's company dir, or absolute) that
                       meeting-prep scans. Default: empty — CRM-only briefs.
  GW_EXA_LIB           Path to an Exa CLI ``lib`` dir enabling optional contact
                       enrichment during CRM sync. Default: unset — no
                       enrichment.
  GW_KB_CONFIG         Explicit path to the knowledge-base config markdown file.
                       Default: discovered by walking up from the current
                       directory for ``.claude/knowledge-base.local.md``.
  GW_CONFIG_DIR        Base config/cache dir. Default: ``~/.config/gw`` (mirrors
                       the auth layer).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Shown when a connector command is invoked without a knowledge base configured.
KB_NOT_CONFIGURED = (
    "Knowledge base not configured; skipping CRM. Install the knowledge-vault "
    "plugin (Elnora-AI/knowledge-vault) and run its setup to enable CRM sync, "
    "meeting prep, and outreach. The core Google commands need no knowledge base."
)


def config_dir() -> Path:
    """Base config/cache dir: ``$GW_CONFIG_DIR`` override, default ``~/.config/gw``.

    Mirrors ``auth.get_config_dir`` (kept dependency-free here so importing this
    module never pulls in the Google auth stack).
    """
    env = os.environ.get("GW_CONFIG_DIR")
    if not env:
        return Path.home() / ".config" / "gw"
    path = Path(env).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def cache_dir() -> Path:
    """Writable per-user cache dir for connector state files."""
    return config_dir() / "cache"


def internal_domains() -> set[str]:
    """Email domains to treat as internal (skipped by CRM sync). Default: none."""
    raw = os.environ.get("GW_INTERNAL_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def slack_user_id() -> str | None:
    """Slack user id to DM meeting briefs to, or None if unset."""
    return os.environ.get("GW_SLACK_USER_ID") or None


def slack_cli_bin() -> Path | None:
    """Path to a Slack CLI entry point for DM delivery, or None if unset."""
    value = os.environ.get("GW_SLACK_CLI_BIN")
    return Path(value).expanduser() if value else None


def transcript_dirs() -> list[str]:
    """Meeting-transcript directories to scan (relative or absolute). Default: none."""
    raw = os.environ.get("GW_TRANSCRIPT_DIRS", "")
    return [d.strip() for d in raw.split(",") if d.strip()]


def exa_lib() -> Path | None:
    """Path to an Exa CLI ``lib`` dir for optional enrichment, or None if unset."""
    value = os.environ.get("GW_EXA_LIB")
    return Path(value).expanduser() if value else None


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse simple ``key: value`` YAML frontmatter from a markdown file."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def find_kb_config() -> Path | None:
    """Locate the knowledge-base config markdown file, or None if absent.

    Order: ``$GW_KB_CONFIG`` (explicit path), else walk up from the current
    directory looking for ``.claude/knowledge-base.local.md`` (the file the
    ``knowledge-vault`` plugin writes in the user's project).
    """
    env = os.environ.get("GW_KB_CONFIG")
    if env:
        path = Path(env).expanduser()
        return path if path.exists() else None
    try:
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / ".claude" / "knowledge-base.local.md"
            if candidate.exists():
                return candidate
    except OSError:
        pass
    return None


def kb_configured() -> bool:
    """True if a knowledge-base config file is present."""
    return find_kb_config() is not None

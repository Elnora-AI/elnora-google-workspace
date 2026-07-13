"""Shared template loading and personalization utilities.

Used by cold_outreach_agent.py. Centralizes path traversal prevention,
template parsing, and placeholder formatting.

Template resolution order:
  1. Vault CRM templates directory (from knowledge-base config)
  2. Local plugin templates directory (fallback)
"""

from __future__ import annotations

import re
from pathlib import Path

from output import CliError

# Local fallback templates directory
_LOCAL_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Lazy-loaded vault templates directory (resolved from knowledge-base config)
_vault_templates_dir: Path | None = None
_vault_templates_checked = False


def _get_vault_templates_dir() -> Path | None:
    """Resolve vault CRM templates directory from knowledge-base config.

    Returns None if config is missing or CRM is not configured.
    Result is cached after first call.
    """
    global _vault_templates_dir, _vault_templates_checked
    if _vault_templates_checked:
        return _vault_templates_dir
    _vault_templates_checked = True
    try:
        import crm
        _vault_templates_dir = crm.templates_dir()
        if not _vault_templates_dir.exists():
            _vault_templates_dir = None
    except (ImportError, Exception):
        _vault_templates_dir = None
    return _vault_templates_dir


def _resolve_templates_dir() -> Path:
    """Return the active templates directory (vault first, local fallback)."""
    vault_dir = _get_vault_templates_dir()
    if vault_dir is not None:
        return vault_dir
    return _LOCAL_TEMPLATES_DIR


# Keep module-level reference for backwards compatibility
TEMPLATES_DIR = _LOCAL_TEMPLATES_DIR


def _validate_template_name(name: str) -> None:
    """Raise CliError if name contains path traversal characters."""
    if "/" in name or "\\" in name or ".." in name:
        raise CliError(
            f"Invalid template name: {name}",
            suggestion="Template name must be a simple name without path separators.",
        )


def _resolve_template_path(name: str) -> Path:
    """Resolve and validate a template path.

    Checks vault CRM templates first, falls back to local plugin templates.
    """
    _validate_template_name(name)
    templates = _resolve_templates_dir()
    path = templates / f"{name}.md"
    if not path.resolve().is_relative_to(templates.resolve()):
        raise CliError(
            f"Invalid template path: {name}",
            suggestion="Template name must be a simple name without path separators.",
        )
    # If not found in vault, try local fallback
    if not path.exists() and templates != _LOCAL_TEMPLATES_DIR:
        local_path = _LOCAL_TEMPLATES_DIR / f"{name}.md"
        if local_path.exists() and local_path.resolve().is_relative_to(_LOCAL_TEMPLATES_DIR.resolve()):
            return local_path
    return path


_template_cache: dict[str, str] = {}


def load_template(name: str) -> str:
    """Load a template file by name. Returns the full file content.

    Searches vault CRM templates first, then local plugin templates.
    Result is cached to avoid re-reading the same file multiple times
    (e.g. parse_template + _parse_template_goal in the same invocation).
    """
    if name in _template_cache:
        return _template_cache[name]
    path = _resolve_template_path(name)
    if not path.exists():
        raise CliError(
            f"Template not found: {name}.md",
            suggestion="Check the templates/ directory for available templates.",
        )
    content = path.read_text(encoding="utf-8")
    _template_cache[name] = content
    return content


def parse_template(name: str) -> tuple[str, str]:
    """Load and parse a template into (subject_template, body_template).

    Extracts the template block between ``` markers under ## Template.
    Returns (subject_template, body_template).
    """
    content = load_template(name)

    template_match = re.search(r"## Template\s*\n```\n(.*?)```", content, re.DOTALL)
    if not template_match:
        raise CliError(
            f"Could not parse template block from {name}.md",
            suggestion="Template must have a '## Template' section with a fenced code block",
        )

    template_text = template_match.group(1).strip()

    lines = template_text.split("\n", 1)
    subject_line = ""
    body = template_text

    if lines[0].startswith("Subject:"):
        subject_line = lines[0].replace("Subject:", "").strip()
        body = lines[1].strip() if len(lines) > 1 else ""

    if not body.strip():
        raise CliError(
            f"Template {name}.md has an empty body",
            suggestion="Add email body text after the Subject: line in the template block.",
        )

    return subject_line, body


def safe_format(template: str, data: dict) -> str:
    """Replace {key} placeholders without supporting attribute access or format specs."""
    def _replacer(match):
        return str(data.get(match.group(1), "[MISSING]"))
    return re.sub(r"\{(\w+)\}", _replacer, template)



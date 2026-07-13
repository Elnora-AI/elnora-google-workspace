#!/usr/bin/env python3
"""Google Workspace CLI — agent-friendly interface to Gmail, Sheets, Calendar, Tasks.

Token-efficient JSON output for agent workflows.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Add lib/ and cli/ to path so we can import our modules
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_CLI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))
sys.path.insert(0, str(_CLI_DIR))

import click

from output import (
    _scrub_credentials,
    set_field_filter,
    set_output_format,
)

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# Global crash handler — redacts secrets from tracebacks
# ---------------------------------------------------------------------------

def _crash_handler(exc_type, exc_value, exc_tb):
    """Last-resort handler: print scrubbed traceback to stderr as JSON."""
    raw = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    scrubbed = _scrub_credentials(raw)
    payload = {
        "error": _scrub_credentials(str(exc_value)),
        "code": "UNHANDLED_EXCEPTION",
        "traceback": scrubbed,
    }
    print(json.dumps(payload, indent=2), file=sys.stderr)
    sys.exit(1)


sys.excepthook = _crash_handler


# ---------------------------------------------------------------------------
# Shared option decorators — used by all command modules
# ---------------------------------------------------------------------------

_account_option = click.option(
    "--account", type=str, default=None,
    help="Account name from accounts.json or a legacy token file (default: config 'default', else 'main'). See 'gw auth list'.",
)

_compact_option = click.option(
    "--compact", is_flag=True, help="Compact JSON output (saves tokens)",
)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--compact", is_flag=True, default=False, help="Minimal JSON (no indentation).")
@click.option("--output", "output_format", type=click.Choice(["json", "table", "csv"]), default="json", help="Output format.")
@click.option("--fields", type=str, default=None, help="Comma-separated fields to include in output.")
@click.option("--no-color", is_flag=True, default=False, help="Disable colored output.")
@click.version_option(__version__, prog_name="gw")
@click.pass_context
def cli(ctx, compact: bool, output_format: str, fields: str | None, no_color: bool):
    """Google Workspace CLI — agent-friendly Gmail, Calendar, Drive, Docs, Sheets, Forms, and Tasks."""
    ctx.ensure_object(dict)
    ctx.obj["compact"] = compact

    # Set global output state
    set_output_format(output_format)
    if fields:
        set_field_filter([f.strip() for f in fields.split(",") if f.strip()])
    else:
        set_field_filter(None)

    if no_color:
        os.environ["NO_COLOR"] = "1"


# ---------------------------------------------------------------------------
# Register commands from commands/
# ---------------------------------------------------------------------------

from commands.api import register as register_api
from commands.auth import register as register_auth
from commands.gmail import register as register_gmail
from commands.sheets import register as register_sheets
from commands.calendar import register as register_calendar
from commands.tasks import register as register_tasks
from commands.docs import register as register_docs
from commands.forms import register as register_forms
from commands.drive import register as register_drive
from commands.completion import completion

register_api(cli, _account_option, _compact_option)
register_auth(cli, _account_option, _compact_option)
register_gmail(cli, _account_option, _compact_option)
register_sheets(cli, _account_option, _compact_option)
register_calendar(cli, _account_option, _compact_option)
register_tasks(cli, _account_option, _compact_option)
register_docs(cli, _account_option, _compact_option)
register_forms(cli, _account_option, _compact_option)
register_drive(cli, _account_option, _compact_option)
cli.add_command(completion)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()

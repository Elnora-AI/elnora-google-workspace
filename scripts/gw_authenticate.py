"""DEPRECATED — use ``gw auth login --account <name>`` instead.

Thin shim kept for backward compatibility. Delegates to the ``gw auth login``
path (OAuth client resolution: GW_CLIENT_ID/GW_CLIENT_SECRET env →
<config-dir>/client_secret.json → client embedded in an existing legacy
token file).

Usage (from repo root):
    python3 scripts/gw_authenticate.py --account personal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "cli"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--account",
        required=True,
        help="Account name to authenticate (free-form; see 'gw auth list')",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-authenticate even if a valid token already exists.",
    )
    args = parser.parse_args()

    print(
        "DEPRECATED: gw_authenticate.py is a compatibility shim. "
        f"Use: gw auth login --account {args.account}",
        file=sys.stderr,
    )

    import auth
    from commands.auth import perform_login
    from output import AuthError, CliError

    try:
        auth._validate_account_name(args.account)
    except AuthError as err:
        print(err.message, file=sys.stderr)
        return err.exit_code

    # Idempotence guard: don't launch a browser OAuth flow when a valid token
    # already exists unless --force is given (matches the pre-refactor contract).
    if not args.force:
        try:
            auth.get_credentials(args.account)
            print(
                f"Token already exists for '{args.account}'. Pass --force to re-authenticate.",
                file=sys.stderr,
            )
            return 0
        except CliError:
            pass  # no valid token — proceed to authenticate

    try:
        result = perform_login(account=args.account)
    except CliError as err:
        print(err.message, file=sys.stderr)
        if err.suggestion:
            print(err.suggestion, file=sys.stderr)
        return err.exit_code

    # Print an explicit field allowlist — never the raw login result, which
    # may grow credential-adjacent fields (e.g. token storage paths).
    summary = {
        key: result[key]
        for key in ("authenticated", "account", "storage", "scopes", "email")
        if key in result
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

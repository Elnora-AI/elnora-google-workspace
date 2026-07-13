"""Google Workspace CLI — auth commands (login, list, status, logout, setup)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import click

from output import (
    AuthError,
    ValidationError,
    output_success,
    output_warning,
    _handle_errors,
)


# ---------------------------------------------------------------------------
# OAuth client resolution
# ---------------------------------------------------------------------------

def _installed_client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _client_config_from_file(path: Path) -> dict:
    """Load a Google client_secret.json ("installed" or "web" shape)."""
    with open(path) as f:
        data = json.load(f)
    section = data.get("installed") or data.get("web") or data
    client_id = section.get("client_id")
    client_secret = section.get("client_secret")
    if not client_id or not client_secret:
        raise ValidationError(
            f"No client_id/client_secret found in {path}.",
            suggestion="Download the Desktop OAuth client JSON from Google Cloud Console.",
        )
    return _installed_client_config(client_id, client_secret)


def _resolve_oauth_client(client_secret_file: str | None) -> tuple[dict, str]:
    """Resolve the OAuth client. Returns (client_config, source label).

    Precedence (explicit beats ambient): --client-secret-file →
    GW_CLIENT_ID/GW_CLIENT_SECRET env → <config-dir>/client_secret.json →
    client embedded in an existing legacy token file.

    The source label is a fixed, non-sensitive category — never a filesystem
    path or a secret-file name — so it is safe to include in printed output.
    """
    import auth

    # An explicit per-invocation flag wins over any ambient env/config default.
    if client_secret_file:
        return _client_config_from_file(Path(client_secret_file)), "client-secret-file"

    env_id = os.environ.get("GW_CLIENT_ID")
    env_secret = os.environ.get("GW_CLIENT_SECRET")
    if env_id and env_secret:
        return _installed_client_config(env_id, env_secret), "env"

    config_secret = auth.get_config_dir() / "client_secret.json"
    if config_secret.exists():
        return _client_config_from_file(config_secret), "config-dir"

    for name, token_path in auth.discover_legacy_accounts().items():
        try:
            with open(token_path) as f:
                data = json.load(f)
        except (ValueError, OSError):
            continue
        if data.get("client_id") and data.get("client_secret"):
            return (
                _installed_client_config(data["client_id"], data["client_secret"]),
                "legacy-token",
            )

    raise AuthError(
        "No OAuth client found (env, client_secret.json, or existing token).",
        suggestion=(
            "Run: gw auth setup  (guided Google Cloud checklist)\n"
            f"Then save the Desktop OAuth client JSON to {config_secret}"
        ),
    )


# ---------------------------------------------------------------------------
# Login (also used by the deprecated scripts/gw_authenticate.py shim)
# ---------------------------------------------------------------------------

def perform_login(
    account: str | None = None,
    email: str | None = None,
    scopes: str | None = None,
    readonly: bool = False,
    client_secret_file: str | None = None,
    port: int = 0,
    no_browser: bool = False,
    no_keyring: bool = False,
    keyring: bool = False,
) -> dict:
    """Run the OAuth flow and persist the token. Returns a result payload."""
    import auth

    cfg = auth.load_accounts_config()
    default = cfg.get("default")
    name = account or (default if isinstance(default, str) else None) or auth.DEFAULT_ACCOUNT
    auth._validate_account_name(name)

    services = None
    if scopes:
        services = [s.strip() for s in scopes.split(",") if s.strip()]
    try:
        scope_list = auth.scopes_for(services, readonly=readonly)
    except ValueError as err:
        raise ValidationError(str(err)) from err

    client_config, client_source = _resolve_oauth_client(client_secret_file)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(client_config, scope_list)
    creds = flow.run_local_server(
        port=port,
        login_hint=email,
        prompt="consent",
        access_type="offline",
        open_browser=not no_browser,
    )

    if not creds.refresh_token:
        output_warning(
            "No refresh_token returned — revoke prior access at "
            "https://myaccount.google.com/permissions and log in again.",
            code="NO_REFRESH_TOKEN",
        )

    # File is the default backend. Keyring is used only on explicit opt-in
    # (--keyring) or when the account's existing config entry already records
    # keyring — a legacy file token is never silently migrated to the keyring.
    accounts_cfg = cfg.get("accounts")
    existing_entry = accounts_cfg.get(name) if isinstance(accounts_cfg, dict) else None
    prior_keyring = isinstance(existing_entry, dict) and existing_entry.get("token_storage") == "keyring"
    want_keyring = (keyring or prior_keyring) and not no_keyring
    storage = "keyring" if (want_keyring and auth.keyring_available()) else "file"

    resolved_email = email or _profile_email(creds, scope_list)

    def _build_entry(config: dict) -> None:
        """Record the account under a lock (re-reads current config first)."""
        accounts = config.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
            config["accounts"] = accounts
        prior = accounts.get(name)
        entry = dict(prior) if isinstance(prior, dict) else {}
        entry["token_storage"] = storage
        if storage == "file":
            existing = entry.get("token_file")
            if existing:
                token_file = Path(existing).expanduser()
                if not token_file.is_absolute():
                    token_file = auth.get_config_dir() / token_file
            else:
                legacy = auth.legacy_token_path(name)
                token_file = legacy if legacy.exists() else auth.get_config_dir() / f"token-{name}.json"
            entry["token_file"] = str(token_file)
        if resolved_email:
            entry["email"] = resolved_email
        accounts[name] = entry

    auth.update_accounts_config(_build_entry)

    backend = auth.store_token(name, creds.to_json(), storage=storage)
    if backend != storage:
        # Keyring write fell back to file — keep config truthful.
        def _fix_backend(config: dict) -> None:
            accounts = config.get("accounts")
            if isinstance(accounts, dict) and isinstance(accounts.get(name), dict):
                accounts[name]["token_storage"] = backend
                accounts[name]["token_file"] = str(auth.get_token_path(name))
        auth.update_accounts_config(_fix_backend)
    if backend == "file":
        output_warning(
            f"Token stored as a plaintext file ({auth.get_token_path(name)}, "
            "mode 0600). Install the optional 'keyring' package for "
            "OS-keychain storage.",
            code="PLAINTEXT_TOKEN",
        )

    result = {
        "authenticated": True,
        "account": name,
        "storage": backend,
        "scopes": scope_list,
        "client_source": client_source,
    }
    if resolved_email:
        result["email"] = resolved_email
    if backend == "file":
        result["token_file"] = str(auth.get_token_path(name))
    return result


def _is_gmail_scope(scope: str) -> bool:
    """True if an OAuth scope grants Gmail access.

    Matches the exact scope forms Google issues — the full-access
    ``https://mail.google.com/`` scope or a ``.../auth/gmail*`` scope — rather
    than a loose ``"mail.google.com" in scope`` substring test, which a
    look-alike host (e.g. ``mail.google.com.evil.example``) could satisfy.
    """
    return (
        scope == "https://mail.google.com/"
        or scope.startswith("https://www.googleapis.com/auth/gmail")
    )


def _profile_email(creds, scope_list: list[str]) -> str | None:
    """Best-effort account email via Gmail getProfile (needs a gmail scope)."""
    if not any(_is_gmail_scope(s) for s in scope_list):
        return None
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds)
        return service.users().getProfile(userId="me").execute().get("emailAddress")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Status / list helpers
# ---------------------------------------------------------------------------

def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # Google stores UTC
    return parsed


def _account_status(account: str | None) -> dict:
    import auth

    acct = auth.resolve_account(account)
    name = acct["name"]
    result: dict = {
        "account": name,
        "source": acct["source"],
        "storage": acct["token_storage"],
    }
    data = auth.load_token_data(name)
    if data is None:
        result["token_present"] = False
        result["suggestion"] = f"Run: gw auth login --account {name}"
        return result

    expiry = _parse_expiry(data.get("expiry"))
    expired = expiry is None or expiry <= datetime.now(timezone.utc)
    refreshable = bool(
        data.get("refresh_token") and data.get("client_id") and data.get("client_secret")
    )
    result.update({
        "token_present": True,
        "email": acct.get("email") or data.get("account") or None,
        "valid": not expired,
        "expired": expired,
        "refreshable": refreshable,
        "expiry": data.get("expiry"),
        "scopes": data.get("scopes", []),
    })
    return result


def _list_accounts() -> dict:
    import auth

    cfg = auth.load_accounts_config()
    rows: list[dict] = []
    for name in (cfg.get("accounts") or {}):
        acct = auth.resolve_account(name)
        data = auth.load_token_data(name)
        rows.append({
            "account": name,
            "source": "config",
            "storage": acct["token_storage"],
            "email": acct.get("email") or (data or {}).get("account") or None,
            "token_present": data is not None,
        })
    seen = {r["account"] for r in rows}
    for name, path in auth.discover_legacy_accounts().items():
        if name in seen:
            continue
        try:
            with open(path) as f:
                email = json.load(f).get("account") or None
        except (ValueError, OSError):
            email = None
        rows.append({
            "account": name,
            "source": "legacy",
            "storage": "file",
            "email": email,
            "token_present": True,
        })
    return {
        "default": cfg.get("default") or auth.DEFAULT_ACCOUNT,
        "accounts": rows,
    }


# ---------------------------------------------------------------------------
# Setup checklist
# ---------------------------------------------------------------------------

_SETUP_APIS = [
    ("Gmail API", "gmail.googleapis.com"),
    ("Google Calendar API", "calendar-json.googleapis.com"),
    ("Google Drive API", "drive.googleapis.com"),
    ("Google Docs API", "docs.googleapis.com"),
    ("Google Sheets API", "sheets.googleapis.com"),
    ("Google Tasks API", "tasks.googleapis.com"),
    ("Google Forms API", "forms.googleapis.com"),
]


def _setup_checklist() -> dict:
    import auth

    config_dir = auth.get_config_dir()
    steps = [{
        "step": 1,
        "title": "Create a Google Cloud project",
        "url": "https://console.cloud.google.com/projectcreate",
        "instructions": (
            "Sign in with the Google account you want to use, name the "
            "project (e.g. 'gw-cli'), and click Create. No billing account "
            "is required."
        ),
        "verify": "The project appears in the console project picker.",
    }]
    for i, (label, service) in enumerate(_SETUP_APIS, start=2):
        steps.append({
            "step": i,
            "title": f"Enable the {label}",
            "url": f"https://console.cloud.google.com/apis/library/{service}",
            "instructions": f"With your project selected, open the {label} page and click Enable.",
            "verify": "The page shows 'API Enabled' (a Manage button replaces Enable).",
        })
    n = len(steps)
    steps.extend([
        {
            "step": n + 1,
            "title": "Configure the OAuth consent screen",
            "url": "https://console.cloud.google.com/auth/overview",
            "instructions": (
                "Choose user type 'External' and fill in the app name and "
                "support email. Under Audience/Test users, add your own "
                "Google account as a test user — Gmail scopes are sensitive "
                "and consent is blocked for unlisted users. Note: unverified "
                "apps are capped at ~25 requested scopes; the gw default set "
                "is well under that."
            ),
            "verify": "Consent screen status is 'Testing' and your email is listed as a test user.",
        },
        {
            "step": n + 2,
            "title": "Create a Desktop OAuth client",
            "url": "https://console.cloud.google.com/apis/credentials/oauthclient",
            "instructions": (
                "Create Credentials → OAuth client ID → Application type "
                "'Desktop app'. Name it (e.g. 'gw-cli') and click Create, "
                "then download the client JSON."
            ),
            "verify": "A client ID of type 'Desktop' is listed under Credentials.",
        },
        {
            "step": n + 3,
            "title": "Save the client secret to the gw config dir",
            "url": None,
            "instructions": (
                f"Save the downloaded JSON as {config_dir / 'client_secret.json'} "
                "(alternatively set GW_CLIENT_ID/GW_CLIENT_SECRET env vars, or "
                "pass --client-secret-file to gw auth login)."
            ),
            "verify": f"{config_dir / 'client_secret.json'} exists.",
        },
        {
            "step": n + 4,
            "title": "Authenticate",
            "url": None,
            "instructions": (
                "Run: gw auth login  (add --account NAME for extra accounts, "
                "--no-browser to print the consent URL instead of opening one)."
            ),
            "verify": "gw auth status reports token_present: true and valid: true.",
        },
    ])
    return {"config_dir": str(config_dir), "steps": steps}


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------

def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register auth commands on the CLI group."""

    @cli_group.group()
    def auth():
        """Authentication — login, list, status, logout, setup."""
        pass

    @auth.command()
    @account_option
    @click.option("--email", default=None, help="Login hint (pre-selects the Google account)")
    @click.option("--scopes", default=None, help="Comma-separated services to authorize (gmail,calendar,sheets,docs,tasks,forms,drive). Default: all.")
    @click.option("--readonly", is_flag=True, help="Request read-only scope variants")
    @click.option("--client-secret-file", type=click.Path(exists=True, dir_okay=False), default=None, help="Path to a Google OAuth client_secret.json")
    @click.option("--port", type=int, default=0, help="Localhost callback port (default: random free port)")
    @click.option("--no-browser", is_flag=True, help="Print the consent URL instead of opening a browser")
    @click.option("--keyring", "keyring_opt", is_flag=True, help="Store the token in the OS keyring (default: file; legacy file accounts are not auto-migrated)")
    @click.option("--no-keyring", is_flag=True, help="Store the token as a file even if OS keyring is available")
    @compact_option
    def login(account, email, scopes, readonly, client_secret_file, port, no_browser, keyring_opt, no_keyring, compact):
        """Run the OAuth flow and store a token for an account."""
        with _handle_errors(compact):
            result = perform_login(
                account=account,
                email=email,
                scopes=scopes,
                readonly=readonly,
                client_secret_file=client_secret_file,
                port=port,
                no_browser=no_browser,
                no_keyring=no_keyring,
                keyring=keyring_opt,
            )
            output_success(result, compact=compact)

    @auth.command(name="list")
    @compact_option
    def auth_list(compact):
        """List configured accounts and discovered legacy tokens."""
        with _handle_errors(compact):
            output_success(_list_accounts(), compact=compact)

    @auth.command()
    @account_option
    @compact_option
    def status(account, compact):
        """Show token state for an account (never prints token material)."""
        with _handle_errors(compact):
            output_success(_account_status(account), compact=compact)

    @auth.command()
    @account_option
    @click.option("--purge", is_flag=True, help="Also remove the account entry from accounts.json")
    @compact_option
    def logout(account, purge, compact):
        """Remove an account's token from its storage backend."""
        import auth as auth_lib
        with _handle_errors(compact):
            acct = auth_lib.resolve_account(account)
            name = acct["name"]
            # delete_token sweeps keyring + config file + any legacy file for
            # the name, so purge below cannot re-expose a live legacy credential.
            # Refuse purge when a legacy file's location can only be guessed.
            if purge and auth_lib._unambiguous_legacy_dir() is None:
                raise AuthError(
                    f"Refusing to purge account '{name}': the legacy token location "
                    "is ambiguous, so a lingering legacy credential could survive.",
                    suggestion="Set GOOGLE_WORKSPACE_TOKEN_DIR to the token directory and retry.",
                )
            removed = auth_lib.delete_token(name)
            result = {
                "account": name,
                "logged_out": bool(removed),
                "removed": removed,
            }
            if purge:
                accounts = auth_lib.load_accounts_config().get("accounts")
                if isinstance(accounts, dict) and name in accounts:
                    def _purge(cfg: dict) -> None:
                        accts = cfg.get("accounts")
                        if isinstance(accts, dict) and name in accts:
                            del accts[name]
                            if cfg.get("default") == name:
                                cfg.pop("default")
                    auth_lib.update_accounts_config(_purge)
                    result["purged"] = True
            output_success(result, compact=compact)

    @auth.command()
    @compact_option
    def setup(compact):
        """Print the Google Cloud setup checklist (machine-readable steps)."""
        with _handle_errors(compact):
            output_success(_setup_checklist(), compact=compact)

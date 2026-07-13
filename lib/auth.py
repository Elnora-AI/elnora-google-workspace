"""Multi-account Google OAuth token management.

Accounts are config-driven via ``<config-dir>/accounts.json`` where the
config dir is ``$GW_CONFIG_DIR`` (default ``~/.config/gw``):

    {
      "default": "main",
      "accounts": {
        "<name>": {
          "email": "<optional>",
          "token_file": "<optional path, relative paths resolve under the config dir>",
          "token_storage": "file" | "keyring"
        }
      }
    }

With no config file present, legacy token files keep working unchanged:
``.google-token.json`` (account "main") and ``.google-token-<name>.json``
(any other name) in ``$GOOGLE_WORKSPACE_TOKEN_DIR`` or the repo root.

Token storage backends: "file" (0600 JSON, default) and "keyring"
(OS keyring via the optional ``keyring`` package, service "gw-cli",
username = account name). ``GW_KEYRING=off`` disables keyring entirely.

Storage default rule: ``file`` is the default backend. Keyring is used only
when the user explicitly opts in (``--keyring``) or the account's existing
config entry already records ``token_storage: keyring`` — a login is never
silently migrated from a legacy file token to the keyring.

Account names become file-path components and keyring usernames, so they are
validated (see ``_validate_account_name``): letters, digits, ``.``, ``-`` and
``_`` only, not starting with a dot, no path separators or ``..``, and not a
Windows reserved device name.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import logging
import stat
import sys

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from output import AuthError, CliError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scopes — per-service constants; use the narrowest scopes that cover
# actual functionality.
# ---------------------------------------------------------------------------

SERVICE_SCOPES: dict[str, list[str]] = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.labels",
    ],
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "sheets": [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "docs": ["https://www.googleapis.com/auth/documents"],
    "tasks": ["https://www.googleapis.com/auth/tasks"],
    "forms": [
        "https://www.googleapis.com/auth/forms.body",
        "https://www.googleapis.com/auth/forms.responses.readonly",
    ],
    "drive": ["https://www.googleapis.com/auth/drive"],
}

READONLY_SERVICE_SCOPES: dict[str, list[str]] = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
    "sheets": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
    "docs": ["https://www.googleapis.com/auth/documents.readonly"],
    "tasks": ["https://www.googleapis.com/auth/tasks.readonly"],
    "forms": [
        "https://www.googleapis.com/auth/forms.body.readonly",
        "https://www.googleapis.com/auth/forms.responses.readonly",
    ],
    "drive": ["https://www.googleapis.com/auth/drive.readonly"],
}


def scopes_for(services: list[str] | None = None, *, readonly: bool = False) -> list[str]:
    """Return the scope list for the given service names (all if None)."""
    table = READONLY_SERVICE_SCOPES if readonly else SERVICE_SCOPES
    names = list(table) if not services else services
    unknown = [n for n in names if n not in table]
    if unknown:
        raise ValueError(
            f"Unknown service(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(SERVICE_SCOPES)}"
        )
    out: list[str] = []
    for n in names:
        for s in table[n]:
            if s not in out:
                out.append(s)
    return out


# Full login scope set.
SCOPES = scopes_for()

# Scopes added after the original token set (forms.responses.readonly and full
# drive, both 2026-07) are not required of legacy tokens, so existing tokens
# keep working; a missing drive scope surfaces as a typed 403 with a re-auth
# suggestion (gw auth login --scopes drive).
_LEGACY_REQUIRED_SCOPES = [
    s for s in SCOPES
    if s not in (
        "https://www.googleapis.com/auth/forms.responses.readonly",
        "https://www.googleapis.com/auth/drive",
    )
]

DEFAULT_ACCOUNT = "main"

KEYRING_SERVICE = "gw-cli"


# ---------------------------------------------------------------------------
# Account name validation
# ---------------------------------------------------------------------------

# Account names are interpolated into file paths and keyring usernames, so they
# must not escape the config dir or collide with OS-reserved names.
_ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_ACCOUNT_NAME_LEN = 64
_WINDOWS_RESERVED = {
    "con", "aux", "nul", "prn",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _validate_account_name(name: object) -> str:
    """Validate a free-form account name before it becomes a path/keyring key.

    Rejects empty/overlong names, path separators, ``..``, null bytes, leading
    dots, and Windows reserved device names. Returns the name unchanged when
    valid; otherwise raises AuthError without touching the filesystem.
    """
    suggestion = (
        "Account names may contain letters, digits, '.', '-' and '_', must not "
        "start with a dot, and must not contain path separators."
    )
    if not isinstance(name, str) or not name:
        raise AuthError("Account name must be a non-empty string.", suggestion=suggestion)
    if len(name) > _MAX_ACCOUNT_NAME_LEN:
        raise AuthError(
            f"Account name is too long (max {_MAX_ACCOUNT_NAME_LEN} characters).",
            suggestion=suggestion,
        )
    if "\x00" in name:
        raise AuthError("Account name contains a null byte.", suggestion=suggestion)
    seps = {"/", "\\", os.sep}
    if os.altsep:
        seps.add(os.altsep)
    if seps & set(name):
        raise AuthError(f"Account name '{name}' contains a path separator.", suggestion=suggestion)
    if ".." in name:
        raise AuthError(f"Account name '{name}' contains '..'.", suggestion=suggestion)
    if not _ACCOUNT_NAME_RE.match(name):
        raise AuthError(f"Account name '{name}' is not a valid account name.", suggestion=suggestion)
    if name.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise AuthError(f"Account name '{name}' is a reserved device name.", suggestion=suggestion)
    return name


# ---------------------------------------------------------------------------
# Config dir + accounts.json
# ---------------------------------------------------------------------------

def get_config_dir() -> Path:
    """Config dir: $GW_CONFIG_DIR override, default ~/.config/gw.

    A ``~`` in $GW_CONFIG_DIR is expanded, and a relative value is anchored to
    an absolute path (so it does not float with cwd or create a literal '~'
    directory).
    """
    env = os.environ.get("GW_CONFIG_DIR")
    if not env:
        return Path.home() / ".config" / "gw"
    path = Path(env).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def load_accounts_config() -> dict:
    """Load accounts.json from the config dir. Missing/invalid → {}.

    Read paths degrade gracefully so a corrupt file never bricks legacy-account
    reads. Write paths go through ``save_accounts_config``, which refuses to
    overwrite a corrupt file (see below).
    """
    path = get_config_dir() / "accounts.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (ValueError, OSError) as err:
        logger.warning("Could not read %s (%s); ignoring.", path, type(err).__name__)
        return {}


def save_accounts_config(cfg: dict) -> None:
    """Write accounts.json atomically, creating the config dir with 0700.

    Refuses to overwrite an existing but unparseable accounts.json (which
    ``load_accounts_config`` would have read as ``{}``): the corrupt file is
    copied to ``accounts.json.corrupt`` and a CliError is raised, so a parse
    error can never silently drop other configured accounts.
    """
    config_dir = get_config_dir()
    path = config_dir / "accounts.json"
    if path.exists():
        try:
            with open(path) as f:
                json.load(f)
        except (ValueError, OSError) as err:
            backup = config_dir / "accounts.json.corrupt"
            try:
                _atomic_write(backup, path.read_text(errors="replace"))
            except OSError:
                pass
            raise CliError(
                f"accounts.json is unreadable ({type(err).__name__}); refusing to "
                f"overwrite it. A copy was saved to {backup.name}.",
                suggestion="Fix or remove accounts.json (restore from the .corrupt copy if needed) and retry.",
            )
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config_dir, 0o700)
    except OSError:
        pass  # Windows: best-effort
    _atomic_write(path, json.dumps(cfg, indent=2))


@contextmanager
def _config_lock(timeout: float = 10.0):
    """Best-effort cross-platform lock around accounts.json read-modify-write.

    Uses an ``O_CREAT | O_EXCL`` lock file (works on POSIX and Windows). On
    timeout it proceeds without the lock rather than deadlocking on a stale
    lock from a crashed process.
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config_dir / "accounts.json.lock"
    fd = None
    start = time.monotonic()
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() - start > timeout:
                break  # proceed lock-free; re-read-under-lock still helps
            time.sleep(0.05)
        except OSError:
            break
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.unlink(str(lock_path))
            except OSError:
                pass


def update_accounts_config(mutate) -> dict:
    """Apply ``mutate(cfg)`` to accounts.json under a lock.

    Re-reads the on-disk config inside the lock before applying the change, so
    concurrent ``gw`` processes don't lose each other's updates (last-writer
    wins is avoided). ``mutate`` edits ``cfg`` in place. Returns the written cfg.
    """
    with _config_lock():
        cfg = load_accounts_config()
        mutate(cfg)
        save_accounts_config(cfg)
        return cfg


# ---------------------------------------------------------------------------
# Legacy token discovery (pre-config setups)
# ---------------------------------------------------------------------------

def _legacy_token_dir() -> Path:
    """Find the legacy token dir: env var or walking up to a sentinel file.

    Checks (in order): GOOGLE_WORKSPACE_TOKEN_DIR env var, walking up from
    __file__, walking up from cwd (handles plugin cache paths outside the repo).
    """
    env_override = os.environ.get("GOOGLE_WORKSPACE_TOKEN_DIR")
    if env_override:
        return Path(env_override)
    sentinels = ("CLAUDE.md", "pyproject.toml")
    # Try from __file__ first (works for repo-local runs)
    for parent in Path(__file__).resolve().parents:
        if any((parent / s).exists() for s in sentinels):
            return parent
    # Try from cwd (works when CLI runs from plugin cache outside the repo)
    try:
        for parent in [Path.cwd()] + list(Path.cwd().parents):
            if any((parent / s).exists() for s in sentinels):
                return parent
    except OSError:
        pass
    # Last resort — assume standard plugin depth from __file__
    return Path(__file__).resolve().parent.parent.parent.parent


def _unambiguous_legacy_dir() -> Path | None:
    """Legacy token dir resolved WITHOUT the cwd walk.

    Returns the dir when it is determined by GOOGLE_WORKSPACE_TOKEN_DIR or a
    sentinel above ``__file__``; returns None when only the cwd-dependent
    fallback would apply, so destructive operations can refuse rather than
    delete a file in an unrelated project.
    """
    env_override = os.environ.get("GOOGLE_WORKSPACE_TOKEN_DIR")
    if env_override:
        return Path(env_override).expanduser()
    sentinels = ("CLAUDE.md", "pyproject.toml")
    for parent in Path(__file__).resolve().parents:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return None


def _legacy_filename(name: str) -> str:
    """Legacy token filename for an account name."""
    return ".google-token.json" if name == DEFAULT_ACCOUNT else f".google-token-{name}.json"


def legacy_token_path(name: str) -> Path:
    """Legacy token file path for an account name."""
    _validate_account_name(name)
    return _legacy_token_dir() / _legacy_filename(name)


def discover_legacy_accounts() -> dict[str, Path]:
    """Map account names to existing legacy token files."""
    out: dict[str, Path] = {}
    try:
        for p in sorted(_legacy_token_dir().glob(".google-token*.json")):
            if p.name == ".google-token.json":
                out[DEFAULT_ACCOUNT] = p
            elif p.name.startswith(".google-token-"):
                name = p.name[len(".google-token-"):-len(".json")]
                if name:
                    out[name] = p
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------

def resolve_account(account: str | None) -> dict:
    """Resolve an account name (free-form) to its runtime settings.

    Returns {"name", "source": "config"|"legacy", "email", "token_file": Path,
    "token_storage": "file"|"keyring"}. Raises AuthError only for an invalid
    account name; a well-formed name with no token anywhere resolves to a
    legacy entry and fails later in get_credentials with a clear AuthError.
    Malformed config (non-dict ``accounts`` or entry) is ignored, not fatal.
    """
    cfg = load_accounts_config()
    default = cfg.get("default")
    name = account or (default if isinstance(default, str) else None) or DEFAULT_ACCOUNT
    _validate_account_name(name)
    accounts = cfg.get("accounts")
    entry = accounts.get(name) if isinstance(accounts, dict) else None
    if isinstance(entry, dict):
        token_file = entry.get("token_file")
        if token_file:
            path = Path(token_file).expanduser()
            if not path.is_absolute():
                path = get_config_dir() / path
        else:
            path = get_config_dir() / f"token-{name}.json"
        return {
            "name": name,
            "source": "config",
            "email": entry.get("email"),
            "token_file": path,
            "token_storage": entry.get("token_storage", "file"),
        }
    return {
        "name": name,
        "source": "legacy",
        "email": None,
        "token_file": legacy_token_path(name),
        "token_storage": "file",
    }


def get_token_path(account: str | None = None) -> Path:
    """Get the token file path for the given account (file backend location)."""
    return resolve_account(account)["token_file"]


# ---------------------------------------------------------------------------
# Keyring backend (optional)
# ---------------------------------------------------------------------------

def _keyring():
    """Return the keyring module if importable and a usable backend exists.

    Rejects both the ``fail`` and ``null`` backends (keyring's documented
    "disabled" mechanisms — ``null`` silently discards writes) and any backend
    advertising priority <= 0, so a no-op backend never masquerades as usable.
    """
    if os.environ.get("GW_KEYRING", "").lower() in ("off", "0", "false", "no"):
        return None
    try:
        import keyring
        from keyring.backends.fail import Keyring as _FailKeyring
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    if isinstance(backend, _FailKeyring):
        return None
    try:
        from keyring.backends.null import Keyring as _NullKeyring
        if isinstance(backend, _NullKeyring):
            return None
    except ImportError:
        pass
    try:
        if backend.priority <= 0:
            return None
    except Exception:
        pass
    return keyring


def keyring_available() -> bool:
    """True if the keyring package and a usable OS backend are present."""
    return _keyring() is not None


# ---------------------------------------------------------------------------
# Token storage (load / store / delete)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, payload: str) -> None:
    """Write a file atomically with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass  # Windows: os.chmod is a no-op, permissions handled by OS defaults
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _enforce_file_perms(path: Path) -> None:
    """Verify and enforce 0600 on a token file (POSIX best-effort)."""
    mode = path.stat().st_mode
    if sys.platform != "win32" and mode & (stat.S_IRGRP | stat.S_IROTH):
        logger.warning(
            "Token file %s has insecure permissions (%s). Fixing to 0600.",
            path.name, oct(mode & 0o777),
        )
        try:
            os.chmod(str(path), 0o600)
        except OSError as perm_err:
            logger.error("Could not fix token permissions: %s", perm_err)


def _load_token(acct: dict) -> tuple[dict | None, str | None]:
    """Load token data for a resolved account. Returns (data, backend_used)."""
    if acct["token_storage"] == "keyring":
        kr = _keyring()
        if kr is not None:
            try:
                raw = kr.get_password(KEYRING_SERVICE, acct["name"])
            except Exception as err:
                logger.warning("Keyring read failed: %s", type(err).__name__)
                raw = None
            if raw:
                try:
                    return json.loads(raw), "keyring"
                except ValueError:
                    logger.warning(
                        "Keyring entry for account '%s' is not valid JSON.",
                        acct["name"],
                    )
        # fall through to the file backend
    path = acct["token_file"]
    if path.exists():
        _enforce_file_perms(path)
        try:
            with open(path) as f:
                return json.load(f), "file"
        except (ValueError, OSError) as parse_err:
            logger.warning(
                "Token file %s exists but could not be parsed: %s. "
                "The file may be corrupted. Re-authentication may be required.",
                path.name, type(parse_err).__name__,
            )
    return None, None


def load_token_data(account: str | None = None) -> dict | None:
    """Load the raw token dict for an account, or None if absent."""
    return _load_token(resolve_account(account))[0]


def store_token(account: str | None, token_json: str, *, storage: str | None = None) -> str:
    """Persist token JSON via the requested backend. Returns the backend used.

    "keyring" falls back to "file" (with a stderr note) when no usable
    keyring backend exists or the write fails.
    """
    acct = resolve_account(account)
    backend = storage or acct["token_storage"]
    if backend == "keyring":
        kr = _keyring()
        if kr is not None:
            try:
                kr.set_password(KEYRING_SERVICE, acct["name"], token_json)
                # Read back: a null/no-op backend accepts the write silently but
                # persists nothing. Only report keyring success if it round-trips.
                if kr.get_password(KEYRING_SERVICE, acct["name"]) == token_json:
                    return "keyring"
                logger.warning("Keyring did not persist the token; falling back to file.")
            except Exception as err:
                logger.warning("Keyring write failed: %s", type(err).__name__)
        print(
            "gw: keyring unavailable — falling back to file token storage.",
            file=sys.stderr,
        )
    _atomic_write(acct["token_file"], token_json)
    return "file"


def delete_token(account: str | None = None) -> list[str]:
    """Remove an account's token from every backend. Returns what was removed.

    Sweeps the keyring entry, the resolved config token file, and any legacy
    token file for the name (so a config-backed login does not leave a live
    legacy credential behind). Legacy files are located WITHOUT the cwd walk;
    for a legacy-sourced account whose directory is ambiguous, refuses rather
    than risk deleting an unrelated project's ``.google-token.json``.
    """
    acct = resolve_account(account)
    name = acct["name"]
    removed: list[str] = []
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(KEYRING_SERVICE, name) is not None:
                kr.delete_password(KEYRING_SERVICE, name)
                removed.append("keyring")
        except Exception as err:
            logger.warning("Keyring delete failed: %s", type(err).__name__)

    paths: list[Path] = []
    legacy_dir = _unambiguous_legacy_dir()
    if acct["source"] == "config":
        paths.append(acct["token_file"])
        if legacy_dir is not None:
            legacy_path = legacy_dir / _legacy_filename(name)
            if legacy_path not in paths:
                paths.append(legacy_path)
    else:  # legacy source — the file location must be unambiguous to delete safely
        if legacy_dir is None:
            raise AuthError(
                f"Refusing to delete the legacy token for '{name}': its location is "
                "ambiguous (no GOOGLE_WORKSPACE_TOKEN_DIR set and no repo sentinel "
                "above the plugin).",
                suggestion="Set GOOGLE_WORKSPACE_TOKEN_DIR to the token directory and retry.",
            )
        paths.append(legacy_dir / _legacy_filename(name))

    for path in paths:
        if path.exists():
            try:
                path.unlink()
                removed.append(str(path))
            except OSError as err:
                logger.warning("Could not delete %s: %s", path, err)
    _evict_account_caches(name)
    return removed


# ---------------------------------------------------------------------------
# Credentials + services
# ---------------------------------------------------------------------------

def get_credentials(account: str | None = None) -> Credentials:
    """Load or refresh OAuth credentials for the given account.

    Args:
        account: Free-form account name. Defaults to the config default,
            else "main".

    Returns:
        Valid Google OAuth Credentials.

    Raises:
        AuthError: If no valid token exists for the account.
    """
    acct = resolve_account(account)
    name = acct["name"]
    token_data, backend = _load_token(acct)
    creds = None

    if token_data is not None:
        existing_scopes = set(token_data.get("scopes", []))
        # Legacy tokens must cover the pre-config scope set (forces re-auth
        # when the plugin grows required scopes). Config accounts may be
        # deliberately narrow-scoped; missing scopes surface as API 403s.
        if acct["source"] == "config" or _scopes_satisfied(
            existing_scopes, set(_LEGACY_REQUIRED_SCOPES)
        ):
            try:
                creds = Credentials.from_authorized_user_info(
                    token_data, list(existing_scopes) or None
                )
            except ValueError as parse_err:
                logger.warning(
                    "Token for account '%s' is missing required fields: %s. "
                    "Re-authentication may be required.",
                    name, type(parse_err).__name__,
                )

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            store_token(name, creds.to_json(), storage=backend)
        except RefreshError as refresh_err:
            logger.warning(
                "Token refresh failed for account '%s' (%s). "
                "Re-authentication required.",
                name, type(refresh_err).__name__,
            )
            creds = None
            _evict_account_caches(name)

    if not creds or not creds.valid:
        raise _no_token_error(name, acct)

    return creds


def _known_account_names() -> list[str]:
    """Names known from accounts.json plus discovered legacy token files."""
    cfg = load_accounts_config()
    accounts = cfg.get("accounts")
    names = set(accounts) if isinstance(accounts, dict) else set()
    names |= set(discover_legacy_accounts())
    return sorted(names)


def _no_token_error(name: str, acct: dict) -> AuthError:
    """AuthError for a missing token. For an unknown name (likely a typo), list
    the known accounts instead of steering the user into creating it."""
    if acct["source"] == "legacy" and not acct["token_file"].exists():
        known = _known_account_names()
        if known:
            return AuthError(
                f"No valid token for account '{name}'.",
                account=name,
                suggestion=(
                    f"Unknown account '{name}'. Known accounts: {', '.join(known)}.\n"
                    f"Check the spelling, or if intended: gw auth login --account {name}"
                ),
            )
        return AuthError(
            f"No valid token for account '{name}'.",
            account=name,
            suggestion=f"No accounts configured yet. Run: gw auth login --account {name}",
        )
    return AuthError(f"No valid token for account '{name}'.", account=name)


def _scopes_satisfied(existing: set[str], needed: set[str]) -> bool:
    """Check if existing scopes cover all needed scopes.

    Handles broad-to-narrow scope coverage, e.g.:
    - https://mail.google.com/ covers all gmail.* scopes
    - https://www.googleapis.com/auth/drive covers drive.file, drive.readonly, etc.
    """
    # Broad scopes that imply narrower ones (prefix matching)
    BROAD_SCOPE_PREFIXES = {
        "https://mail.google.com/": "https://www.googleapis.com/auth/gmail.",
        "https://www.googleapis.com/auth/drive": "https://www.googleapis.com/auth/drive.",
    }

    for scope in needed:
        if scope in existing:
            continue
        # Check if a broad scope covers this narrow scope
        covered = False
        for broad, narrow_prefix in BROAD_SCOPE_PREFIXES.items():
            if broad in existing and scope.startswith(narrow_prefix):
                covered = True
                break
        if not covered:
            return False
    return True


_service_cache: dict[tuple, object] = {}
_email_cache: dict[str, str] = {}


def _evict_account_caches(name: str) -> None:
    """Drop cached services + email for an account."""
    for k in [k for k in _service_cache if k[2] == name]:
        _service_cache.pop(k, None)
    _email_cache.pop(name, None)


def get_account_email(account: str | None = None, *, fetch: bool = True) -> str | None:
    """Best-effort email for an account: config → token data → getProfile.

    With fetch=False, never touches the network (config/token data only).
    Results are cached in-process.
    """
    acct = resolve_account(account)
    name = acct["name"]
    if name in _email_cache:
        return _email_cache[name]
    email = acct.get("email")
    if not email:
        token_data, _ = _load_token(acct)
        email = (token_data or {}).get("account") or None
    if not email and fetch:
        try:
            service = build_service("gmail", "v1", name)
            profile = service.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress")
        except Exception:
            email = None
    if email:
        _email_cache[name] = email
    return email


def build_service(api: str, version: str, account: str | None = None):
    """Build a Google API service client (cached per api/version/account).

    Args:
        api: Google API name (e.g., "gmail", "sheets", "calendar", "tasks")
        version: API version (e.g., "v1")
        account: Free-form account name (see resolve_account).

    Returns:
        Google API service resource.
    """
    name = resolve_account(account)["name"]
    key = (api, version, name)
    if key not in _service_cache:
        from googleapiclient.discovery import build

        creds = get_credentials(name)
        _service_cache[key] = build(api, version, credentials=creds)
    return _service_cache[key]

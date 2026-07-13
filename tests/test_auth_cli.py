"""Tests for the gw auth command group — client resolution, status/list/setup shapes."""

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "cli"))

import auth
from commands.auth import (
    _account_status,
    _list_accounts,
    _resolve_oauth_client,
    _setup_checklist,
    perform_login,
)
from output import AuthError

GW = str(_PLUGIN_ROOT / "cli" / "gw.py")

_TOKEN_MATERIAL_KEYS = {"token", "access_token", "refresh_token", "client_secret"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    monkeypatch.setenv("GW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("GOOGLE_WORKSPACE_TOKEN_DIR", str(legacy_dir))
    monkeypatch.setenv("GW_KEYRING", "off")
    monkeypatch.delenv("GW_CLIENT_ID", raising=False)
    monkeypatch.delenv("GW_CLIENT_SECRET", raising=False)
    auth._service_cache.clear()
    auth._email_cache.clear()
    return SimpleNamespace(config_dir=config_dir, legacy_dir=legacy_dir)


def _token_data(**extra):
    data = {
        "token": "x",
        "refresh_token": "r",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        "expiry": "2099-01-01T00:00:00Z",
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# OAuth client resolution order
# ---------------------------------------------------------------------------

class TestOAuthClientResolution:
    def test_env_vars_first(self, env, monkeypatch):
        monkeypatch.setenv("GW_CLIENT_ID", "env-id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "env-secret")
        (env.config_dir / "client_secret.json").parent.mkdir(parents=True, exist_ok=True)
        (env.config_dir / "client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "file-id", "client_secret": "file-secret"}})
        )
        config, source = _resolve_oauth_client(None)
        assert source == "env"
        assert config["installed"]["client_id"] == "env-id"

    def test_config_dir_client_secret_second(self, env):
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "file-id", "client_secret": "file-secret"}})
        )
        config, source = _resolve_oauth_client(None)
        assert source == "config-dir"
        assert config["installed"]["client_id"] == "file-id"

    def test_explicit_client_secret_file_third(self, env, tmp_path):
        secret = tmp_path / "cs.json"
        secret.write_text(
            json.dumps({"installed": {"client_id": "flag-id", "client_secret": "flag-secret"}})
        )
        config, source = _resolve_oauth_client(str(secret))
        assert source == "client-secret-file"
        assert config["installed"]["client_id"] == "flag-id"

    def test_legacy_token_client_reuse_last(self, env):
        (env.legacy_dir / ".google-token.json").write_text(json.dumps(_token_data()))
        config, source = _resolve_oauth_client(None)
        assert source == "legacy-token"
        assert config["installed"]["client_id"] == "cid"

    def test_nothing_found_raises_with_setup_hint(self, env):
        with pytest.raises(AuthError) as exc:
            _resolve_oauth_client(None)
        assert "gw auth setup" in exc.value.suggestion

    def test_explicit_flag_beats_config_dir(self, env, tmp_path):
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "client_secret.json").write_text(
            json.dumps({"installed": {"client_id": "config-id", "client_secret": "config-secret"}})
        )
        secret = tmp_path / "flag.json"
        secret.write_text(
            json.dumps({"installed": {"client_id": "flag-id", "client_secret": "flag-secret"}})
        )
        config, source = _resolve_oauth_client(str(secret))
        assert source == "client-secret-file"
        assert config["installed"]["client_id"] == "flag-id"

    def test_explicit_flag_beats_env(self, env, monkeypatch, tmp_path):
        monkeypatch.setenv("GW_CLIENT_ID", "env-id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "env-secret")
        secret = tmp_path / "flag.json"
        secret.write_text(
            json.dumps({"installed": {"client_id": "flag-id", "client_secret": "flag-secret"}})
        )
        config, source = _resolve_oauth_client(str(secret))
        assert source == "client-secret-file"
        assert config["installed"]["client_id"] == "flag-id"


# ---------------------------------------------------------------------------
# auth status shapes (no network)
# ---------------------------------------------------------------------------

class TestStatus:
    def test_missing_token(self, env):
        result = _account_status("ghost")
        assert result["account"] == "ghost"
        assert result["token_present"] is False
        assert "gw auth login --account ghost" in result["suggestion"]

    def test_valid_token(self, env):
        (env.legacy_dir / ".google-token.json").write_text(
            json.dumps(_token_data(account="me@example.com"))
        )
        result = _account_status("main")
        assert result["token_present"] is True
        assert result["valid"] is True
        assert result["expired"] is False
        assert result["refreshable"] is True
        assert result["email"] == "me@example.com"
        assert result["scopes"] == ["https://www.googleapis.com/auth/gmail.send"]

    def test_expired_token(self, env):
        (env.legacy_dir / ".google-token.json").write_text(
            json.dumps(_token_data(expiry="2020-01-01T00:00:00Z"))
        )
        result = _account_status("main")
        assert result["expired"] is True
        assert result["valid"] is False
        assert result["refreshable"] is True

    def test_never_prints_token_material(self, env):
        (env.legacy_dir / ".google-token.json").write_text(
            json.dumps(_token_data(token="secret-token-material", refresh_token="secret-refresh"))
        )
        result = _account_status("main")
        assert not (_TOKEN_MATERIAL_KEYS & set(result))
        dumped = json.dumps(result)
        assert "secret-token-material" not in dumped
        assert "secret-refresh" not in dumped
        assert "csec" not in dumped


# ---------------------------------------------------------------------------
# auth list
# ---------------------------------------------------------------------------

class TestList:
    def test_config_and_legacy_merged(self, env):
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "accounts.json").write_text(json.dumps({
            "default": "work",
            "accounts": {"work": {"email": "w@example.com"}},
        }))
        (env.legacy_dir / ".google-token-mail.json").write_text(
            json.dumps(_token_data(account="m@example.com"))
        )
        result = _list_accounts()
        assert result["default"] == "work"
        by_name = {r["account"]: r for r in result["accounts"]}
        assert by_name["work"]["source"] == "config"
        assert by_name["work"]["token_present"] is False
        assert by_name["mail"]["source"] == "legacy"
        assert by_name["mail"]["email"] == "m@example.com"
        assert by_name["mail"]["token_present"] is True

    def test_config_entry_shadows_legacy_discovery(self, env):
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "accounts.json").write_text(
            json.dumps({"accounts": {"main": {}}})
        )
        (env.legacy_dir / ".google-token.json").write_text(json.dumps(_token_data()))
        result = _list_accounts()
        assert [r["account"] for r in result["accounts"]] == ["main"]

    def test_empty(self, env):
        result = _list_accounts()
        assert result == {"default": "main", "accounts": []}


# ---------------------------------------------------------------------------
# auth setup checklist
# ---------------------------------------------------------------------------

class TestSetup:
    def test_step_shape(self, env):
        result = _setup_checklist()
        assert result["config_dir"] == str(env.config_dir)
        for step in result["steps"]:
            assert set(step) == {"step", "title", "url", "instructions", "verify"}
        assert [s["step"] for s in result["steps"]] == list(range(1, len(result["steps"]) + 1))

    def test_covers_required_google_cloud_steps(self, env):
        titles = " | ".join(s["title"] for s in _setup_checklist()["steps"])
        for needle in ("project", "Gmail", "Calendar", "Drive", "Docs", "Sheets",
                       "Tasks", "Forms", "consent", "Desktop OAuth client",
                       "client secret", "Authenticate"):
            assert needle in titles
        instructions = " | ".join(s["instructions"] for s in _setup_checklist()["steps"])
        assert "gw auth login" in instructions
        assert str(env.config_dir) in instructions


# ---------------------------------------------------------------------------
# perform_login (OAuth flow mocked, no network)
# ---------------------------------------------------------------------------

def _fake_flow(monkeypatch, scopes_seen):
    """Install a fake google_auth_oauthlib.flow module."""
    creds = MagicMock()
    creds.refresh_token = "r"
    creds.to_json.return_value = json.dumps(_token_data())

    flow_mod = types.ModuleType("google_auth_oauthlib.flow")

    class FakeFlow:
        @classmethod
        def from_client_config(cls, client_config, scopes):
            scopes_seen.append(scopes)
            inst = cls()
            inst.client_config = client_config
            return inst

        def run_local_server(self, **kwargs):
            return creds

    flow_mod.InstalledAppFlow = FakeFlow
    pkg = types.ModuleType("google_auth_oauthlib")
    pkg.flow = flow_mod
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_mod)
    return creds


def _install_fake_keyring(monkeypatch):
    """Inject an in-memory (usable) keyring backend."""
    store: dict = {}
    kr = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")

    class FailKeyring:
        pass

    class _Backend:
        pass

    fail.Keyring = FailKeyring
    kr.get_keyring = lambda: _Backend()
    kr.get_password = lambda s, u: store.get((s, u))
    kr.set_password = lambda s, u, v: store.__setitem__((s, u), v)
    kr.delete_password = lambda s, u: store.pop((s, u), None)
    kr.backends = backends
    backends.fail = fail
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    monkeypatch.delenv("GW_KEYRING", raising=False)
    return store


class TestPerformLogin:
    def test_writes_token_and_config(self, env, monkeypatch):
        monkeypatch.setenv("GW_CLIENT_ID", "id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "sec")
        scopes_seen = []
        _fake_flow(monkeypatch, scopes_seen)

        result = perform_login(account="work", email="w@example.com")

        assert result["authenticated"] is True
        assert result["account"] == "work"
        assert result["storage"] == "file"
        assert scopes_seen == [auth.SCOPES]
        assert not (_TOKEN_MATERIAL_KEYS & set(result))

        cfg = json.loads((env.config_dir / "accounts.json").read_text())
        assert cfg["accounts"]["work"]["email"] == "w@example.com"
        assert cfg["accounts"]["work"]["token_storage"] == "file"

        token_path = Path(result["token_file"])
        assert token_path == env.config_dir / "token-work.json"
        assert json.loads(token_path.read_text())["token"] == "x"
        if sys.platform != "win32":
            assert (token_path.stat().st_mode & 0o777) == 0o600

    def test_scope_filter(self, env, monkeypatch):
        monkeypatch.setenv("GW_CLIENT_ID", "id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "sec")
        scopes_seen = []
        _fake_flow(monkeypatch, scopes_seen)

        perform_login(account="work", email="w@example.com", scopes="gmail,calendar")

        assert scopes_seen == [auth.scopes_for(["gmail", "calendar"])]

    def test_unknown_scope_service_fails_validation(self, env, monkeypatch):
        from output import ValidationError

        with pytest.raises(ValidationError, match="Unknown service"):
            perform_login(account="work", scopes="nope")

    def test_reuses_existing_legacy_token_path(self, env, monkeypatch):
        legacy = env.legacy_dir / ".google-token-mail.json"
        legacy.write_text(json.dumps(_token_data()))
        scopes_seen = []
        _fake_flow(monkeypatch, scopes_seen)

        result = perform_login(account="mail", email="m@example.com")

        assert result["client_source"] == "legacy-token"
        assert Path(result["token_file"]) == legacy
        cfg = json.loads((env.config_dir / "accounts.json").read_text())
        assert cfg["accounts"]["mail"]["token_file"] == str(legacy)

    def test_plaintext_warning_on_file_backend(self, env, monkeypatch, capfd):
        monkeypatch.setenv("GW_CLIENT_ID", "id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "sec")
        _fake_flow(monkeypatch, [])

        perform_login(account="work", email="w@example.com")

        assert "PLAINTEXT_TOKEN" in capfd.readouterr().err

    def test_new_account_defaults_to_file_even_with_keyring(self, env, monkeypatch):
        monkeypatch.setenv("GW_CLIENT_ID", "id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "sec")
        _install_fake_keyring(monkeypatch)
        _fake_flow(monkeypatch, [])

        result = perform_login(account="work", email="w@example.com")

        assert result["storage"] == "file"

    def test_keyring_opt_in_uses_keyring(self, env, monkeypatch):
        monkeypatch.setenv("GW_CLIENT_ID", "id")
        monkeypatch.setenv("GW_CLIENT_SECRET", "sec")
        store = _install_fake_keyring(monkeypatch)
        _fake_flow(monkeypatch, [])

        result = perform_login(account="work", email="w@example.com", keyring=True)

        assert result["storage"] == "keyring"
        assert ("gw-cli", "work") in store
        cfg = json.loads((env.config_dir / "accounts.json").read_text())
        assert cfg["accounts"]["work"]["token_storage"] == "keyring"

    def test_legacy_file_account_not_migrated_to_keyring(self, env, monkeypatch):
        legacy = env.legacy_dir / ".google-token-mail.json"
        legacy.write_text(json.dumps(_token_data()))
        _install_fake_keyring(monkeypatch)
        _fake_flow(monkeypatch, [])

        result = perform_login(account="mail", email="m@example.com")

        assert result["storage"] == "file"
        assert Path(result["token_file"]) == legacy


# ---------------------------------------------------------------------------
# CLI smoke (subprocess, no network)
# ---------------------------------------------------------------------------

def _run_gw(env, *args):
    proc_env = dict(os.environ)
    proc_env.update({
        "GW_CONFIG_DIR": str(env.config_dir),
        "GOOGLE_WORKSPACE_TOKEN_DIR": str(env.legacy_dir),
        "GW_KEYRING": "off",
    })
    return subprocess.run(
        [sys.executable, GW, *args],
        capture_output=True, text=True, env=proc_env, timeout=60,
    )


class TestCliSmoke:
    def test_auth_list_compact(self, env):
        result = _run_gw(env, "auth", "list", "--compact")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["default"] == "main"
        assert payload["accounts"] == []

    def test_auth_status_missing_token_exits_zero(self, env):
        result = _run_gw(env, "auth", "status", "--account", "ghost", "--compact")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["token_present"] is False

    def test_auth_setup_compact(self, env):
        result = _run_gw(env, "auth", "setup", "--compact")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert len(payload["steps"]) == 12

    def test_auth_logout_missing_token(self, env):
        result = _run_gw(env, "auth", "logout", "--account", "ghost", "--compact")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["logged_out"] is False

    def test_auth_logout_purge_sweeps_lingering_legacy(self, env):
        # config records main as keyring-backed (no config token file), but a
        # pre-refactor legacy token file still holds a live credential.
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "accounts.json").write_text(
            json.dumps({"accounts": {"main": {"token_storage": "keyring"}}})
        )
        legacy = env.legacy_dir / ".google-token.json"
        legacy.write_text(json.dumps(_token_data()))

        result = _run_gw(env, "auth", "logout", "--account", "main", "--purge", "--compact")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["logged_out"] is True
        assert payload.get("purged") is True
        assert not legacy.exists()

        status = _run_gw(env, "auth", "status", "--account", "main", "--compact")
        assert json.loads(status.stdout)["token_present"] is False

    def test_auth_login_rejects_traversal_account(self, env):
        result = _run_gw(env, "auth", "login", "--account", "x/../../evil", "--compact")
        assert result.returncode == 3  # EXIT_AUTH
        assert "evil.json" not in (result.stdout + result.stderr)
        # no token file written outside the config dir
        assert list(env.config_dir.parent.glob("evil*")) == []

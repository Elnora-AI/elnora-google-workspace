"""Tests for auth module — config resolution, legacy fallback, storage backends."""

import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import auth
from output import AuthError


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config dir + legacy token dir, keyring disabled."""
    config_dir = tmp_path / "config"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    monkeypatch.setenv("GW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("GOOGLE_WORKSPACE_TOKEN_DIR", str(legacy_dir))
    monkeypatch.setenv("GW_KEYRING", "off")
    auth._service_cache.clear()
    auth._email_cache.clear()
    return SimpleNamespace(config_dir=config_dir, legacy_dir=legacy_dir)


def _write_accounts(env, cfg):
    env.config_dir.mkdir(parents=True, exist_ok=True)
    (env.config_dir / "accounts.json").write_text(json.dumps(cfg))


def _install_fake_keyring(monkeypatch, store=None):
    """Inject an in-memory keyring module tree into sys.modules."""
    store = store if store is not None else {}
    kr = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")

    class FailKeyring:  # never returned by the fake get_keyring
        pass

    fail.Keyring = FailKeyring

    class _Backend:
        pass

    kr.get_keyring = lambda: _Backend()
    kr.get_password = lambda service, user: store.get((service, user))
    kr.set_password = lambda service, user, value: store.__setitem__((service, user), value)

    def _delete(service, user):
        del store[(service, user)]

    kr.delete_password = _delete
    kr.backends = backends
    backends.fail = fail
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    monkeypatch.delenv("GW_KEYRING", raising=False)
    return store


# ---------------------------------------------------------------------------
# Legacy fallback naming
# ---------------------------------------------------------------------------

class TestLegacyResolution:
    def test_default_account_is_main(self, env):
        acct = auth.resolve_account(None)
        assert acct["name"] == "main"
        assert acct["source"] == "legacy"
        assert acct["token_file"] == env.legacy_dir / ".google-token.json"

    def test_named_account_token_filename(self, env):
        acct = auth.resolve_account("mail")
        assert acct["token_file"].name == ".google-token-mail.json"

    def test_free_form_account_name(self, env):
        acct = auth.resolve_account("anything-goes")
        assert acct["source"] == "legacy"
        assert acct["token_file"].name == ".google-token-anything-goes.json"

    def test_get_token_path(self, env):
        assert auth.get_token_path("personal").name == ".google-token-personal.json"
        assert auth.get_token_path(None).name == ".google-token.json"

    def test_discover_legacy_accounts(self, env):
        (env.legacy_dir / ".google-token.json").write_text("{}")
        (env.legacy_dir / ".google-token-mail.json").write_text("{}")
        found = auth.discover_legacy_accounts()
        assert set(found) == {"main", "mail"}
        assert found["mail"].name == ".google-token-mail.json"


# ---------------------------------------------------------------------------
# Config-driven resolution
# ---------------------------------------------------------------------------

class TestConfigResolution:
    def test_config_entry_wins(self, env):
        _write_accounts(env, {
            "accounts": {"work": {"email": "w@example.com", "token_storage": "keyring"}},
        })
        acct = auth.resolve_account("work")
        assert acct["source"] == "config"
        assert acct["email"] == "w@example.com"
        assert acct["token_storage"] == "keyring"
        assert acct["token_file"] == env.config_dir / "token-work.json"

    def test_config_default_account(self, env):
        _write_accounts(env, {"default": "work", "accounts": {"work": {}}})
        assert auth.resolve_account(None)["name"] == "work"

    def test_no_config_default_falls_back_to_main(self, env):
        _write_accounts(env, {"accounts": {"work": {}}})
        assert auth.resolve_account(None)["name"] == "main"

    def test_relative_token_file_resolves_under_config_dir(self, env):
        _write_accounts(env, {"accounts": {"a": {"token_file": "tok.json"}}})
        assert auth.resolve_account("a")["token_file"] == env.config_dir / "tok.json"

    def test_absolute_token_file_honored(self, env, tmp_path):
        target = tmp_path / "elsewhere" / "tok.json"
        _write_accounts(env, {"accounts": {"a": {"token_file": str(target)}}})
        assert auth.resolve_account("a")["token_file"] == target

    def test_unlisted_account_falls_back_to_legacy(self, env):
        _write_accounts(env, {"accounts": {"work": {}}})
        acct = auth.resolve_account("mail")
        assert acct["source"] == "legacy"
        assert acct["token_file"] == env.legacy_dir / ".google-token-mail.json"

    def test_invalid_accounts_json_ignored(self, env):
        env.config_dir.mkdir(parents=True)
        (env.config_dir / "accounts.json").write_text("not json{")
        acct = auth.resolve_account("main")
        assert acct["source"] == "legacy"

    def test_save_accounts_config_roundtrip(self, env):
        auth.save_accounts_config({"default": "x", "accounts": {"x": {}}})
        assert auth.load_accounts_config() == {"default": "x", "accounts": {"x": {}}}
        if sys.platform != "win32":
            assert (env.config_dir.stat().st_mode & 0o777) == 0o700


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

class TestScopes:
    def test_full_set_includes_forms_responses_readonly(self):
        assert "https://www.googleapis.com/auth/forms.responses.readonly" in auth.SCOPES

    def test_legacy_required_excludes_forms_responses_readonly(self):
        assert (
            "https://www.googleapis.com/auth/forms.responses.readonly"
            not in auth._LEGACY_REQUIRED_SCOPES
        )

    def test_filter_by_service(self):
        scopes = auth.scopes_for(["gmail"])
        assert scopes == auth.SERVICE_SCOPES["gmail"]

    def test_multiple_services(self):
        scopes = auth.scopes_for(["calendar", "tasks"])
        assert scopes == [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/tasks",
        ]

    def test_readonly_variants(self):
        scopes = auth.scopes_for(["gmail"], readonly=True)
        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]

    def test_readonly_forms_includes_responses(self):
        scopes = auth.scopes_for(["forms"], readonly=True)
        assert "https://www.googleapis.com/auth/forms.responses.readonly" in scopes

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="Unknown service"):
            auth.scopes_for(["gmail", "nope"])

    def test_default_is_full_set(self):
        assert auth.scopes_for() == auth.SCOPES


# ---------------------------------------------------------------------------
# Token storage backends
# ---------------------------------------------------------------------------

class TestTokenStorage:
    def test_file_store_and_load(self, env):
        backend = auth.store_token("main", json.dumps({"token": "x"}))
        assert backend == "file"
        path = env.legacy_dir / ".google-token.json"
        assert path.exists()
        if sys.platform != "win32":
            assert (path.stat().st_mode & 0o777) == 0o600
        assert auth.load_token_data("main") == {"token": "x"}

    def test_keyring_off_env_disables(self, env):
        assert auth.keyring_available() is False

    def test_keyring_missing_falls_back_to_file(self, env, capsys):
        backend = auth.store_token("main", json.dumps({"token": "x"}), storage="keyring")
        assert backend == "file"
        assert (env.legacy_dir / ".google-token.json").exists()
        assert "falling back to file" in capsys.readouterr().err

    def test_fake_keyring_roundtrip(self, env, monkeypatch):
        store = _install_fake_keyring(monkeypatch)
        _write_accounts(env, {"accounts": {"work": {"token_storage": "keyring"}}})
        assert auth.keyring_available() is True
        backend = auth.store_token("work", json.dumps({"token": "secret"}), storage="keyring")
        assert backend == "keyring"
        assert store[("gw-cli", "work")] == json.dumps({"token": "secret"})
        assert auth.load_token_data("work") == {"token": "secret"}
        removed = auth.delete_token("work")
        assert removed == ["keyring"]
        assert ("gw-cli", "work") not in store

    def test_keyring_account_falls_back_to_file_read(self, env, monkeypatch):
        _install_fake_keyring(monkeypatch)  # empty keyring
        _write_accounts(env, {"accounts": {"work": {"token_storage": "keyring"}}})
        token_path = env.config_dir / "token-work.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps({"token": "from-file"}))
        os.chmod(token_path, 0o600)
        assert auth.load_token_data("work") == {"token": "from-file"}

    def test_delete_token_file(self, env):
        path = env.legacy_dir / ".google-token-mail.json"
        path.write_text("{}")
        removed = auth.delete_token("mail")
        assert removed == [str(path)]
        assert not path.exists()

    def test_delete_token_nothing_present(self, env):
        assert auth.delete_token("ghost") == []


# ---------------------------------------------------------------------------
# get_credentials
# ---------------------------------------------------------------------------

def _legacy_token_data(scopes=None):
    return {
        "token": "x",
        "refresh_token": "r",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": scopes if scopes is not None else list(auth._LEGACY_REQUIRED_SCOPES),
    }


class TestGetCredentials:
    def test_missing_token_raises_auth_error_with_login_hint(self, env):
        with pytest.raises(AuthError) as exc:
            auth.get_credentials("nosuch")
        assert "nosuch" in str(exc.value)
        assert "gw auth login --account nosuch" in exc.value.suggestion

    def test_legacy_token_loads(self, env):
        (env.legacy_dir / ".google-token.json").write_text(
            json.dumps(_legacy_token_data())
        )
        fake = MagicMock(valid=True, expired=False)
        with patch.object(auth.Credentials, "from_authorized_user_info", return_value=fake):
            assert auth.get_credentials("main") is fake

    def test_legacy_token_missing_scopes_rejected(self, env):
        (env.legacy_dir / ".google-token.json").write_text(
            json.dumps(_legacy_token_data(scopes=["https://www.googleapis.com/auth/gmail.send"]))
        )
        with pytest.raises(AuthError):
            auth.get_credentials("main")

    def test_config_account_narrow_scopes_allowed(self, env):
        _write_accounts(env, {"accounts": {"narrow": {"token_file": "tok.json"}}})
        env.config_dir.mkdir(parents=True, exist_ok=True)
        (env.config_dir / "tok.json").write_text(
            json.dumps(_legacy_token_data(scopes=["https://www.googleapis.com/auth/gmail.send"]))
        )
        fake = MagicMock(valid=True, expired=False)
        with patch.object(auth.Credentials, "from_authorized_user_info", return_value=fake):
            assert auth.get_credentials("narrow") is fake

    def test_corrupt_token_file_raises_auth_error(self, env):
        (env.legacy_dir / ".google-token.json").write_text("not json{")
        with pytest.raises(AuthError):
            auth.get_credentials("main")


# ---------------------------------------------------------------------------
# get_account_email
# ---------------------------------------------------------------------------

class TestAccountEmail:
    def test_config_email_no_network(self, env):
        _write_accounts(env, {"accounts": {"work": {"email": "w@example.com"}}})
        assert auth.get_account_email("work", fetch=False) == "w@example.com"

    def test_token_account_field_fallback(self, env):
        data = _legacy_token_data()
        data["account"] = "tok@example.com"
        (env.legacy_dir / ".google-token.json").write_text(json.dumps(data))
        assert auth.get_account_email("main", fetch=False) == "tok@example.com"

    def test_unknown_email_returns_none(self, env):
        assert auth.get_account_email("main", fetch=False) is None

    def test_email_cached(self, env):
        _write_accounts(env, {"accounts": {"work": {"email": "w@example.com"}}})
        auth.get_account_email("work", fetch=False)
        _write_accounts(env, {"accounts": {"work": {"email": "changed@example.com"}}})
        assert auth.get_account_email("work", fetch=False) == "w@example.com"


def _install_null_keyring(monkeypatch):
    """Inject a keyring whose active backend is the null (no-op) backend."""
    kr = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")
    null = types.ModuleType("keyring.backends.null")

    class FailKeyring:
        pass

    class NullKeyring:
        priority = -1

        def get_password(self, service, user):
            return None

        def set_password(self, service, user, value):
            return None  # silently discards

        def delete_password(self, service, user):
            return None

    fail.Keyring = FailKeyring
    null.Keyring = NullKeyring
    active = NullKeyring()
    kr.get_keyring = lambda: active
    kr.get_password = lambda s, u: active.get_password(s, u)
    kr.set_password = lambda s, u, v: active.set_password(s, u, v)
    kr.delete_password = lambda s, u: active.delete_password(s, u)
    kr.backends = backends
    backends.fail = fail
    backends.null = null
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    monkeypatch.setitem(sys.modules, "keyring.backends.null", null)
    monkeypatch.delenv("GW_KEYRING", raising=False)


def _install_silent_discard_keyring(monkeypatch):
    """Keyring with a normal-looking backend that accepts writes but never
    persists them (get_password always None) — exercises the read-back guard."""
    kr = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")

    class FailKeyring:
        pass

    class _Backend:
        pass

    fail.Keyring = FailKeyring
    kr.get_keyring = lambda: _Backend()
    kr.get_password = lambda s, u: None
    kr.set_password = lambda s, u, v: None
    kr.delete_password = lambda s, u: None
    kr.backends = backends
    backends.fail = fail
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    monkeypatch.delenv("GW_KEYRING", raising=False)


# ---------------------------------------------------------------------------
# Account name validation (path traversal, reserved names)
# ---------------------------------------------------------------------------

class TestAccountNameValidation:
    def test_traversal_name_rejected_on_resolve(self, env):
        with pytest.raises(AuthError):
            auth.resolve_account("x/../../evil")

    def test_traversal_store_raises_and_writes_nothing(self, env, tmp_path):
        outside = tmp_path / "evil.json"
        with pytest.raises(AuthError):
            auth.store_token("x/../../evil", json.dumps({"refresh_token": "SECRET"}), storage="file")
        assert not outside.exists()
        # nothing landed outside the config dir either
        assert list(tmp_path.glob("evil*")) == []

    def test_traversal_delete_rejected(self, env):
        with pytest.raises(AuthError):
            auth.delete_token("x/../../evil")

    @pytest.mark.parametrize("bad", ["x/y", "x\\y", "..", "a..b", ".hidden", "con", "COM1", "nul.json", "a" * 65])
    def test_bad_names_rejected(self, env, bad):
        with pytest.raises(AuthError):
            auth.resolve_account(bad)

    @pytest.mark.parametrize("bad", ["", "x/y", "..", "\x00abc", "con"])
    def test_validator_rejects_directly(self, bad):
        with pytest.raises(AuthError):
            auth._validate_account_name(bad)

    @pytest.mark.parametrize("good", ["main", "mail", "personal", "anything-goes", "work_2", "a", "outreach2"])
    def test_good_names_allowed(self, env, good):
        assert auth.resolve_account(good)["name"] == good


# ---------------------------------------------------------------------------
# Malformed config shape (must not crash)
# ---------------------------------------------------------------------------

class TestConfigShape:
    def test_non_dict_accounts_falls_back_to_legacy(self, env):
        _write_accounts(env, {"accounts": ["main"]})
        acct = auth.resolve_account("main")
        assert acct["source"] == "legacy"

    def test_non_dict_entry_falls_back_to_legacy(self, env):
        _write_accounts(env, {"accounts": {"main": "keyring"}})
        acct = auth.resolve_account("main")
        assert acct["source"] == "legacy"

    def test_non_string_default_ignored(self, env):
        _write_accounts(env, {"default": ["x"], "accounts": {}})
        assert auth.resolve_account(None)["name"] == "main"


# ---------------------------------------------------------------------------
# Config dir expansion / anchoring
# ---------------------------------------------------------------------------

class TestConfigDir:
    def test_tilde_expanded(self, monkeypatch):
        monkeypatch.setenv("GW_CONFIG_DIR", "~/gw-tilde-test")
        got = auth.get_config_dir()
        assert got.is_absolute()
        assert "~" not in str(got)
        assert got == Path.home() / "gw-tilde-test"

    def test_relative_anchored_to_absolute(self, monkeypatch):
        monkeypatch.setenv("GW_CONFIG_DIR", "rel-cfg-dir")
        got = auth.get_config_dir()
        assert got.is_absolute()


# ---------------------------------------------------------------------------
# Corrupt config must never silently drop accounts on write
# ---------------------------------------------------------------------------

class TestCorruptConfigNoDataLoss:
    def test_write_over_corrupt_refuses_and_backs_up(self, env):
        from output import CliError

        env.config_dir.mkdir(parents=True)
        corrupt = env.config_dir / "accounts.json"
        corrupt.write_text('{"accounts": {"a": {}, "b": {}} BROKEN')
        with pytest.raises(CliError):
            auth.save_accounts_config({"accounts": {"c": {}}})
        # original untouched, corrupt copy preserved for recovery
        assert corrupt.read_text().startswith('{"accounts": {"a"')
        assert (env.config_dir / "accounts.json.corrupt").exists()


# ---------------------------------------------------------------------------
# Concurrent-write safety (re-read-merge under lock)
# ---------------------------------------------------------------------------

class TestConcurrentWrites:
    def test_update_rereads_and_merges(self, env):
        _write_accounts(env, {"accounts": {"a": {}}})
        auth.update_accounts_config(lambda c: c.setdefault("accounts", {}).__setitem__("b", {}))
        auth.update_accounts_config(lambda c: c.setdefault("accounts", {}).__setitem__("c", {}))
        assert set(auth.load_accounts_config()["accounts"]) == {"a", "b", "c"}

    def test_update_does_not_clobber_concurrent_writer(self, env):
        _write_accounts(env, {"accounts": {"a": {}}})
        # another process added 'b' on disk after we'd have loaded
        _write_accounts(env, {"accounts": {"a": {}, "b": {}}})
        auth.update_accounts_config(lambda c: c["accounts"].__setitem__("c", {}))
        assert set(auth.load_accounts_config()["accounts"]) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Keyring backend detection (null / silent-discard)
# ---------------------------------------------------------------------------

class TestKeyringBackends:
    def test_null_backend_treated_unavailable(self, env, monkeypatch):
        _install_null_keyring(monkeypatch)
        assert auth.keyring_available() is False
        backend = auth.store_token("main", json.dumps({"token": "x"}), storage="keyring")
        assert backend == "file"
        assert (env.legacy_dir / ".google-token.json").exists()

    def test_silent_discard_falls_back_via_readback(self, env, monkeypatch, capsys):
        _install_silent_discard_keyring(monkeypatch)
        backend = auth.store_token("main", json.dumps({"token": "x"}), storage="keyring")
        assert backend == "file"
        assert (env.legacy_dir / ".google-token.json").exists()
        assert "falling back to file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# logout / delete legacy sweep + ambiguity refusal
# ---------------------------------------------------------------------------

class TestDeleteLegacySweep:
    def test_config_account_delete_sweeps_lingering_legacy(self, env):
        _write_accounts(env, {"accounts": {"main": {"token_storage": "keyring"}}})
        legacy = env.legacy_dir / ".google-token.json"
        legacy.write_text(json.dumps({"refresh_token": "r"}))
        removed = auth.delete_token("main")
        assert str(legacy) in removed
        assert not legacy.exists()

    def test_legacy_delete_refuses_when_dir_ambiguous(self, env, monkeypatch):
        (env.legacy_dir / ".google-token-mail.json").write_text("{}")
        monkeypatch.setattr(auth, "_unambiguous_legacy_dir", lambda: None)
        with pytest.raises(AuthError):
            auth.delete_token("mail")


# ---------------------------------------------------------------------------
# Unknown-account error guides to known accounts
# ---------------------------------------------------------------------------

class TestUnknownAccountError:
    def test_unknown_account_lists_known(self, env):
        _write_accounts(env, {"accounts": {"main": {}, "work": {}}})
        (env.legacy_dir / ".google-token-mail.json").write_text("{}")
        with pytest.raises(AuthError) as exc:
            auth.get_credentials("mial")
        suggestion = exc.value.suggestion
        assert "work" in suggestion and "mail" in suggestion

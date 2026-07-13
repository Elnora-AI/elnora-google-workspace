"""Tests for the generic Discovery invoker (gw api / gw schema) — no network."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "cli"))

import discovery
import gw
from output import CliError, ValidationError

GW = str(_PLUGIN_ROOT / "cli" / "gw.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_DOC = {
    "kind": "discovery#restDescription",
    "name": "fakemail",
    "version": "v1",
    "title": "Fake Mail API",
    "rootUrl": "https://fakemail.example.com/",
    "servicePath": "",
    "parameters": {
        "fields": {"type": "string", "location": "query", "description": "Field selector"},
        "alt": {"type": "string", "location": "query"},
    },
    "schemas": {
        "Message": {
            "id": "Message", "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Message id"},
                "payload": {"$ref": "MessagePart"},
            },
        },
        "MessagePart": {
            "id": "MessagePart", "type": "object",
            "properties": {
                "body": {"type": "string"},
                "parts": {"type": "array", "items": {"$ref": "MessagePart"}},
            },
        },
        "ListMessagesResponse": {
            "id": "ListMessagesResponse", "type": "object",
            "properties": {
                "messages": {"type": "array", "items": {"$ref": "Message"}},
                "nextPageToken": {"type": "string"},
            },
        },
    },
    "resources": {
        "users": {
            "methods": {
                "getProfile": {
                    "id": "fakemail.users.getProfile",
                    "path": "v1/users/{userId}/profile",
                    "flatPath": "v1/users/{userId}/profile",
                    "httpMethod": "GET",
                    "description": "Gets the user profile.",
                    "parameters": {
                        "userId": {"type": "string", "required": True, "location": "path"},
                    },
                    "scopes": ["https://example.com/auth/fakemail.readonly"],
                    "response": {"$ref": "Message"},
                },
            },
            "resources": {
                "messages": {
                    "methods": {
                        "list": {
                            "id": "fakemail.users.messages.list",
                            "path": "v1/users/{userId}/messages",
                            "httpMethod": "GET",
                            "description": "Lists messages.",
                            "parameters": {
                                "userId": {"type": "string", "required": True, "location": "path"},
                                "maxResults": {"type": "integer", "location": "query"},
                                "pageToken": {"type": "string", "location": "query"},
                            },
                            "scopes": ["https://example.com/auth/fakemail.readonly"],
                            "response": {"$ref": "ListMessagesResponse"},
                        },
                        "send": {
                            "id": "fakemail.users.messages.send",
                            "path": "v1/users/{userId}/messages/send",
                            "httpMethod": "POST",
                            "parameters": {
                                "userId": {"type": "string", "required": True, "location": "path"},
                            },
                            "request": {"$ref": "Message"},
                            "response": {"$ref": "Message"},
                        },
                        "delete": {
                            "id": "fakemail.users.messages.delete",
                            "path": "v1/users/{userId}/messages/{id}",
                            "httpMethod": "POST",  # segment rule must trigger regardless
                            "parameters": {
                                "userId": {"type": "string", "required": True, "location": "path"},
                                "id": {"type": "string", "required": True, "location": "path"},
                            },
                        },
                        "erase": {
                            "id": "fakemail.users.messages.erase",
                            "path": "v1/users/{userId}/messages/{id}",
                            "httpMethod": "DELETE",  # httpMethod rule
                            "parameters": {
                                "userId": {"type": "string", "required": True, "location": "path"},
                                "id": {"type": "string", "required": True, "location": "path"},
                            },
                        },
                    },
                },
            },
        },
    },
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config/token dirs; guard env cleared."""
    config_dir = tmp_path / "config"
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    monkeypatch.setenv("GW_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("GOOGLE_WORKSPACE_TOKEN_DIR", str(legacy_dir))
    monkeypatch.setenv("GW_KEYRING", "off")
    monkeypatch.delenv("GW_API_CONFIRM", raising=False)
    return SimpleNamespace(config_dir=config_dir, legacy_dir=legacy_dir)


class FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class FakeMessages:
    """Mimics service.users().messages() with paged list + delete."""

    def __init__(self, pages):
        self.pages = pages
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        idx = int(kwargs.get("pageToken") or 0)
        return FakeRequest(self.pages[idx])

    def delete(self, **kwargs):
        return FakeRequest({})

    def erase(self, **kwargs):
        return FakeRequest({})


class FakeService:
    def __init__(self, messages):
        self._messages = messages

    def users(self):
        return self

    def messages(self):
        return self._messages


def make_pages(n):
    return [
        {
            "messages": [{"id": f"m{i}"}],
            **({"nextPageToken": str(i + 1)} if i < n - 1 else {}),
        }
        for i in range(n)
    ]


def invoke_call(**over):
    """Invoke the api call command callback directly (in-process)."""
    kwargs = dict(
        service="fakemail:v1", method_path="users.messages.list",
        params_json=None, body_json=None, json_file=None,
        dry_run=False, page_all=False, page_limit=10, page_delay_ms=0,
        confirm=False, no_cache=False, account=None, compact=True,
    )
    kwargs.update(over)
    gw.cli.commands["api"].commands["call"].callback(**kwargs)


# ---------------------------------------------------------------------------
# Service alias resolution + escape hatch
# ---------------------------------------------------------------------------

class TestServiceResolution:
    REQUIRED_ALIASES = [
        "gmail", "calendar", "drive", "docs", "sheets", "slides", "tasks",
        "people", "chat", "forms", "keep", "meet", "script", "classroom",
        "admin-reports",
    ]

    def test_required_aliases_present(self):
        for alias in self.REQUIRED_ALIASES:
            assert alias in discovery.SERVICE_ALIASES

    def test_alias_resolves(self):
        assert discovery.resolve_service("gmail") == ("gmail", "v1")
        assert discovery.resolve_service("sheets") == ("sheets", "v4")
        assert discovery.resolve_service("admin-reports") == ("admin", "reports_v1")

    def test_escape_hatch(self):
        assert discovery.resolve_service("admin:directory_v1") == ("admin", "directory_v1")
        assert discovery.resolve_service("fakemail:v1") == ("fakemail", "v1")

    def test_malformed_escape_hatch(self):
        with pytest.raises(ValidationError):
            discovery.resolve_service(":v1")
        with pytest.raises(ValidationError):
            discovery.resolve_service("gmail:")

    def test_unknown_alias_lists_options(self):
        with pytest.raises(ValidationError) as exc:
            discovery.resolve_service("nope")
        assert "gmail" in exc.value.suggestion
        assert "api:version" in exc.value.suggestion


# ---------------------------------------------------------------------------
# Method-path walking
# ---------------------------------------------------------------------------

class TestMethodResolution:
    def test_walks_nested_path(self):
        method = discovery.resolve_method(FAKE_DOC, "users.messages.list")
        assert method["id"] == "fakemail.users.messages.list"

    def test_top_level_resource_method(self):
        method = discovery.resolve_method(FAKE_DOC, "users.getProfile")
        assert method["httpMethod"] == "GET"

    def test_resource_not_method(self):
        with pytest.raises(ValidationError) as exc:
            discovery.resolve_method(FAKE_DOC, "users.messages")
        assert "resource, not a method" in exc.value.message
        assert "list" in exc.value.suggestion

    def test_unknown_segment_lists_options(self):
        with pytest.raises(ValidationError) as exc:
            discovery.resolve_method(FAKE_DOC, "users.messages.nope")
        assert "nope" in exc.value.message
        assert "list" in exc.value.suggestion

    def test_unknown_root(self):
        with pytest.raises(ValidationError) as exc:
            discovery.resolve_method(FAKE_DOC, "ghosts.list")
        assert "users" in exc.value.suggestion

    def test_resolve_resource(self):
        node = discovery.resolve_resource(FAKE_DOC, "users.messages")
        assert "list" in node["methods"]
        with pytest.raises(ValidationError):
            discovery.resolve_resource(FAKE_DOC, "users.ghosts")


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------

class TestParamValidation:
    METHOD = FAKE_DOC["resources"]["users"]["resources"]["messages"]["methods"]["list"]

    def test_valid_params_pass(self):
        discovery.validate_params(FAKE_DOC, self.METHOD, {"userId": "me", "maxResults": 5})

    def test_common_doc_param_allowed(self):
        discovery.validate_params(FAKE_DOC, self.METHOD, {"userId": "me", "fields": "messages"})

    def test_unknown_param(self):
        with pytest.raises(ValidationError) as exc:
            discovery.validate_params(FAKE_DOC, self.METHOD, {"userId": "me", "bogus": 1})
        assert "bogus" in exc.value.message

    def test_missing_required(self):
        with pytest.raises(ValidationError) as exc:
            discovery.validate_params(FAKE_DOC, self.METHOD, {"maxResults": 5})
        assert "userId" in exc.value.message


# ---------------------------------------------------------------------------
# Dry-run (auth-free)
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_output_shape_and_no_auth(self, env, monkeypatch, capfd):
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        import auth
        monkeypatch.setattr(auth, "get_credentials", lambda *a, **k: pytest.fail("dry-run must not touch auth"))

        invoke_call(
            method_path="users.getProfile",
            params_json='{"userId":"me"}',
            dry_run=True,
        )
        payload = json.loads(capfd.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["service"] == "fakemail:v1"
        assert payload["method"] == "users.getProfile"
        assert payload["httpMethod"] == "GET"
        assert payload["uri_template"] == "https://fakemail.example.com/v1/users/{userId}/profile"
        assert payload["params"] == {"userId": "me"}
        assert payload["body"] is None
        assert payload["required_scopes"] == ["https://example.com/auth/fakemail.readonly"]
        assert payload["destructive"] is False

    def test_dry_run_validates_params(self, env, monkeypatch, capfd):
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        with pytest.raises(SystemExit) as exc:
            invoke_call(method_path="users.getProfile", params_json='{"bogus":1}', dry_run=True)
        assert exc.value.code == 2
        err = json.loads(capfd.readouterr().err)
        assert err["code"] == "VALIDATION_ERROR"
        assert "bogus" in err["error"]

    def test_dry_run_marks_destructive_without_confirm(self, env, monkeypatch, capfd):
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        invoke_call(
            method_path="users.messages.delete",
            params_json='{"userId":"me","id":"m1"}',
            dry_run=True,
        )
        payload = json.loads(capfd.readouterr().out)
        assert payload["destructive"] is True


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_paginate_follows_tokens(self):
        pages = make_pages(3)
        seen = list(discovery.paginate(
            lambda tok: pages[int(tok or 0)], page_limit=10, page_delay_ms=0,
        ))
        assert seen == pages

    def test_paginate_respects_limit(self):
        pages = make_pages(5)
        seen = list(discovery.paginate(
            lambda tok: pages[int(tok or 0)], page_limit=2, page_delay_ms=0,
        ))
        assert len(seen) == 2

    def test_paginate_delay_between_pages(self):
        pages = make_pages(3)
        naps = []
        list(discovery.paginate(
            lambda tok: pages[int(tok or 0)],
            page_limit=10, page_delay_ms=250, sleep=naps.append,
        ))
        assert naps == [0.25, 0.25]  # between pages only

    def test_cli_emits_ndjson(self, env, monkeypatch, capfd):
        pages = make_pages(3)
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        monkeypatch.setattr(
            discovery, "build_dynamic_service",
            lambda doc, account=None: FakeService(FakeMessages(pages)),
        )
        invoke_call(params_json='{"userId":"me"}', page_all=True)
        lines = capfd.readouterr().out.strip().splitlines()
        assert len(lines) == 3
        assert [json.loads(line)["messages"][0]["id"] for line in lines] == ["m0", "m1", "m2"]

    def test_cli_single_call_envelope(self, env, monkeypatch, capfd):
        pages = make_pages(1)
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        monkeypatch.setattr(
            discovery, "build_dynamic_service",
            lambda doc, account=None: FakeService(FakeMessages(pages)),
        )
        invoke_call(params_json='{"userId":"me"}')
        payload = json.loads(capfd.readouterr().out)
        assert payload == pages[0]


# ---------------------------------------------------------------------------
# Destructive guard
# ---------------------------------------------------------------------------

class TestDestructiveGuard:
    def test_is_destructive_segments_and_http_method(self):
        methods = FAKE_DOC["resources"]["users"]["resources"]["messages"]["methods"]
        assert discovery.is_destructive("users.messages.delete", methods["delete"])
        assert discovery.is_destructive("users.messages.erase", methods["erase"])  # HTTP DELETE
        assert not discovery.is_destructive("users.messages.list", methods["list"])
        for seg in ("clear", "remove", "trash", "stop", "revoke"):
            assert discovery.is_destructive(f"x.{seg}", {"httpMethod": "POST"})

    def test_blocked_without_confirm(self, env, monkeypatch, capfd):
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        with pytest.raises(SystemExit) as exc:
            invoke_call(method_path="users.messages.delete", params_json='{"userId":"me","id":"m1"}')
        assert exc.value.code == 2
        err = json.loads(capfd.readouterr().err)
        assert err["code"] == "CONFIRM_REQUIRED"
        assert "--confirm" in err["suggestion"]

    def test_confirm_flag_allows(self, env, monkeypatch, capfd):
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        monkeypatch.setattr(
            discovery, "build_dynamic_service",
            lambda doc, account=None: FakeService(FakeMessages([])),
        )
        invoke_call(
            method_path="users.messages.delete",
            params_json='{"userId":"me","id":"m1"}', confirm=True,
        )
        assert json.loads(capfd.readouterr().out) == {"status": "ok"}

    def test_env_off_disables_guard(self, env, monkeypatch, capfd):
        monkeypatch.setenv("GW_API_CONFIRM", "off")
        monkeypatch.setattr(discovery, "get_discovery_doc", lambda *a, **k: FAKE_DOC)
        monkeypatch.setattr(
            discovery, "build_dynamic_service",
            lambda doc, account=None: FakeService(FakeMessages([])),
        )
        invoke_call(method_path="users.messages.delete", params_json='{"userId":"me","id":"m1"}')
        assert json.loads(capfd.readouterr().out) == {"status": "ok"}


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------

class TestSchema:
    def test_ref_cycle_terminates(self):
        resolved = discovery.resolve_schema(FAKE_DOC["schemas"], "MessagePart", max_depth=5)
        # parts.items refers back to MessagePart — must collapse to a bare $ref
        assert resolved["properties"]["parts"]["items"] == {"$ref": "MessagePart"}

    def test_depth_limit(self):
        resolved = discovery.resolve_schema(FAKE_DOC["schemas"], "Message", max_depth=1)
        assert resolved["properties"]["payload"] == {"$ref": "MessagePart"}

    def test_unknown_ref_left_as_is(self):
        resolved = discovery.resolve_schema({}, "Ghost")
        assert resolved == {"$ref": "Ghost"}

    def test_method_schema_shape(self):
        payload = discovery.method_schema(FAKE_DOC, "fakemail", "v1", "users.messages.send")
        assert payload["service"] == "fakemail:v1"
        assert payload["httpMethod"] == "POST"
        assert payload["required_scopes"] == []
        [param] = payload["parameters"]
        assert param == {
            "name": "userId", "type": "string", "required": True,
            "location": "path", "description": None,
        }
        assert payload["request"]["properties"]["id"]["type"] == "string"
        assert payload["response"]["properties"]["payload"]["properties"]["parts"]["items"] == {"$ref": "MessagePart"}

    def test_schema_command_requires_method_path(self, env, capfd):
        with pytest.raises(SystemExit) as exc:
            gw.cli.commands["schema"].callback(spec="gmail", depth=3, no_cache=False, compact=True)
        assert exc.value.code == 2
        assert "SERVICE.RESOURCE.METHOD" in json.loads(capfd.readouterr().err)["suggestion"]


# ---------------------------------------------------------------------------
# Discovery doc cache (TTL, corruption, offline fallback)
# ---------------------------------------------------------------------------

class TestCache:
    @pytest.fixture
    def fetches(self, env, monkeypatch):
        counter = SimpleNamespace(count=0, fail=False)

        def fake_fetch(api, version):
            if counter.fail:
                raise discovery.DiscoveryUnavailableError(api, version)
            counter.count += 1
            return FAKE_DOC

        monkeypatch.setattr(discovery, "fetch_discovery_doc", fake_fetch)
        return counter

    def test_fresh_cache_skips_fetch(self, fetches, monkeypatch):
        monkeypatch.setattr(discovery, "_now", lambda: 1_000_000.0)
        assert discovery.get_discovery_doc("fakemail", "v1") == FAKE_DOC
        assert discovery.get_discovery_doc("fakemail", "v1") == FAKE_DOC
        assert fetches.count == 1

    def test_expired_cache_refetches(self, fetches, monkeypatch):
        clock = SimpleNamespace(now=1_000_000.0)
        monkeypatch.setattr(discovery, "_now", lambda: clock.now)
        discovery.get_discovery_doc("fakemail", "v1")
        clock.now += discovery.CACHE_TTL_SECONDS + 1
        discovery.get_discovery_doc("fakemail", "v1")
        assert fetches.count == 2

    def test_no_cache_bypasses_fresh_cache(self, fetches, monkeypatch):
        monkeypatch.setattr(discovery, "_now", lambda: 1_000_000.0)
        discovery.get_discovery_doc("fakemail", "v1")
        discovery.get_discovery_doc("fakemail", "v1", no_cache=True)
        assert fetches.count == 2

    def test_corrupt_cache_refetches(self, fetches, env):
        discovery.get_discovery_doc("fakemail", "v1")
        cache_file = env.config_dir / "cache" / "discovery" / "fakemail.v1.json"
        cache_file.write_text("{not json")
        assert discovery.get_discovery_doc("fakemail", "v1") == FAKE_DOC
        assert fetches.count == 2

    def test_offline_uses_stale_cache_with_warning(self, fetches, monkeypatch, capfd):
        clock = SimpleNamespace(now=1_000_000.0)
        monkeypatch.setattr(discovery, "_now", lambda: clock.now)
        discovery.get_discovery_doc("fakemail", "v1")
        clock.now += discovery.CACHE_TTL_SECONDS + 1
        fetches.fail = True
        assert discovery.get_discovery_doc("fakemail", "v1") == FAKE_DOC
        warning = json.loads(capfd.readouterr().err)
        assert warning["code"] == "STALE_DISCOVERY_CACHE"

    def test_offline_no_cache_raises(self, fetches):
        fetches.fail = True
        with pytest.raises(discovery.DiscoveryUnavailableError):
            discovery.get_discovery_doc("fakemail", "v1")


# ---------------------------------------------------------------------------
# Scope-error enrichment
# ---------------------------------------------------------------------------

class FakeHttpError(Exception):
    def __init__(self, status: int, content: bytes):
        super().__init__(f"HTTP {status}")
        self.resp = SimpleNamespace(status=status)
        self.content = content


class TestScopeErrors:
    def test_403_insufficient_scopes(self, env):
        err = FakeHttpError(
            403, b'{"error":{"message":"Request had insufficient authentication scopes."}}',
        )
        method = {"scopes": ["https://example.com/auth/fakemail.readonly"]}
        with pytest.raises(CliError) as exc:
            discovery.handle_api_http_error(
                err, method=method, method_path="users.messages.list", account="main",
            )
        assert exc.value.code == "INSUFFICIENT_SCOPES"
        assert "fakemail.readonly" in exc.value.suggestion
        assert "gw auth login --account main" in exc.value.suggestion

    def test_other_403_falls_through(self, env):
        err = FakeHttpError(403, b'{"error":{"message":"Rate limit for this domain."}}')
        with pytest.raises(CliError) as exc:
            discovery.handle_api_http_error(
                err, method={}, method_path="users.messages.list", account="main",
            )
        assert exc.value.code != "INSUFFICIENT_SCOPES"


# ---------------------------------------------------------------------------
# CLI plumbing (subprocess, offline-safe)
# ---------------------------------------------------------------------------

def run(*args: str) -> tuple[str, str, int]:
    env = {**os.environ, "NO_COLOR": "1"}
    result = subprocess.run(
        [sys.executable, GW, *args],
        capture_output=True, text=True, timeout=10, env=env,
    )
    return result.stdout, result.stderr, result.returncode


class TestCliPlumbing:
    def test_api_help(self):
        stdout, _, code = run("api", "--help")
        assert code == 0
        assert "call" in stdout
        assert "describe" in stdout
        assert "list" in stdout
        assert "--confirm" in run("api", "call", "--help")[0]

    def test_schema_help(self):
        stdout, _, code = run("schema", "--help")
        assert code == 0
        assert "SERVICE.RESOURCE.METHOD" in stdout

    def test_api_list(self):
        stdout, _, code = run("api", "list", "--compact")
        assert code == 0
        payload = json.loads(stdout)
        aliases = {s["alias"]: (s["api"], s["version"]) for s in payload["services"]}
        assert aliases["gmail"] == ("gmail", "v1")
        assert aliases["admin-directory"] == ("admin", "directory_v1")
        assert "api:version" in payload["escape_hatch"]

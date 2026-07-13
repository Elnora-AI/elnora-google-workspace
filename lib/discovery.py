"""Generic Google API Discovery invoker.

Resolves any Google API method from its Discovery document, validates
parameters, and builds/executes the call dynamically. Discovery docs are
cached under ``<config-dir>/cache/discovery`` with a 24h TTL.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from output import (
    CliError,
    ValidationError,
    handle_http_error,
    output_warning,
)

# ---------------------------------------------------------------------------
# Service aliases — mirrors the googleworkspace/cli service set.
# Anything else is reachable via the "api:version" escape hatch.
# ---------------------------------------------------------------------------

SERVICE_ALIASES: dict[str, tuple[str, str]] = {
    "gmail": ("gmail", "v1"),
    "calendar": ("calendar", "v3"),
    "drive": ("drive", "v3"),
    "docs": ("docs", "v1"),
    "sheets": ("sheets", "v4"),
    "slides": ("slides", "v1"),
    "tasks": ("tasks", "v1"),
    "people": ("people", "v1"),
    "chat": ("chat", "v1"),
    "forms": ("forms", "v1"),
    "keep": ("keep", "v1"),
    "meet": ("meet", "v2"),
    "script": ("script", "v1"),
    "classroom": ("classroom", "v1"),
    "admin-directory": ("admin", "directory_v1"),
    "admin-reports": ("admin", "reports_v1"),
    "licensing": ("licensing", "v1"),
    "drive-activity": ("driveactivity", "v2"),
}

DESTRUCTIVE_SEGMENTS = {"delete", "clear", "remove", "trash", "stop", "revoke"}

CACHE_TTL_SECONDS = 24 * 60 * 60

_DISCOVERY_URLS = (
    "https://www.googleapis.com/discovery/v1/apis/{api}/{version}/rest",
    "https://{api}.googleapis.com/$discovery/rest?version={version}",
)


class DiscoveryUnavailableError(CliError):
    """Discovery service unreachable (offline / DNS / timeout)."""

    def __init__(self, api: str, version: str) -> None:
        super().__init__(
            f"Could not reach the Google Discovery service for {api}:{version}.",
            suggestion="Check network connectivity, or retry once online (docs are cached 24h).",
            code="DISCOVERY_UNAVAILABLE",
        )


class ConfirmationRequiredError(CliError):
    """Destructive method invoked without --confirm."""

    exit_code = 2  # EXIT_VALIDATION

    def __init__(self, method_path: str) -> None:
        super().__init__(
            f"'{method_path}' is a destructive method (delete/clear/remove/trash/stop/revoke or HTTP DELETE).",
            suggestion=(
                "Re-run with --confirm to execute it. "
                "Set GW_API_CONFIRM=off to disable this guard entirely."
            ),
            code="CONFIRM_REQUIRED",
        )


def resolve_service(service: str) -> tuple[str, str]:
    """Resolve a service alias or 'api:version' escape hatch to (api, version)."""
    if ":" in service:
        api, _, version = service.partition(":")
        if not api or not version:
            raise ValidationError(
                f"Invalid service '{service}'. Escape-hatch form is api:version, e.g. admin:directory_v1."
            )
        return api, version
    if service in SERVICE_ALIASES:
        return SERVICE_ALIASES[service]
    raise ValidationError(
        f"Unknown service alias '{service}'.",
        suggestion=(
            f"Aliases: {', '.join(sorted(SERVICE_ALIASES))}. "
            "Any other Google API works as api:version (see 'gw api list')."
        ),
    )


# ---------------------------------------------------------------------------
# Discovery doc fetch + cache
# ---------------------------------------------------------------------------

def _now() -> float:
    """Current epoch time — module-level so tests can inject a clock."""
    return time.time()


def cache_dir() -> Path:
    import auth

    return auth.get_config_dir() / "cache" / "discovery"


def _cache_path(api: str, version: str) -> Path:
    return cache_dir() / f"{api}.{version}.json"


def _read_cache(path: Path) -> tuple[dict, bool] | None:
    """Read a cached doc. Returns (doc, fresh) or None if missing/corrupt."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            wrapper = json.load(f)
        doc = wrapper["doc"]
        fetched_at = float(wrapper["fetched_at"])
        if not isinstance(doc, dict):
            return None
    except (ValueError, KeyError, TypeError, OSError):
        return None
    return doc, (_now() - fetched_at) < CACHE_TTL_SECONDS


def fetch_discovery_doc(api: str, version: str) -> dict:
    """Fetch a discovery doc over HTTP (legacy directory URL, then per-service)."""
    import urllib.error
    import urllib.request

    not_found = False
    for tmpl in _DISCOVERY_URLS:
        url = tmpl.format(api=api, version=version)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            if err.code == 404:
                not_found = True
                continue
            raise CliError(
                f"Discovery fetch failed ({err.code}) for {api}:{version}."
            ) from err
        except (urllib.error.URLError, OSError) as err:
            raise DiscoveryUnavailableError(api, version) from err
        except ValueError as err:
            raise CliError(
                f"Discovery service returned invalid JSON for {api}:{version}."
            ) from err
    assert not_found
    raise ValidationError(
        f"No discovery document found for '{api}:{version}'.",
        suggestion="Check the API name and version (directory: https://discovery.googleapis.com/discovery/v1/apis).",
    )


def get_discovery_doc(api: str, version: str, *, no_cache: bool = False) -> dict:
    """Get a discovery doc: fresh cache → network → stale cache (with warning)."""
    path = _cache_path(api, version)
    cached = None if no_cache else _read_cache(path)
    if cached and cached[1]:
        return cached[0]
    try:
        doc = fetch_discovery_doc(api, version)
    except DiscoveryUnavailableError:
        if cached:
            output_warning(
                f"Offline — using cached discovery doc for {api}:{version} (may be stale).",
                code="STALE_DISCOVERY_CACHE",
            )
            return cached[0]
        raise
    import auth

    auth._atomic_write(path, json.dumps({"fetched_at": _now(), "doc": doc}))
    return doc


# ---------------------------------------------------------------------------
# Discovery doc navigation
# ---------------------------------------------------------------------------

def resolve_method(doc: dict, method_path: str) -> dict:
    """Walk a dotted method path (e.g. users.messages.list) to its method spec."""
    segs = [s for s in method_path.split(".") if s]
    if not segs:
        raise ValidationError("Empty method path.")
    node = doc
    for i, seg in enumerate(segs):
        resources = node.get("resources") or {}
        methods = node.get("methods") or {}
        if i == len(segs) - 1 and seg in methods:
            return methods[seg]
        if i == len(segs) - 1 and seg in resources:
            opts = sorted(resources[seg].get("methods") or {})
            raise ValidationError(
                f"'{method_path}' is a resource, not a method.",
                suggestion=f"Methods on it: {', '.join(opts) or '(none)'}. See 'gw api describe'.",
            )
        if seg in resources:
            node = resources[seg]
            continue
        opts = sorted(set(resources) | set(methods))
        raise ValidationError(
            f"Unknown segment '{seg}' in method path '{method_path}'.",
            suggestion=f"Available at this level: {', '.join(opts[:25]) or '(none)'}. See 'gw api describe'.",
        )
    raise ValidationError(f"Could not resolve method '{method_path}'.")


def resolve_resource(doc: dict, resource_path: str) -> dict:
    """Walk a dotted resource path to its resource node."""
    node = doc
    for seg in [s for s in resource_path.split(".") if s]:
        sub = (node.get("resources") or {}).get(seg)
        if sub is None:
            opts = sorted(node.get("resources") or {})
            raise ValidationError(
                f"Unknown resource '{seg}' in '{resource_path}'.",
                suggestion=f"Available: {', '.join(opts[:25]) or '(none)'}.",
            )
        node = sub
    return node


def validate_params(doc: dict, method: dict, params: dict) -> None:
    """Reject unknown params and missing required params (Discovery-defined)."""
    allowed = dict(doc.get("parameters") or {})
    allowed.update(method.get("parameters") or {})
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise ValidationError(
            f"Unknown parameter(s): {', '.join(unknown)}",
            suggestion=f"Valid parameters: {', '.join(sorted(allowed)) or '(none)'}. See 'gw schema'.",
        )
    missing = sorted(
        name
        for name, spec in (method.get("parameters") or {}).items()
        if spec.get("required") and name not in params
    )
    if missing:
        raise ValidationError(f"Missing required parameter(s): {', '.join(missing)}")


def is_destructive(method_path: str, method: dict) -> bool:
    """True for delete/clear/remove/trash/stop/revoke methods or HTTP DELETE."""
    last = method_path.rsplit(".", 1)[-1].lower()
    return last in DESTRUCTIVE_SEGMENTS or (method.get("httpMethod") or "").upper() == "DELETE"


def _short(desc: str | None, limit: int = 120) -> str | None:
    if not desc:
        return None
    desc = " ".join(desc.split())
    return desc if len(desc) <= limit else desc[: limit - 3] + "..."


def dry_run_payload(
    doc: dict, api: str, version: str, method_path: str, method: dict,
    params: dict, body: object,
) -> dict:
    """Resolved-call description printed by --dry-run (no API call)."""
    uri = method.get("flatPath") or method.get("path") or ""
    base = f"{doc.get('rootUrl', '')}{doc.get('servicePath', '')}"
    return {
        "dry_run": True,
        "service": f"{api}:{version}",
        "method": method_path,
        "httpMethod": method.get("httpMethod"),
        "uri_template": f"{base}{uri}" if uri else None,
        "params": params,
        "body": body,
        "required_scopes": method.get("scopes", []),
        "destructive": is_destructive(method_path, method),
    }


def describe_node(node: dict, *, full: bool = False) -> dict:
    """Enumerate a resource node's methods + subresources (one level or full tree)."""
    methods = [
        {
            "name": name,
            "httpMethod": m.get("httpMethod"),
            "description": _short(m.get("description")),
        }
        for name, m in sorted((node.get("methods") or {}).items())
    ]
    resources = []
    for name, sub in sorted((node.get("resources") or {}).items()):
        if full:
            resources.append({"name": name, **describe_node(sub, full=True)})
        else:
            resources.append({
                "name": name,
                "methods": len(sub.get("methods") or {}),
                "resources": len(sub.get("resources") or {}),
            })
    return {"methods": methods, "resources": resources}


def resolve_schema(schemas: dict, ref: str, *, max_depth: int = 3) -> dict:
    """Expand a Discovery $ref, depth-limited and cycle-safe."""

    def expand(node: object, depth: int, seen: frozenset) -> object:
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"]
            target = schemas.get(name)
            if target is None or name in seen or depth >= max_depth:
                return {"$ref": name}
            return expand(target, depth + 1, seen | {name})
        out: dict = {}
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: expand(pv, depth, seen) for pk, pv in v.items()}
            elif k in ("items", "additionalProperties"):
                out[k] = expand(v, depth, seen)
            else:
                out[k] = v
        return out

    return expand({"$ref": ref}, 0, frozenset())  # type: ignore[return-value]


def method_schema(doc: dict, api: str, version: str, method_path: str, *, depth: int = 3) -> dict:
    """Full introspection payload for 'gw schema'."""
    method = resolve_method(doc, method_path)
    schemas = doc.get("schemas") or {}
    parameters = [
        {
            "name": name,
            "type": spec.get("type"),
            "required": bool(spec.get("required")),
            "location": spec.get("location"),
            "description": _short(spec.get("description")),
        }
        for name, spec in sorted(
            (method.get("parameters") or {}).items(),
            key=lambda kv: (not kv[1].get("required"), kv[0]),
        )
    ]

    def resolved(key: str) -> dict | None:
        ref = (method.get(key) or {}).get("$ref")
        return resolve_schema(schemas, ref, max_depth=depth) if ref else None

    return {
        "service": f"{api}:{version}",
        "method": method_path,
        "httpMethod": method.get("httpMethod"),
        "description": _short(method.get("description"), 200),
        "required_scopes": method.get("scopes", []),
        "parameters": parameters,
        "request": resolved("request"),
        "response": resolved("response"),
    }


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------

def build_dynamic_service(doc: dict, account: str | None = None):
    """Build a googleapiclient service from a discovery doc + account creds."""
    from googleapiclient.discovery import build_from_document

    import auth

    return build_from_document(doc, credentials=auth.get_credentials(account))


def build_request(service_obj, method_path: str, params: dict, body: object = None):
    """Walk the resource path on a built service and create the HttpRequest."""
    from googleapiclient.discovery import key2param

    segs = method_path.split(".")
    node = service_obj
    for seg in segs[:-1]:
        node = getattr(node, seg)()
    kwargs = {key2param(k): v for k, v in params.items()}
    if body is not None:
        kwargs["body"] = body
    return getattr(node, segs[-1])(**kwargs)


def paginate(call_page, *, page_limit: int = 10, page_delay_ms: int = 100, sleep=time.sleep):
    """Yield pages following nextPageToken. call_page(page_token) -> dict."""
    token = None
    for i in range(max(page_limit, 1)):
        page = call_page(token)
        yield page
        token = (page or {}).get("nextPageToken")
        if not token:
            return
        if page_delay_ms > 0 and i < page_limit - 1:
            sleep(page_delay_ms / 1000)


def handle_api_http_error(err, *, method: dict, method_path: str, account: str | None) -> None:
    """403-with-insufficient-scopes → actionable error; else shared handler. Always raises."""
    resp = getattr(err, "resp", None)
    status = getattr(resp, "status", 0) or 0
    if status == 403:
        try:
            body_text = (getattr(err, "content", b"") or b"").decode(errors="replace").lower()
        except Exception:
            body_text = ""
        if "insufficient" in body_text or "scope" in body_text:
            import auth

            name = auth.resolve_account(account)["name"]
            granted = (auth.load_token_data(name) or {}).get("scopes", [])
            required = method.get("scopes", [])
            raise CliError(
                f"Insufficient OAuth scopes for '{method_path}'.",
                suggestion=(
                    f"Method accepts: {', '.join(required) or 'unknown'}\n"
                    f"Token has: {', '.join(granted) or 'none'}\n"
                    f"Re-authenticate with broader scopes: gw auth login --account {name} "
                    "[--scopes gmail,calendar,sheets,docs,tasks,forms]"
                ),
                code="INSUFFICIENT_SCOPES",
            ) from err
    handle_http_error(err, context=f"api call {method_path}")

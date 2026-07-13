"""Google Workspace CLI — generic API invoker (api call/list/describe, schema)."""

from __future__ import annotations

import json
import os

import click

from output import (
    ValidationError,
    _handle_errors,
    _write_stdout,
    output_success,
)


def _parse_json_object(raw: str, flag: str) -> dict:
    try:
        value = json.loads(raw)
    except ValueError as err:
        raise ValidationError(f"--{flag} is not valid JSON: {err}") from err
    if not isinstance(value, dict):
        raise ValidationError(f"--{flag} must be a JSON object.")
    return value


def _load_body(body_json: str | None, json_file: str | None) -> object:
    if body_json and json_file:
        raise ValidationError("Use --json or --json-file, not both.")
    if body_json:
        try:
            return json.loads(body_json)
        except ValueError as err:
            raise ValidationError(f"--json is not valid JSON: {err}") from err
    if json_file:
        try:
            with open(json_file) as f:
                return json.load(f)
        except ValueError as err:
            raise ValidationError(f"--json-file is not valid JSON: {err}") from err
    return None


def _confirm_guard_off() -> bool:
    return os.environ.get("GW_API_CONFIRM", "").lower() in ("off", "0", "false", "no")


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register api commands + the top-level schema command on the CLI group."""

    @cli_group.group()
    def api():
        """Generic access to ANY Google API method via Discovery.

        SERVICE is an alias (gw api list) or api:version (e.g. admin:directory_v1).
        Destructive methods (delete/clear/remove/trash/stop/revoke or HTTP DELETE)
        require --confirm; GW_API_CONFIRM=off disables the guard.
        """
        pass

    @api.command(name="call")
    @click.argument("service")
    @click.argument("method_path")
    @click.option("--params", "params_json", default=None, help="Method parameters as a JSON object, e.g. '{\"userId\":\"me\"}'")
    @click.option("--json", "body_json", default=None, help="Request body as inline JSON")
    @click.option("--json-file", type=click.Path(exists=True, dir_okay=False), default=None, help="Request body from a JSON file")
    @click.option("--dry-run", is_flag=True, help="Resolve + validate the call and print it without executing (no auth needed)")
    @click.option("--page-all", is_flag=True, help="Follow nextPageToken; emit one JSON object per page (NDJSON)")
    @click.option("--page-limit", type=int, default=10, show_default=True, help="Max pages with --page-all")
    @click.option("--page-delay-ms", type=int, default=100, show_default=True, help="Delay between pages with --page-all")
    @click.option("--confirm", is_flag=True, help="Confirm execution of a destructive method")
    @click.option("--no-cache", is_flag=True, help="Bypass the discovery-doc cache")
    @account_option
    @compact_option
    def api_call(service, method_path, params_json, body_json, json_file,
                 dry_run, page_all, page_limit, page_delay_ms, confirm,
                 no_cache, account, compact):
        """Call METHOD_PATH (dotted Discovery path) on SERVICE.

        Example: gw api call gmail users.messages.list --params '{"userId":"me","maxResults":5}'
        """
        import discovery
        from googleapiclient.errors import HttpError

        with _handle_errors(compact):
            gapi, version = discovery.resolve_service(service)
            params = _parse_json_object(params_json, "params") if params_json else {}
            body = _load_body(body_json, json_file)
            doc = discovery.get_discovery_doc(gapi, version, no_cache=no_cache)
            method = discovery.resolve_method(doc, method_path)
            discovery.validate_params(doc, method, params)

            if dry_run:
                output_success(
                    discovery.dry_run_payload(doc, gapi, version, method_path, method, params, body),
                    compact=compact,
                )
                return

            if discovery.is_destructive(method_path, method) and not confirm and not _confirm_guard_off():
                raise discovery.ConfirmationRequiredError(method_path)

            service_obj = discovery.build_dynamic_service(doc, account)
            try:
                if page_all:
                    def call_page(token):
                        page_params = dict(params)
                        if token:
                            page_params["pageToken"] = token
                        return discovery.build_request(
                            service_obj, method_path, page_params, body
                        ).execute()

                    for page in discovery.paginate(
                        call_page, page_limit=page_limit, page_delay_ms=page_delay_ms
                    ):
                        _write_stdout(json.dumps(page, separators=(",", ":"), default=str))
                else:
                    response = discovery.build_request(
                        service_obj, method_path, params, body
                    ).execute()
                    output_success(response if response else {"status": "ok"}, compact=compact)
            except HttpError as err:
                discovery.handle_api_http_error(
                    err, method=method, method_path=method_path, account=account
                )

    @api.command(name="list")
    @compact_option
    def api_list(compact):
        """List service aliases and the api:version escape hatch."""
        import discovery

        with _handle_errors(compact):
            output_success({
                "services": [
                    {"alias": alias, "api": gapi, "version": version}
                    for alias, (gapi, version) in sorted(discovery.SERVICE_ALIASES.items())
                ],
                "escape_hatch": (
                    "Any Google API works as SERVICE = api:version, "
                    "e.g. gw api call admin:directory_v1 users.list --params '{\"customer\":\"my_customer\"}'"
                ),
            }, compact=compact)

    @api.command()
    @click.argument("service")
    @click.argument("resource_path", required=False)
    @click.option("--full", is_flag=True, help="Recurse into the full resource tree")
    @click.option("--no-cache", is_flag=True, help="Bypass the discovery-doc cache")
    @compact_option
    def describe(service, resource_path, full, no_cache, compact):
        """Enumerate resources and methods of SERVICE (optionally under RESOURCE_PATH)."""
        import discovery

        with _handle_errors(compact):
            gapi, version = discovery.resolve_service(service)
            doc = discovery.get_discovery_doc(gapi, version, no_cache=no_cache)
            node = discovery.resolve_resource(doc, resource_path) if resource_path else doc
            output_success({
                "service": f"{gapi}:{version}",
                "title": doc.get("title"),
                "resource": resource_path or None,
                **discovery.describe_node(node, full=full),
            }, compact=compact)

    @cli_group.command()
    @click.argument("spec")
    @click.option("--depth", type=int, default=3, show_default=True, help="Max $ref expansion depth")
    @click.option("--no-cache", is_flag=True, help="Bypass the discovery-doc cache")
    @compact_option
    def schema(spec, depth, no_cache, compact):
        """Show parameters, scopes, and request/response schema for a method.

        SPEC is SERVICE.RESOURCE.METHOD, e.g. gmail.users.messages.list
        (escape hatch works too: admin:directory_v1.users.list).
        """
        import discovery

        with _handle_errors(compact):
            service, _, method_path = spec.partition(".")
            if not method_path:
                raise ValidationError(
                    f"Invalid spec '{spec}'.",
                    suggestion="Format: SERVICE.RESOURCE.METHOD, e.g. gmail.users.messages.list",
                )
            gapi, version = discovery.resolve_service(service)
            doc = discovery.get_discovery_doc(gapi, version, no_cache=no_cache)
            output_success(
                discovery.method_schema(doc, gapi, version, method_path, depth=depth),
                compact=compact,
            )

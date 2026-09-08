---
name: gw-api
description: >
  Call ANY Google API method via the generic Discovery invoker — services with no
  curated command group (Drive, Slides, People, Chat, Keep, Meet, Classroom,
  Apps Script, Admin SDK, ...) and uncovered methods on curated ones. Includes
  schema introspection, dry-run validation, NDJSON pagination, and a destructive-method guard.
  TRIGGERS: "google api", "gw api", "api call", "discovery", "drive api", "slides",
  "people api", "contacts", "chat api", "admin sdk", "apps script", "classroom",
  "any google service", "api schema"
---

# Generic Google API Invoker

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## When to Use

- **Prefer the curated groups** (`gmail`, `calendar`, `sheets`, `docs`, `tasks`, `forms`) for anything they cover — they have task-shaped flags and validated response shapes.
- **Use `gw api`** for everything else: services without a curated group, or curated services' methods the groups don't expose.
- SERVICE is an alias (`gw api list` shows all 18) or the `api:version` escape hatch for any Google API, e.g. `admin:directory_v1`.

## Workflow: schema → dry-run → call

Always resolve the method shape before calling:

```bash
# 1. Find the service and browse its surface
$CLI api list --compact
$CLI api describe gmail --compact                    # top-level resources
$CLI api describe gmail users.messages --compact     # methods on a resource

# 2. Inspect the method: params, required scopes, request/response schema
$CLI schema gmail.users.messages.list --compact

# 3. Validate the call without executing (offline, no auth needed)
$CLI api call gmail users.messages.list --params '{"userId":"me","maxResults":5}' --dry-run

# 4. Execute
$CLI api call gmail users.messages.list --params '{"userId":"me","maxResults":5}' --compact
```

`--dry-run` prints `{service, method, httpMethod, uri_template, params, body, required_scopes, destructive}` and catches unknown/missing-required params before any network call.

## The discovery document is the contract, not the published examples

A vendor's documentation shows what usually works. The discovery document defines what is
guaranteed. When they disagree, the published example is the one that can change.

This matters most for **enums**, which are the cheapest thing to get wrong and the hardest to
debug, because a rejected enum reports as a generic 400 rather than as "that value is not in the
list". Fetch the document and read the enum yourself:

```bash
# Any Google API, no auth, no gw needed
curl -s "https://<api>.googleapis.com/\$discovery/rest?version=<version>" -o /tmp/d.json

# The enum for one request field
python3 -c "import json;d=json.load(open('/tmp/d.json'));print(d['schemas']['<RequestSchema>']['properties']['<field>']['enum'])"
```

Two things this catches that reading the docs does not:

**Casing.** Published examples routinely use lower camel case for values the discovery document
spells in upper snake case. The API often accepts both, but only one of those is promised. Send
the documented enum spelling and accept either from your own callers.

**Narrower sub-enums.** The same concept can have a wider enum in one field than another. A
dimension you may group by is not necessarily a dimension you may filter on, and the two enums
sit in different schemas. Comparing them is how you find that out before a user does.

`$CLI schema <service>.<resource>.<method>` surfaces the same material through gw. Pass
`--depth 0 --compact` for parameters and required scopes at a fraction of the size; the default
depth expands referenced schemas and can return tens of kilobytes for one method.

## A validator answers the question it was given

Before trusting any `check`, `validate` or `compatibility` endpoint, read its request schema for
a filter field. Several of them accept a parameter that narrows the response to one class of
result, and passing the value you are hoping for makes the endpoint incapable of telling you
otherwise. The verdict then describes what survived the filter rather than what you asked about.

The tell is response size. An answer about the handful of fields you named should be small. If a
validator returns tens of kilobytes, it is answering a wider question than you asked, and the
part you care about is buried in it.

## Commands

```bash
$CLI api call SERVICE METHOD_PATH [--params '{json}'] [--json '{body}' | --json-file PATH] \
    [--dry-run] [--page-all] [--page-limit 10] [--page-delay-ms 100] [--confirm] \
    [--no-cache] [--account NAME] --compact
$CLI api list --compact
$CLI api describe SERVICE [RESOURCE.PATH] [--full] --compact
$CLI schema SERVICE.RESOURCE.METHOD [--depth 3] --compact
```

- `METHOD_PATH` is the dotted resource path from the Discovery doc (`users.messages.list`).
- `--params` = method (query/path) parameters; `--json` / `--json-file` = request body.

## Pagination

`--page-all` follows `nextPageToken` and emits **NDJSON** — one JSON object per page on stdout. Bound it with `--page-limit` (default 10 pages):

```bash
$CLI api call drive files.list --params '{"pageSize":100,"q":"trashed=false"}' --page-all --page-limit 5
```

Without `--page-all` you get a single call with the normal JSON envelope.

## Destructive-Method Guard

Methods ending in `delete`/`clear`/`remove`/`trash`/`stop`/`revoke` (or with HTTP DELETE) refuse to run without `--confirm` (error code `CONFIRM_REQUIRED`).

**Get explicit user confirmation before adding `--confirm` — never self-confirm a destructive call.** Use `--dry-run` first to show the user exactly what would execute. `GW_API_CONFIRM=off` disables the guard (power users only).

```bash
# Show the user what would happen, then (after they approve):
$CLI api call gmail users.messages.trash --params '{"userId":"me","id":"MSG_ID"}' --dry-run
$CLI api call gmail users.messages.trash --params '{"userId":"me","id":"MSG_ID"}' --confirm
```

## Examples

```bash
# Drive: search files (no curated drive group needed)
$CLI api call drive files.list --params '{"q":"name contains \"report\"","pageSize":10}' --compact

# People: list contacts
$CLI api call people people.connections.list --params '{"resourceName":"people/me","personFields":"names,emailAddresses"}' --compact

# Escape hatch — Admin SDK directory
$CLI api call admin:directory_v1 users.list --params '{"customer":"my_customer"}' --compact

# Create via request body
$CLI api call drive files.create --json '{"name":"Notes","mimeType":"application/vnd.google-apps.folder"}' --compact
```

## Notes

- Discovery docs are cached 24h under the gw config dir (`cache/discovery/`); `--no-cache` forces a refetch. Offline runs fall back to the cache with a stderr note.
- On 403 insufficient-scope errors the CLI prints the method's required scopes vs the token's scopes and suggests `gw auth login`.
- `--account NAME` selects the account (see `gw auth list`); default per config.
- Empty API responses (e.g. after a delete) print `{"status":"ok"}`.

---
name: gw-analytics
description: >
  Read Google Analytics 4 via CLI — traffic, acquisition, pages, conversions, realtime.
  TRIGGERS: "google analytics", "GA4", "analytics report", "how much traffic", "how many
  visitors", "sessions", "active users", "pageviews", "traffic sources", "acquisition
  channels", "top pages", "landing pages", "conversion events", "key events", "realtime
  users", "who is on the site now", "GA4 property", "GA4 dimension", "GA4 metric"
---

# Google Analytics 4

Read-only. Nothing here writes to a property.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Scope prerequisite

GA4 is an opt-in scope, so a default `gw` token does not carry it. Add it once:

```bash
$CLI auth login --add-scopes analytics
```

`--add-scopes` unions with the scopes the account already has. Do not use `--scopes analytics`
here: a login replaces the token, so that form would drop Gmail, Calendar, Drive, Sheets, Docs,
Tasks and Forms. The scope granted is `analytics.readonly`.

## Commands

```bash
$CLI analytics properties --compact

$CLI analytics report --property PROPERTY_ID \
  [--metrics sessions,activeUsers] [--dimensions date,sessionDefaultChannelGroup] \
  [--since 28daysAgo] [--until yesterday] [--limit 100] [--offset 0] \
  [--order-by -sessions] [--filter hostname==example.com] [--totals] [--keep-empty-rows]

$CLI analytics realtime --property PROPERTY_ID [--metrics activeUsers] [--dimensions country]

$CLI analytics metadata --property PROPERTY_ID [--kind all|dimensions|metrics] [--grep TERM] [--full]

$CLI analytics check --property PROPERTY_ID [--metrics sessions] [--dimensions pagePath] [--suggest]
```

`--property` takes the numeric property id. The three ids that sit next to it in the GA4 UI are
each rejected by name rather than passed on to a confusing API error: `G-XXXXXXXXXX` is the
measurement id (the web tag), `GTM-XXXXXXX` is a Tag Manager container, and `UA-XXXXX-Y` is a
Universal Analytics property, which stopped collecting data in 2023 and is not reachable
through the GA4 API at all.

Dates accept `YYYY-MM-DD`, `today`, `yesterday` or `NdaysAgo`.

`--filter` is repeatable and clauses are ANDed: `dim==value`, `dim!=value`, `dim=~regex`,
`dim=@substring`.

`--order-by` takes a metric or a dimension you actually requested; prefix with `-` for
descending. Ordering by a field you did not request is rejected by name.

## Never guess a field name

A wrong dimension name is the most common way a GA4 call fails, and the failure is an opaque
400. Two cheap commands settle it before spending a call:

```bash
$CLI analytics metadata --property PROPERTY_ID --kind dimensions --grep channel --compact
$CLI analytics check --property PROPERTY_ID --metrics sessions --dimensions sessionDefaultChannelGroup
```

`sessionDefaultChannelGroup` is correct. `sessionDefaultChannelGrouping` does not exist; it is
widely copied from third-party skill packs and fails every time.

`check` answers about the fields **you named** and nothing else. The API itself judges every
field in the property against your request — around 490 of them, ~30KB — and a verdict drawn
from that is about fields you never asked for. Add `--suggest` when you do want that list,
which is the honest question it answers: *what else could I add to this request?*

## Response shapes (validated)

`properties`: `{"properties":[{"property_id","display_name","account","property_type"}],"count"}`
and, if a very large org pages past the ceiling, `"truncated":true` with a `"note"`.

`report` and `realtime` return flattened records, not the GA4 wire format:

```json
{"rows":[{"date":"20260901","sessionDefaultChannelGroup":"Organic Search","sessions":42}],
 "row_count":2,"returned":2,"dimensions":["date"],"metrics":["sessions"],
 "property":"properties/PROPERTY_ID","window":{"since":"7daysAgo","until":"yesterday"}}
```

Metric values are converted to numbers using the type the API declares, so rows sort and sum
without reparsing. Present when applicable: `totals`, `property_quota`, and these two, which
mean different things and are reported separately:

| Field | Meaning |
|---|---|
| `data_loss_from_other_row` | A high cardinality `(other)` row absorbed the tail, so the breakdown is incomplete. Re-run narrower, or use the BigQuery export. |
| `sampled` with `sampling` | The response was sampled. This is the only sampling signal. |

`metadata`: `{"property","dimensions":[...],"metrics":[...]}` — names only unless `--full`.

`check`: `{"property","dimensions":[{"name","compatibility"}],"metrics":[...],"compatible","incompatible":[...]}`
— one entry per field you named. `compatibility` is `COMPATIBLE`, `INCOMPATIBLE`, or
`UNKNOWN_FIELD` when the property does not carry that name at all. With `--suggest`, adds
`"could_add":{"dimensions":[...],"metrics":[...]}`.

A name that is not a GA4 field at all fails earlier, at the API, and the error carries a
correction: `Did you mean browserVersion? Field zzz is not a valid dimension.`

## Cheapest output for an agent

`--output csv` collapses a report to a header and rows, which is far smaller than the JSON
and is usually all an agent needs to answer a question:

```bash
$CLI --output csv analytics report --property PROPERTY_ID \
  --metrics sessions --dimensions date --since 7daysAgo
```

`--output` and `--fields` are **group-level** flags: they go **before** the subcommand.
`--compact` and `--account` go after it. `--fields rows` keeps only the rows key.

## Limits

GA4 accepts at most 9 dimensions per request, enforced before the call.

## Anything not covered here

`gw-api` reaches the rest of the Data API and the Admin API, including batch and pivot reports:

```bash
$CLI api call analyticsdata:v1beta properties.runReport \
  --params '{"property":"properties/PROPERTY_ID"}' --json '{...}' --dry-run --compact
```

`--dry-run` needs no auth, validates the call and prints `required_scopes`.

---
name: gw-searchconsole
description: >
  Read Google Search Console via CLI — search queries, pages, CTR, position, indexing, sitemaps.
  TRIGGERS: "search console", "GSC", "what are we ranking for", "search queries", "organic
  traffic", "impressions", "clicks", "CTR", "average position", "keyword", "is this page
  indexed", "index coverage", "URL inspection", "sitemap", "SEO performance", "search
  performance", "brand vs non-brand"
---

# Google Search Console

Read-only by construction. `sites.add`, `sites.delete` and the sitemap write methods are not
exposed, and a test asserts they stay unreachable.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Scope prerequisite

Search Console is an opt-in scope, so a default `gw` token does not carry it. Add it once:

```bash
$CLI auth login --add-scopes searchconsole
```

`--add-scopes` unions with the scopes the account already has. Do not use
`--scopes searchconsole`: a login replaces the token, so that form would drop Gmail, Calendar,
Drive, Sheets, Docs, Tasks and Forms. The scope granted is `webmasters.readonly`.

## Commands

```bash
$CLI searchconsole sites --compact

$CLI searchconsole query --site SITE \
  [--dimensions query] [--since 28daysAgo] [--until 3daysAgo] \
  [--limit 25] [--start-row 0] [--type web] [--data-state final] \
  [--filter "page~~/blog"]

$CLI searchconsole sitemaps --site SITE
$CLI searchconsole inspect --site SITE --url URL
```

## Resolve the property first

`sc-domain:example.com` and `https://example.com/` are different properties holding different
data. Run `searchconsole sites` and use the exact string it returns; a guess fails or, worse,
answers about the wrong property.

## Flags

| Flag | Values |
|---|---|
| `--dimensions` | `query`, `page`, `country`, `device`, `searchAppearance`, `date`, `hour` |
| `--since` / `--until` | `YYYY-MM-DD`, `today`, `yesterday`, `NdaysAgo` |
| `--limit` | up to 25000; page beyond that with `--start-row` |
| `--type` | `web`, `image`, `video`, `news`, `discover`, `googleNews` |
| `--data-state` | `final`, `all`, `hourlyAll` |
| `--filter` | repeatable and ANDed: `dim==value`, `dim!=value`, `dim~~substring`, `dim=~regex` |

Filters accept `query`, `page`, `country`, `device` and `searchAppearance` only. `date` and
`hour` can be grouped by but not filtered on; narrow the window with `--since` and `--until`
instead. Grouping by `hour` requires `--data-state hourlyAll`.

Values are normalised to the spelling the v1 discovery document defines, so either case works
on input.

## The window

`--until` defaults to `3daysAgo` because the data lags. Asking for yesterday reliably returns
less than the real figure and reads as a drop. Quote the `window` echoed back in the response
rather than the window you asked for.

## Cheapest output for an agent

`--output csv` collapses the rows to a header and lines, far smaller than the JSON:

```bash
$CLI --output csv searchconsole query --site SITE --dimensions query --limit 25
```

`--output` and `--fields` are **group-level** flags: they go **before** the subcommand.
`--compact` and `--account` go after it.

## Response shapes (validated)

`sites`: `{"sites":[{"site_url","permission_level"}],"count"}`

`query` returns named records, not positional keys:

```json
{"site":"sc-domain:example.com","window":{"since":"2026-08-01","until":"2026-08-31"},
 "dimensions":["query"],
 "rows":[{"query":"pricing","clicks":3,"impressions":40,"ctr":0.075,"position":8.2}],
 "returned":1,"aggregation_type":"byProperty","note":null}
```

When grouped by `query`, `note` warns that rows below Google's privacy threshold are withheld,
so the column does not sum to the site total. For the total, pass `--dimensions ''`.

`sitemaps`: `{"site","sitemaps":[{"path","last_submitted","last_downloaded","is_pending","errors","warnings","contents"}],"count"}`

`inspect`: `{"site","url","verdict","coverage_state","robots_txt_state","indexing_state","page_fetch_state","last_crawl_time","google_canonical","user_canonical","sitemaps","mobile_usability_verdict","rich_results_verdict","inspection_link"}`

## Anything not covered here

`gw-api` reaches the rest of the API. It can also reach the write methods, which it gates
behind `--confirm`; dry-run first and never pass `--confirm` to a call you have not inspected.

```bash
$CLI api call searchconsole:v1 searchanalytics.query \
  --params '{"siteUrl":"SITE"}' --json '{...}' --dry-run --compact
```

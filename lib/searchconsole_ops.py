"""Google Search Console operations — sites, search analytics, sitemaps, URL inspection.

Read-only. The write half of the API (sites.add, sites.delete, sitemaps.submit,
sitemaps.delete) is deliberately not exposed: nothing here should be able to
add or remove a property, and a mis-scoped agent submitting or deleting a
sitemap is a real cost with no matching benefit.

Search Console rows arrive as {"keys": [...], "clicks": n, ...} where the keys
are positional and the caller has to remember which dimension it asked for in
which order. Every response here is flattened so each key is named.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from auth import build_service
from googleapiclient.errors import HttpError
from output import ValidationError, handle_http_error

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE = re.compile(r"^(today|yesterday|(\d+)daysAgo)$")

# dimension~~substring / dimension==value / dimension=~regex
_FILTER = re.compile(r"^([A-Za-z0-9_]+)(==|!=|~~|=~)(.*)$")

_OPERATORS = {
    "==": "equals",
    "!=": "notEquals",
    "~~": "contains",
    "=~": "includingRegex",
}

VALID_DIMENSIONS = ("query", "page", "country", "device", "searchAppearance", "date")


def _service(account: str | None = None):
    return build_service("searchconsole", "v1", account)


def _split(csv: str | None) -> list[str]:
    return [s.strip() for s in (csv or "").split(",") if s.strip()]


def _resolve_date(value: str, *, field: str, today: date | None = None) -> str:
    """Search Console takes YYYY-MM-DD only, so relative forms resolve here."""
    v = str(value).strip()
    if _ISO_DATE.match(v):
        return v
    m = _RELATIVE.match(v)
    if m:
        base = today or date.today()
        if v == "today":
            return base.isoformat()
        if v == "yesterday":
            return (base - timedelta(days=1)).isoformat()
        return (base - timedelta(days=int(m.group(2)))).isoformat()
    raise ValidationError(
        f"Invalid {field}: {value!r}.",
        suggestion="Use YYYY-MM-DD, or today / yesterday / NdaysAgo.",
    )


def _build_filters(expressions: tuple[str, ...] | list[str]) -> list[dict]:
    groups = []
    for raw in expressions or []:
        m = _FILTER.match(raw.strip())
        if not m:
            raise ValidationError(
                f"Invalid filter: {raw!r}.",
                suggestion="Use dimension==value, dimension!=value, "
                "dimension~~substring or dimension=~regex. "
                "Repeat --filter to AND several together.",
            )
        dimension, op, value = m.group(1), m.group(2), m.group(3)
        groups.append(
            {
                "filters": [
                    {
                        "dimension": dimension,
                        "operator": _OPERATORS[op],
                        "expression": value,
                    }
                ]
            }
        )
    return groups


def flatten_rows(response: dict, dimensions: list[str]) -> list[dict]:
    """Name each positional key and keep the metrics as numbers."""
    rows = []
    for row in response.get("rows", []) or []:
        rec: dict = {}
        for name, value in zip(dimensions, row.get("keys", []) or []):
            rec[name] = value
        rec["clicks"] = row.get("clicks", 0)
        rec["impressions"] = row.get("impressions", 0)
        rec["ctr"] = row.get("ctr", 0.0)
        rec["position"] = row.get("position", 0.0)
        rows.append(rec)
    return rows


def list_sites(account: str | None = None) -> dict:
    """Every property this token can reach.

    The exact string matters: sc-domain:example.com and https://example.com/
    are different properties with different data. Use what this returns.
    """
    try:
        result = _service(account).sites().list().execute()
    except HttpError as e:
        handle_http_error(e, "searchconsole list sites")
        raise  # unreachable

    sites = [
        {"site_url": s.get("siteUrl"), "permission_level": s.get("permissionLevel")}
        for s in result.get("siteEntry", []) or []
    ]
    return {"sites": sites, "count": len(sites)}


def query(
    site: str,
    dimensions: str = "query",
    since: str = "28daysAgo",
    until: str = "3daysAgo",
    limit: int = 25,
    start_row: int = 0,
    search_type: str = "web",
    data_state: str = "final",
    filters: tuple[str, ...] = (),
    account: str | None = None,
) -> dict:
    """Search analytics rows, flattened.

    The default window ends 3 days back because Search Console lags 2-3 days;
    asking for yesterday reliably returns less than the real figure.
    """
    dimension_names = _split(dimensions)
    unknown = [d for d in dimension_names if d not in VALID_DIMENSIONS]
    if unknown:
        raise ValidationError(
            f"Unknown dimension(s): {', '.join(unknown)}.",
            suggestion=f"Valid: {', '.join(VALID_DIMENSIONS)}",
        )
    if int(limit) > 25000:
        raise ValidationError(
            "rowLimit caps at 25000.",
            suggestion="Page with --start-row instead of raising the limit.",
        )

    body: dict = {
        "startDate": _resolve_date(since, field="since"),
        "endDate": _resolve_date(until, field="until"),
        "dimensions": dimension_names,
        "rowLimit": int(limit),
        "startRow": int(start_row),
        "type": search_type,
        "dataState": data_state,
    }
    filter_groups = _build_filters(filters)
    if filter_groups:
        body["dimensionFilterGroups"] = filter_groups

    try:
        response = (
            _service(account).searchanalytics().query(siteUrl=site, body=body).execute()
        )
    except HttpError as e:
        handle_http_error(e, "searchconsole query")
        raise  # unreachable

    rows = flatten_rows(response, dimension_names)
    return {
        "site": site,
        "window": {"since": body["startDate"], "until": body["endDate"]},
        "dimensions": dimension_names,
        "rows": rows,
        "returned": len(rows),
        "aggregation_type": response.get("responseAggregationType"),
        # Totals from a query dimension never reconcile to the site total:
        # rows below a privacy threshold are withheld. Say so rather than
        # letting a caller sum the column and believe it.
        "note": (
            "Rows below Google's privacy threshold are withheld, so these rows "
            "do not sum to the site total. Query without --dimensions for a total."
        )
        if "query" in dimension_names
        else None,
    }


def list_sitemaps(site: str, account: str | None = None) -> dict:
    """Sitemaps registered for a property, with what Search Console made of them."""
    try:
        result = _service(account).sitemaps().list(siteUrl=site).execute()
    except HttpError as e:
        handle_http_error(e, "searchconsole list sitemaps")
        raise  # unreachable

    sitemaps = []
    for s in result.get("sitemap", []) or []:
        sitemaps.append(
            {
                "path": s.get("path"),
                "last_submitted": s.get("lastSubmitted"),
                "last_downloaded": s.get("lastDownloaded"),
                "is_pending": s.get("isPending"),
                "errors": s.get("errors"),
                "warnings": s.get("warnings"),
                "contents": s.get("contents"),
            }
        )
    return {"site": site, "sitemaps": sitemaps, "count": len(sitemaps)}


def inspect_url(site: str, url: str, account: str | None = None) -> dict:
    """Index status for one URL, reduced to the fields worth reading."""
    body = {"siteUrl": site, "inspectionUrl": url}
    try:
        response = (
            _service(account).urlInspection().index().inspect(body=body).execute()
        )
    except HttpError as e:
        handle_http_error(e, "searchconsole inspect")
        raise  # unreachable

    result = response.get("inspectionResult", {}) or {}
    index_status = result.get("indexStatusResult", {}) or {}
    return {
        "site": site,
        "url": url,
        "verdict": index_status.get("verdict"),
        "coverage_state": index_status.get("coverageState"),
        "robots_txt_state": index_status.get("robotsTxtState"),
        "indexing_state": index_status.get("indexingState"),
        "page_fetch_state": index_status.get("pageFetchState"),
        "last_crawl_time": index_status.get("lastCrawlTime"),
        "google_canonical": index_status.get("googleCanonical"),
        "user_canonical": index_status.get("userCanonical"),
        "sitemaps": index_status.get("sitemap"),
        "mobile_usability_verdict": (result.get("mobileUsabilityResult") or {}).get("verdict"),
        "rich_results_verdict": (result.get("richResultsResult") or {}).get("verdict"),
        "inspection_link": result.get("inspectionResultLink"),
    }

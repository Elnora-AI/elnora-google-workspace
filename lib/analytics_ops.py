"""Google Analytics 4 operations — properties, reports, realtime, metadata.

Reads the GA4 Data API (analyticsdata v1beta) and the Admin API
(analyticsadmin v1beta). Read-only: nothing here writes to a property.

The GA4 wire format is verbose — dimensionHeaders, metricHeaders and rows of
{dimensionValues: [{value}], metricValues: [{value}]}. Every response here is
flattened to plain records, which is both easier to read and far cheaper for an
agent to hold in context. Metric values are converted to numbers using the type
the API declares, so a caller can sort and sum without reparsing strings.
"""

from __future__ import annotations

import re

from auth import build_service
from googleapiclient.errors import HttpError
from output import ValidationError, handle_http_error

# Relative forms the Data API accepts verbatim in a dateRange.
_GA4_RELATIVE = re.compile(r"^(today|yesterday|\d+daysAgo)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# dim==value / dim!=value / dim=~regex / dim=@substring
_FILTER = re.compile(r"^([A-Za-z0-9_]+)(==|!=|=~|=@)(.*)$")

_INT_TYPES = {"TYPE_INTEGER"}
_FLOAT_TYPES = {
    "TYPE_FLOAT",
    "TYPE_SECONDS",
    "TYPE_MILLISECONDS",
    "TYPE_MINUTES",
    "TYPE_HOURS",
    "TYPE_STANDARD",
    "TYPE_CURRENCY",
    "TYPE_FEET",
    "TYPE_MILES",
    "TYPE_METERS",
    "TYPE_KILOMETERS",
}


def _data(account: str | None = None):
    return build_service("analyticsdata", "v1beta", account)


def _admin(account: str | None = None):
    return build_service("analyticsadmin", "v1beta", account)


def _property_path(property_id: str) -> str:
    """Accept 123456, properties/123456 or a full resource name."""
    pid = str(property_id).strip()
    if pid.startswith("properties/"):
        pid = pid.split("/", 1)[1]
    if not pid.isdigit():
        raise ValidationError(
            f"Invalid GA4 property id: {property_id!r}.",
            suggestion="Use the numeric property id, e.g. 123456789. "
            "List the ones you can reach with: gw analytics properties",
        )
    return f"properties/{pid}"


def _date(value: str, *, field: str) -> str:
    v = str(value).strip()
    if _GA4_RELATIVE.match(v) or _ISO_DATE.match(v):
        return v
    raise ValidationError(
        f"Invalid {field}: {value!r}.",
        suggestion="Use YYYY-MM-DD, or a relative form the Data API accepts: "
        "today, yesterday, NdaysAgo (e.g. 28daysAgo).",
    )


def _split(csv: str | None) -> list[str]:
    return [s.strip() for s in (csv or "").split(",") if s.strip()]


def _coerce(value: str, type_name: str):
    """Turn a metric string into a number when the API says it is one."""
    if type_name in _INT_TYPES:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if type_name in _FLOAT_TYPES:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _build_filter(expressions: tuple[str, ...] | list[str]) -> dict | None:
    """Build a FilterExpression from simple dim==value style strings."""
    clauses = []
    for raw in expressions or []:
        m = _FILTER.match(raw.strip())
        if not m:
            raise ValidationError(
                f"Invalid filter: {raw!r}.",
                suggestion="Use dimension==value, dimension!=value, "
                "dimension=~regex or dimension=@substring. "
                "Repeat --filter to AND several together.",
            )
        name, op, value = m.group(1), m.group(2), m.group(3)
        if op == "==":
            string_filter = {"matchType": "EXACT", "value": value}
        elif op == "!=":
            string_filter = {"matchType": "EXACT", "value": value}
        elif op == "=~":
            string_filter = {"matchType": "FULL_REGEXP", "value": value}
        else:
            string_filter = {"matchType": "CONTAINS", "value": value}
        clause: dict = {"filter": {"fieldName": name, "stringFilter": string_filter}}
        if op == "!=":
            clause = {"notExpression": clause}
        clauses.append(clause)
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"andGroup": {"expressions": clauses}}


def _order_bys(order_by: str | None) -> list[dict] | None:
    """'-sessions' -> descending metric; 'date' -> ascending dimension."""
    if not order_by:
        return None
    out = []
    for raw in _split(order_by):
        desc = raw.startswith("-")
        name = raw[1:] if desc else raw
        # A name that is also a requested metric orders by metric; the API
        # rejects the wrong one clearly, so no guessing is hidden here.
        out.append({"desc": desc, "metric": {"metricName": name}} if name else {})
    return out or None


def flatten_report(response: dict) -> dict:
    """Flatten a runReport / runRealtimeReport response into plain records."""
    dim_names = [h.get("name") for h in response.get("dimensionHeaders", [])]
    met_headers = response.get("metricHeaders", [])
    met_names = [h.get("name") for h in met_headers]
    met_types = [h.get("type") for h in met_headers]

    rows = []
    for row in response.get("rows", []) or []:
        rec: dict = {}
        for name, cell in zip(dim_names, row.get("dimensionValues", [])):
            rec[name] = cell.get("value")
        for name, type_name, cell in zip(met_names, met_types, row.get("metricValues", [])):
            rec[name] = _coerce(cell.get("value"), type_name)
        rows.append(rec)

    out: dict = {
        "rows": rows,
        "row_count": response.get("rowCount", len(rows)),
        "returned": len(rows),
        "dimensions": dim_names,
        "metrics": met_names,
    }

    totals = response.get("totals") or []
    if totals:
        first = totals[0].get("metricValues", [])
        out["totals"] = {
            name: _coerce(cell.get("value"), type_name)
            for name, type_name, cell in zip(met_names, met_types, first)
        }

    meta = response.get("metadata") or {}
    # These two are routinely confused. dataLossFromOtherRow means a high
    # cardinality "(other)" row absorbed the tail; samplingMetadatas is the
    # only signal that the response was sampled.
    if meta.get("dataLossFromOtherRow"):
        out["data_loss_from_other_row"] = True
    sampling = meta.get("samplingMetadatas")
    if sampling:
        out["sampled"] = True
        out["sampling"] = sampling
    quota = response.get("propertyQuota")
    if quota:
        out["property_quota"] = quota
    return out


def list_properties(account: str | None = None) -> dict:
    """Every GA4 property this token can reach, flattened to id + name."""
    try:
        result = _admin(account).accountSummaries().list(pageSize=200).execute()
    except HttpError as e:
        handle_http_error(e, "analytics list properties")
        raise  # unreachable

    properties = []
    for summary in result.get("accountSummaries", []) or []:
        for prop in summary.get("propertySummaries", []) or []:
            properties.append(
                {
                    "property_id": (prop.get("property") or "").split("/")[-1],
                    "display_name": prop.get("displayName"),
                    "account": summary.get("displayName"),
                    "property_type": prop.get("propertyType"),
                }
            )
    return {"properties": properties, "count": len(properties)}


def report(
    property_id: str,
    metrics: str = "sessions",
    dimensions: str | None = None,
    since: str = "28daysAgo",
    until: str = "yesterday",
    limit: int = 100,
    offset: int = 0,
    order_by: str | None = None,
    filters: tuple[str, ...] = (),
    totals: bool = False,
    keep_empty_rows: bool = False,
    account: str | None = None,
) -> dict:
    """Run a GA4 report and return flattened rows."""
    metric_names = _split(metrics)
    if not metric_names:
        raise ValidationError(
            "At least one metric is required.",
            suggestion="e.g. --metrics sessions,activeUsers. "
            "Check a name with: gw analytics metadata --property <id> --grep session",
        )
    dimension_names = _split(dimensions)
    if len(dimension_names) > 9:
        raise ValidationError(
            f"GA4 accepts at most 9 dimensions per request; {len(dimension_names)} given.",
            suggestion="Drop dimensions, or run several narrower reports.",
        )

    body: dict = {
        "dateRanges": [
            {"startDate": _date(since, field="since"), "endDate": _date(until, field="until")}
        ],
        "metrics": [{"name": n} for n in metric_names],
        "limit": int(limit),
        "offset": int(offset),
        "keepEmptyRows": bool(keep_empty_rows),
    }
    if dimension_names:
        body["dimensions"] = [{"name": n} for n in dimension_names]
    filter_expr = _build_filter(filters)
    if filter_expr:
        body["dimensionFilter"] = filter_expr
    orders = _order_bys(order_by)
    if orders:
        body["orderBys"] = orders
    if totals:
        body["metricAggregations"] = ["TOTAL"]

    prop = _property_path(property_id)
    try:
        response = _data(account).properties().runReport(property=prop, body=body).execute()
    except HttpError as e:
        handle_http_error(e, "analytics report")
        raise  # unreachable

    out = flatten_report(response)
    out["property"] = prop
    out["window"] = {"since": body["dateRanges"][0]["startDate"], "until": body["dateRanges"][0]["endDate"]}
    return out


def realtime(
    property_id: str,
    metrics: str = "activeUsers",
    dimensions: str | None = None,
    limit: int = 50,
    account: str | None = None,
) -> dict:
    """Active users in the last 30 minutes. A different surface from report()."""
    metric_names = _split(metrics)
    if not metric_names:
        raise ValidationError("At least one metric is required.")
    body: dict = {"metrics": [{"name": n} for n in metric_names], "limit": int(limit)}
    dimension_names = _split(dimensions)
    if dimension_names:
        body["dimensions"] = [{"name": n} for n in dimension_names]

    prop = _property_path(property_id)
    try:
        response = (
            _data(account).properties().runRealtimeReport(property=prop, body=body).execute()
        )
    except HttpError as e:
        handle_http_error(e, "analytics realtime")
        raise  # unreachable

    out = flatten_report(response)
    out["property"] = prop
    out["window"] = "last 30 minutes"
    return out


def metadata(
    property_id: str,
    kind: str = "all",
    grep: str | None = None,
    full: bool = False,
    account: str | None = None,
) -> dict:
    """The dimension and metric names a property actually accepts.

    Names only by default. This is the cheap way to settle whether a field
    exists instead of guessing at one and reading a 400 back.
    """
    prop = _property_path(property_id)
    try:
        response = (
            _data(account).properties().getMetadata(name=f"{prop}/metadata").execute()
        )
    except HttpError as e:
        handle_http_error(e, "analytics metadata")
        raise  # unreachable

    def pick(items):
        out = []
        for item in items or []:
            api_name = item.get("apiName")
            if grep and grep.lower() not in (api_name or "").lower() and grep.lower() not in (
                item.get("uiName") or ""
            ).lower():
                continue
            if full:
                out.append(
                    {
                        "api_name": api_name,
                        "ui_name": item.get("uiName"),
                        "description": item.get("description"),
                        "custom": bool(item.get("customDefinition")),
                    }
                )
            else:
                out.append(api_name)
        return out

    out: dict = {"property": prop}
    if kind in ("all", "dimensions"):
        out["dimensions"] = pick(response.get("dimensions"))
    if kind in ("all", "metrics"):
        out["metrics"] = pick(response.get("metrics"))
    return out


def check_compatibility(
    property_id: str,
    metrics: str = "sessions",
    dimensions: str | None = None,
    account: str | None = None,
) -> dict:
    """Ask GA4 whether a dimension/metric combination is queryable.

    Cheaper than running the report and reading the 400, and it names the
    fields that would have to be dropped.
    """
    body: dict = {
        "metrics": [{"name": n} for n in _split(metrics)],
        "compatibilityFilter": "COMPATIBLE",
    }
    dimension_names = _split(dimensions)
    if dimension_names:
        body["dimensions"] = [{"name": n} for n in dimension_names]

    prop = _property_path(property_id)
    try:
        response = (
            _data(account).properties().checkCompatibility(property=prop, body=body).execute()
        )
    except HttpError as e:
        handle_http_error(e, "analytics check")
        raise  # unreachable

    def summarise(items, key):
        out = []
        for item in items or []:
            field = (item.get(key) or {}).get("apiName")
            out.append({"name": field, "compatibility": item.get("compatibility")})
        return out

    dims = summarise(response.get("dimensionCompatibilities"), "dimensionMetadata")
    mets = summarise(response.get("metricCompatibilities"), "metricMetadata")
    incompatible = [
        f["name"] for f in dims + mets if f.get("compatibility") != "COMPATIBLE"
    ]
    return {
        "property": prop,
        "dimensions": dims,
        "metrics": mets,
        "compatible": not incompatible,
        "incompatible": incompatible,
    }

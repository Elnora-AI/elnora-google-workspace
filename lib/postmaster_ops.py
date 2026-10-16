"""Gmail Postmaster Tools operations — registered domains and their daily traffic stats.

Read-only. Two API versions answer different halves, each under its own read-only
scope, and the `postmaster` opt-in grants both:

  v2  postmaster.traffic.readonly  domainStats.query: spam rate, DKIM / SPF / DMARC
                                   pass rates, TLS, delivery errors, per day
  v1  postmaster.readonly          domains.list, and trafficStats: Google's domain
                                   reputation verdict, which v2 does not carry

v2 has a domains.list of its own, but it asks for postmaster.domain, a scope that
also creates, verifies and deletes domains. Asking for that to read a list is the
scope-widening SAFETY warns against, so the list is read through v1.

Google returns a day only when enough mail reached Gmail users that day. A day
missing from an answer is a day under that threshold, never a clean day, so every
response says how many days came back.
"""

from __future__ import annotations

from auth import build_service
from googleapiclient.errors import HttpError
from output import CliError, ValidationError, handle_http_error
from searchconsole_ops import _resolve_date, _split

# name -> (standard metric, filter). The names are what a row is keyed by.
METRICS: dict[str, tuple[str, str | None]] = {
    "spam_rate": ("SPAM_RATE", None),
    "dkim": ("AUTH_SUCCESS_RATE", 'auth_type = "dkim"'),
    "spf": ("AUTH_SUCCESS_RATE", 'auth_type = "spf"'),
    "dmarc": ("AUTH_SUCCESS_RATE", 'auth_type = "dmarc"'),
    "tls_outbound": ("TLS_ENCRYPTION_RATE", 'traffic_direction = "outbound"'),
    "delivery_error_rate": ("DELIVERY_ERROR_RATE", None),
    "delivery_errors": ("DELIVERY_ERROR_COUNT", None),
}
DEFAULT_METRICS = "spam_rate,dkim,spf,dmarc,delivery_error_rate"

ADD_SCOPE = "Run `gw auth login --add-scopes postmaster`: it keeps the account's other scopes."

THRESHOLD_NOTE = (
    "Postmaster reports a day only when enough mail reached Gmail users that day. "
    "A day missing here is under that threshold, not a day without problems."
)


def _ymd(iso: str) -> dict:
    y, m, d = (int(x) for x in iso.split("-"))
    return {"year": y, "month": m, "day": d}


def _date(d: dict) -> str:
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def _value(v: dict):
    for key in ("doubleValue", "floatValue"):
        if key in v:
            return float(v[key])
    if "intValue" in v:
        return int(v["intValue"])
    return v.get("stringValue")


def _status(e: HttpError) -> int:
    return getattr(getattr(e, "resp", None), "status", 0)


def list_domains(account: str | None = None) -> dict:
    """The domains this account registered in Postmaster Tools (v1, postmaster.readonly)."""
    try:
        result = build_service("gmailpostmastertools", "v1", account).domains().list().execute()
    except HttpError as e:
        if _status(e) == 403:
            raise CliError("Listing Postmaster domains needs the postmaster.readonly scope.",
                           suggestion=ADD_SCOPE) from e
        handle_http_error(e, "postmaster list domains")
        raise  # unreachable
    domains = [
        {"domain": d.get("name", "").split("/", 1)[-1], "permission": d.get("permission"),
         "created": d.get("createTime")}
        for d in result.get("domains", []) or []
    ]
    return {"domains": domains, "count": len(domains)}


def _reputation(domain: str, since: str, until: str, account: str | None) -> dict[str, str]:
    """Google's domain reputation per day (v1 trafficStats), {date: HIGH|MEDIUM|LOW|BAD}."""
    svc = build_service("gmailpostmastertools", "v1", account)
    s, u = _ymd(since), _ymd(until)
    params = {"parent": f"domains/{domain}", "startDate_year": s["year"], "startDate_month": s["month"],
              "startDate_day": s["day"], "endDate_year": u["year"], "endDate_month": u["month"],
              "endDate_day": u["day"]}
    out: dict[str, str] = {}
    request = svc.domains().trafficStats().list(**params)
    while request is not None:
        page = request.execute()
        for t in page.get("trafficStats", []) or []:
            # name: domains/{domain}/trafficStats/{YYYYMMDD}
            day = t.get("name", "").rsplit("/", 1)[-1]
            if len(day) == 8 and t.get("domainReputation"):
                out[f"{day[:4]}-{day[4:6]}-{day[6:]}"] = t["domainReputation"]
        request = svc.domains().trafficStats().list_next(request, page)
    return out


def stats(
    domain: str,
    since: str = "30daysAgo",
    until: str = "yesterday",
    metrics: str = DEFAULT_METRICS,
    account: str | None = None,
) -> dict:
    """One row per day Google reported, each metric a column, plus the domain reputation.

    Rates are fractions (0.003 is 0.3%). until is inclusive.
    """
    names = _split(metrics)
    unknown = [n for n in names if n not in METRICS]
    if unknown or not names:
        raise ValidationError(
            f"Unknown metric(s): {', '.join(unknown) or '(none given)'}.",
            suggestion=f"Valid: {', '.join(METRICS)}",
        )
    domain = domain.strip().lower().removeprefix("domains/")
    start, end = _resolve_date(since, field="since"), _resolve_date(until, field="until")
    if start > end:
        raise ValidationError(f"since {start} is after until {end}.", suggestion="Swap them.")

    body: dict = {
        "metricDefinitions": [
            {"name": n, "baseMetric": {"standardMetric": METRICS[n][0]},
             **({"filter": METRICS[n][1]} if METRICS[n][1] else {})}
            for n in names
        ],
        "timeQuery": {"dateRanges": {"dateRanges": [{"start": _ymd(start), "end": _ymd(end)}]}},
        "aggregationGranularity": "DAILY",
        "pageSize": 200,
    }
    svc = build_service("gmailpostmastertools", "v2", account)
    days: dict[str, dict] = {}
    try:
        while True:
            page = svc.domains().domainStats().query(parent=f"domains/{domain}", body=body).execute()
            for s in page.get("domainStats", []) or []:
                days.setdefault(_date(s["date"]), {})[s.get("metric")] = _value(s.get("value", {}))
            if not page.get("nextPageToken"):
                break
            body["pageToken"] = page["nextPageToken"]
    except HttpError as e:
        handle_http_error(e, "postmaster stats")
        raise  # unreachable

    notes = [THRESHOLD_NOTE]
    try:
        reputation = _reputation(domain, start, end, account)
    except HttpError as e:
        # An account authorized before the v1 scope joined the opt-in: the stats still stand.
        reputation = {}
        if _status(e) == 403:
            notes.append("Domain reputation needs the postmaster.readonly scope. " + ADD_SCOPE)
        else:
            handle_http_error(e, "postmaster reputation")
    for day, verdict in reputation.items():
        days.setdefault(day, {})["reputation"] = verdict

    rows = [{"date": d, **{n: v.get(n) for n in names}, "reputation": v.get("reputation")}
            for d, v in sorted(days.items())]
    return {"domain": domain, "window": {"since": start, "until": end}, "metrics": names,
            "rows": rows, "days_reported": len(rows), "notes": notes}

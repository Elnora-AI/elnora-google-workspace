"""Tests for postmaster_ops — the request it builds, the rows it flattens, mocked Postmaster calls."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import postmaster_ops
from googleapiclient.errors import HttpError
from output import ValidationError


def _stat(day, metric, value):
    y, m, d = (int(x) for x in day.split("-"))
    return {"date": {"year": y, "month": m, "day": d}, "metric": metric, "value": value}


def _services(pages, traffic=None, traffic_error=None):
    """A v2 service answering domainStats.query with `pages`, and a v1 service for trafficStats."""
    v2, v1 = MagicMock(), MagicMock()
    v2.domains().domainStats().query().execute.side_effect = pages
    listing = v1.domains().trafficStats().list.return_value
    if traffic_error:
        listing.execute.side_effect = traffic_error
    else:
        listing.execute.return_value = {"trafficStats": traffic or []}
    v1.domains().trafficStats().list_next.return_value = None
    return lambda api, version, account=None: v2 if version == "v2" else v1


@pytest.fixture
def two_days():
    return _services(
        [{"domainStats": [_stat("2026-09-16", "spam_rate", {"doubleValue": 0.002}),
                          _stat("2026-09-16", "dkim", {"doubleValue": 1}),
                          _stat("2026-09-17", "spam_rate", {"doubleValue": 0})],
          "nextPageToken": "p2"},
         {"domainStats": [_stat("2026-09-17", "dkim", {"floatValue": 0.98})]}],
        traffic=[{"name": "domains/example.com/trafficStats/20260916", "domainReputation": "HIGH"}],
    )


class TestStats:
    def test_rows_per_day_metrics_as_columns_across_pages(self, two_days):
        with patch("postmaster_ops.build_service", side_effect=two_days):
            out = postmaster_ops.stats("example.com", since="2026-09-01", until="2026-09-30",
                                       metrics="spam_rate,dkim")
        assert out["rows"] == [
            {"date": "2026-09-16", "spam_rate": 0.002, "dkim": 1.0, "reputation": "HIGH"},
            {"date": "2026-09-17", "spam_rate": 0.0, "dkim": 0.98, "reputation": None},
        ]
        assert out["days_reported"] == 2 and out["window"] == {"since": "2026-09-01", "until": "2026-09-30"}

    def test_request_names_each_metric_and_its_filter(self, two_days):
        with patch("postmaster_ops.build_service", side_effect=two_days) as build:
            postmaster_ops.stats("domains/Example.com", since="2026-09-01", until="2026-09-02")
        v2 = build("x", "v2")
        call = v2.domains().domainStats().query.call_args_list[-1].kwargs
        assert call["parent"] == "domains/example.com"
        defs = {d["name"]: d for d in call["body"]["metricDefinitions"]}
        assert defs["dkim"] == {"name": "dkim", "baseMetric": {"standardMetric": "AUTH_SUCCESS_RATE"},
                                "filter": 'auth_type = "dkim"'}
        assert "filter" not in defs["spam_rate"]
        assert call["body"]["timeQuery"]["dateRanges"]["dateRanges"][0]["end"] == {"year": 2026, "month": 9, "day": 2}

    def test_no_day_reported_says_why(self):
        with patch("postmaster_ops.build_service", side_effect=_services([{}])):
            out = postmaster_ops.stats("example.com", since="2026-09-01", until="2026-09-30")
        assert out["rows"] == [] and out["days_reported"] == 0
        assert "threshold" in out["notes"][0]

    def test_a_token_without_the_v1_scope_still_gets_the_stats(self):
        denied = HttpError(MagicMock(status=403), b'{"error": {"message": "insufficient scopes"}}')
        svc = _services([{"domainStats": [_stat("2026-09-16", "spam_rate", {"doubleValue": 0})]}],
                        traffic_error=denied)
        with patch("postmaster_ops.build_service", side_effect=svc):
            out = postmaster_ops.stats("example.com", since="2026-09-01", until="2026-09-30")
        assert out["rows"][0]["spam_rate"] == 0.0
        assert any("--add-scopes postmaster" in n for n in out["notes"])

    @pytest.mark.parametrize("metrics", ["spam_rate,reputation_score", ""])
    def test_unknown_metric_is_refused_before_a_call(self, metrics):
        with patch("postmaster_ops.build_service") as build:
            with pytest.raises(ValidationError, match="Unknown metric"):
                postmaster_ops.stats("example.com", metrics=metrics)
        build.assert_not_called()

    def test_reversed_window_is_refused(self):
        with pytest.raises(ValidationError, match="after until"):
            postmaster_ops.stats("example.com", since="2026-09-30", until="2026-09-01")


class TestDomains:
    def test_lists_names_without_the_resource_prefix(self):
        v1 = MagicMock()
        v1.domains().list().execute.return_value = {
            "domains": [{"name": "domains/example.com", "permission": "OWNER", "createTime": "2026-01-01T00:00:00Z"}]}
        with patch("postmaster_ops.build_service", return_value=v1) as build:
            out = postmaster_ops.list_domains()
        assert out == {"domains": [{"domain": "example.com", "permission": "OWNER",
                                    "created": "2026-01-01T00:00:00Z"}], "count": 1}
        assert build.call_args.args[:2] == ("gmailpostmastertools", "v1")


def test_a_token_without_the_v1_scope_is_told_how_to_list():
    v1 = MagicMock()
    v1.domains().list().execute.side_effect = HttpError(MagicMock(status=403), b"{}")
    with patch("postmaster_ops.build_service", return_value=v1):
        with pytest.raises(postmaster_ops.CliError, match="postmaster.readonly") as e:
            postmaster_ops.list_domains()
    assert "--add-scopes postmaster" in e.value.suggestion


def test_read_only_by_construction():
    """Nothing here creates, verifies or deletes a domain, or touches its users."""
    source = Path(postmaster_ops.__file__).read_text()
    for verb in (".create(", ".delete(", ".verify(", ".patch(", ".users("):
        assert verb not in source

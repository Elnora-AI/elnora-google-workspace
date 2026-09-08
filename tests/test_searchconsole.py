"""Tests for searchconsole_ops — dates, filters, flattening, mocked GSC calls."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import searchconsole_ops
from output import ValidationError


class TestDates:
    def test_iso_passes_through(self):
        assert searchconsole_ops._resolve_date("2026-08-01", field="since") == "2026-08-01"

    def test_relative_resolves_against_a_fixed_today(self):
        today = date(2026, 9, 8)
        assert searchconsole_ops._resolve_date("today", field="since", today=today) == "2026-09-08"
        assert searchconsole_ops._resolve_date("yesterday", field="since", today=today) == "2026-09-07"
        assert searchconsole_ops._resolve_date("28daysAgo", field="since", today=today) == "2026-08-11"

    @pytest.mark.parametrize("value", ["last month", "2026/08/01", "28 days ago"])
    def test_rejected(self, value):
        with pytest.raises(ValidationError, match="Invalid since"):
            searchconsole_ops._resolve_date(value, field="since")


class TestFilters:
    def test_operators_map_to_the_api_names(self):
        assert searchconsole_ops._build_filters(["query~~pricing"])[0]["filters"][0] == {
            "dimension": "query",
            "operator": "contains",
            "expression": "pricing",
        }
        assert searchconsole_ops._build_filters(["page=~^/blog"])[0]["filters"][0][
            "operator"
        ] == "includingRegex"
        assert searchconsole_ops._build_filters(["device==MOBILE"])[0]["filters"][0][
            "operator"
        ] == "equals"

    def test_multiple_become_separate_groups(self):
        groups = searchconsole_ops._build_filters(["query~~pricing", "device==MOBILE"])
        assert len(groups) == 2

    def test_malformed_is_rejected(self):
        with pytest.raises(ValidationError, match="Invalid filter"):
            searchconsole_ops._build_filters(["query contains pricing"])


class TestFlattenRows:
    def test_positional_keys_become_named(self):
        response = {
            "rows": [
                {"keys": ["pricing", "MOBILE"], "clicks": 3, "impressions": 40,
                 "ctr": 0.075, "position": 8.2}
            ]
        }
        rows = searchconsole_ops.flatten_rows(response, ["query", "device"])
        assert rows[0] == {
            "query": "pricing",
            "device": "MOBILE",
            "clicks": 3,
            "impressions": 40,
            "ctr": 0.075,
            "position": 8.2,
        }

    def test_missing_metrics_default_to_zero(self):
        rows = searchconsole_ops.flatten_rows({"rows": [{"keys": ["x"]}]}, ["query"])
        assert rows[0]["clicks"] == 0
        assert rows[0]["impressions"] == 0

    def test_empty_response_is_safe(self):
        assert searchconsole_ops.flatten_rows({}, ["query"]) == []


@pytest.fixture
def mock_sc_service():
    svc = MagicMock()
    svc.searchanalytics().query().execute.return_value = {
        "rows": [
            {"keys": ["pricing"], "clicks": 3, "impressions": 40, "ctr": 0.075, "position": 8.2}
        ],
        "responseAggregationType": "byProperty",
    }
    return svc


@pytest.fixture
def patch_sc(mock_sc_service):
    with patch("searchconsole_ops.build_service", return_value=mock_sc_service):
        yield searchconsole_ops, mock_sc_service


class TestQuery:
    def test_returns_flattened_rows(self, patch_sc):
        mod, _ = patch_sc
        out = mod.query(site="sc-domain:example.com", dimensions="query",
                        since="2026-08-01", until="2026-08-31")
        assert out["rows"][0]["query"] == "pricing"
        assert out["returned"] == 1
        assert out["site"] == "sc-domain:example.com"

    def test_default_window_ends_before_the_lag(self, patch_sc):
        """Search Console lags 2-3 days; the default must not ask for yesterday."""
        mod, svc = patch_sc
        mod.query(site="sc-domain:example.com")
        body = svc.searchanalytics().query.call_args.kwargs["body"]
        assert body["startDate"] < body["endDate"]

    def test_query_dimension_carries_the_threshold_note(self, patch_sc):
        mod, _ = patch_sc
        out = mod.query(site="sc-domain:example.com", dimensions="query")
        assert "privacy threshold" in out["note"]

    def test_non_query_dimension_has_no_note(self, patch_sc):
        mod, _ = patch_sc
        assert mod.query(site="sc-domain:example.com", dimensions="device")["note"] is None

    def test_unknown_dimension_is_rejected(self, patch_sc):
        mod, _ = patch_sc
        with pytest.raises(ValidationError, match="Unknown dimension"):
            mod.query(site="sc-domain:example.com", dimensions="keyword")

    def test_row_limit_ceiling_is_enforced(self, patch_sc):
        mod, _ = patch_sc
        with pytest.raises(ValidationError, match="caps at 25000"):
            mod.query(site="sc-domain:example.com", limit=25001)


class TestListSites:
    def test_flattens_site_entries(self):
        svc = MagicMock()
        svc.sites().list().execute.return_value = {
            "siteEntry": [
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                {"siteUrl": "https://example.com/", "permissionLevel": "siteFullUser"},
            ]
        }
        with patch("searchconsole_ops.build_service", return_value=svc):
            out = searchconsole_ops.list_sites()
        assert out["count"] == 2
        assert out["sites"][0]["site_url"] == "sc-domain:example.com"


class TestInspectUrl:
    def test_reduces_to_the_fields_worth_reading(self):
        svc = MagicMock()
        svc.urlInspection().index().inspect().execute.return_value = {
            "inspectionResult": {
                "indexStatusResult": {
                    "verdict": "PASS",
                    "coverageState": "Submitted and indexed",
                    "robotsTxtState": "ALLOWED",
                    "lastCrawlTime": "2026-09-01T00:00:00Z",
                },
                "inspectionResultLink": "https://search.google.com/search-console/inspect",
            }
        }
        with patch("searchconsole_ops.build_service", return_value=svc):
            out = searchconsole_ops.inspect_url(site="sc-domain:example.com",
                                                url="https://example.com/pricing")
        assert out["verdict"] == "PASS"
        assert out["coverage_state"] == "Submitted and indexed"


class TestWriteSurfaceIsAbsent:
    def test_no_destructive_operations_are_exposed(self):
        """sites.add/delete and sitemaps.submit/delete stay unreachable from the CLI."""
        for forbidden in ("add_site", "delete_site", "submit_sitemap", "delete_sitemap"):
            assert not hasattr(searchconsole_ops, forbidden)

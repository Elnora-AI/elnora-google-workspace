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
    def test_operators_and_dimensions_use_the_discovery_spelling(self):
        """The v1 discovery enums are upper snake case; only that is guaranteed."""
        assert searchconsole_ops._build_filters(["query~~pricing"])[0]["filters"][0] == {
            "dimension": "QUERY",
            "operator": "CONTAINS",
            "expression": "pricing",
        }
        assert searchconsole_ops._build_filters(["page=~^/blog"])[0]["filters"][0][
            "operator"
        ] == "INCLUDING_REGEX"
        assert searchconsole_ops._build_filters(["device==MOBILE"])[0]["filters"][0][
            "operator"
        ] == "EQUALS"
        assert searchconsole_ops._build_filters(["query!=brand"])[0]["filters"][0][
            "operator"
        ] == "NOT_EQUALS"

    def test_camel_case_dimension_normalises(self):
        assert searchconsole_ops._build_filters(["searchAppearance==AMP_BLUE_LINK"])[0][
            "filters"
        ][0]["dimension"] == "SEARCH_APPEARANCE"

    def test_date_is_groupable_but_not_filterable(self):
        with pytest.raises(ValidationError, match="Cannot filter on dimension"):
            searchconsole_ops._build_filters(["date==2026-08-01"])

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

    def test_request_enums_are_normalised(self, patch_sc):
        mod, svc = patch_sc
        mod.query(site="sc-domain:example.com", dimensions="query,page",
                  search_type="web", data_state="final")
        body = svc.searchanalytics().query.call_args.kwargs["body"]
        assert body["dimensions"] == ["QUERY", "PAGE"]
        assert body["type"] == "WEB"
        assert body["dataState"] == "FINAL"

    def test_unknown_search_type_is_rejected(self, patch_sc):
        mod, _ = patch_sc
        with pytest.raises(ValidationError, match="Unknown search type"):
            mod.query(site="sc-domain:example.com", search_type="organic")

    def test_unknown_data_state_is_rejected(self, patch_sc):
        mod, _ = patch_sc
        with pytest.raises(ValidationError, match="Unknown data state"):
            mod.query(site="sc-domain:example.com", data_state="fresh")

    def test_hour_grouping_requires_hourly_data_state(self, patch_sc):
        mod, _ = patch_sc
        with pytest.raises(ValidationError, match="requires --data-state hourlyAll"):
            mod.query(site="sc-domain:example.com", dimensions="hour")

    def test_hour_grouping_allowed_with_hourly_all(self, patch_sc):
        mod, svc = patch_sc
        mod.query(site="sc-domain:example.com", dimensions="hour", data_state="hourlyAll")
        assert svc.searchanalytics().query.call_args.kwargs["body"]["dataState"] == "HOURLY_ALL"

    def test_threshold_note_points_at_a_command_that_works(self, patch_sc):
        """The CLI default is 'query', so 'omit --dimensions' was wrong advice."""
        mod, _ = patch_sc
        note = mod.query(site="sc-domain:example.com", dimensions="query")["note"]
        assert "--dimensions ''" in note

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
        """Guards the shape, not a name list, so a future create_site cannot slip past."""
        allowed = {"list_sites", "query", "list_sitemaps", "inspect_url",
                   "flatten_rows"}
        public = {
            name for name in dir(searchconsole_ops)
            if not name.startswith("_") and callable(getattr(searchconsole_ops, name))
            and getattr(getattr(searchconsole_ops, name), "__module__", "") == "searchconsole_ops"
        }
        assert public == allowed, f"unexpected public callable(s): {public - allowed}"

    def test_no_write_verb_appears_in_any_public_name(self):
        for name in dir(searchconsole_ops):
            if name.startswith("_"):
                continue
            for verb in ("add", "create", "delete", "remove", "submit", "update", "put"):
                assert not name.lower().startswith(verb), f"write-shaped name: {name}"


class TestDimensionCasingIsCanonical:
    def test_uppercase_input_returns_canonical_row_keys(self):
        """Row keys must not depend on how the caller spelled the request."""
        svc = MagicMock()
        svc.searchanalytics().query().execute.return_value = {
            "rows": [{"keys": ["elnora"], "clicks": 3, "impressions": 9,
                      "ctr": 0.33, "position": 1.5}]
        }
        with patch("searchconsole_ops.build_service", return_value=svc):
            out = searchconsole_ops.query(site="sc-domain:x.com", dimensions="QUERY")
        assert out["dimensions"] == ["query"]
        assert "query" in out["rows"][0]
        assert "QUERY" not in out["rows"][0]

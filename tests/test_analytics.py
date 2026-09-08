"""Tests for analytics_ops — flattening, filters, validation, mocked GA4 calls."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import analytics_ops
from output import ValidationError


# ---------------------------------------------------------------------------
# Property id normalisation
# ---------------------------------------------------------------------------

class TestPropertyPath:
    def test_bare_numeric(self):
        assert analytics_ops._property_path("123456789") == "properties/123456789"

    def test_already_prefixed(self):
        assert analytics_ops._property_path("properties/123456789") == "properties/123456789"

    def test_measurement_id_is_rejected(self):
        """G-XXXXXXXXXX is the tag id, not the property id. They get confused."""
        with pytest.raises(ValidationError, match="Invalid GA4 property id"):
            analytics_ops._property_path("G-ABCDEFGHIJ")

    def test_empty_is_rejected(self):
        with pytest.raises(ValidationError):
            analytics_ops._property_path("")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

class TestDates:
    @pytest.mark.parametrize("value", ["today", "yesterday", "7daysAgo", "2026-08-01"])
    def test_accepted(self, value):
        assert analytics_ops._date(value, field="since") == value

    @pytest.mark.parametrize("value", ["last week", "2026/08/01", "7 days ago", "3daysago"])
    def test_rejected(self, value):
        with pytest.raises(ValidationError, match="Invalid since"):
            analytics_ops._date(value, field="since")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestFilters:
    def test_none_returns_none(self):
        assert analytics_ops._build_filter([]) is None

    def test_exact_match(self):
        expr = analytics_ops._build_filter(["hostname==example.com"])
        assert expr["filter"]["fieldName"] == "hostname"
        assert expr["filter"]["stringFilter"] == {"matchType": "EXACT", "value": "example.com"}

    def test_negation_wraps_in_not_expression(self):
        expr = analytics_ops._build_filter(["hostname!=localhost"])
        assert "notExpression" in expr
        assert expr["notExpression"]["filter"]["fieldName"] == "hostname"

    def test_regex_and_contains(self):
        assert analytics_ops._build_filter(["pagePath=~^/blog"])["filter"]["stringFilter"][
            "matchType"
        ] == "FULL_REGEXP"
        assert analytics_ops._build_filter(["pagePath=@blog"])["filter"]["stringFilter"][
            "matchType"
        ] == "CONTAINS"

    def test_multiple_are_anded(self):
        expr = analytics_ops._build_filter(["hostname==example.com", "pagePath=@/blog"])
        assert len(expr["andGroup"]["expressions"]) == 2

    def test_malformed_is_rejected(self):
        with pytest.raises(ValidationError, match="Invalid filter"):
            analytics_ops._build_filter(["hostname is example.com"])


# ---------------------------------------------------------------------------
# Flattening — the token-efficiency contract
# ---------------------------------------------------------------------------

_RAW_REPORT = {
    "dimensionHeaders": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
    "metricHeaders": [
        {"name": "sessions", "type": "TYPE_INTEGER"},
        {"name": "averageSessionDuration", "type": "TYPE_SECONDS"},
    ],
    "rows": [
        {
            "dimensionValues": [{"value": "20260901"}, {"value": "Organic Search"}],
            "metricValues": [{"value": "42"}, {"value": "31.5"}],
        },
        {
            "dimensionValues": [{"value": "20260902"}, {"value": "Direct"}],
            "metricValues": [{"value": "17"}, {"value": "12.25"}],
        },
    ],
    "rowCount": 2,
}


class TestFlattenReport:
    def test_rows_become_plain_records(self):
        out = analytics_ops.flatten_report(_RAW_REPORT)
        assert out["rows"][0] == {
            "date": "20260901",
            "sessionDefaultChannelGroup": "Organic Search",
            "sessions": 42,
            "averageSessionDuration": 31.5,
        }

    def test_metric_types_are_coerced(self):
        out = analytics_ops.flatten_report(_RAW_REPORT)
        assert isinstance(out["rows"][0]["sessions"], int)
        assert isinstance(out["rows"][0]["averageSessionDuration"], float)

    def test_counts_are_reported(self):
        out = analytics_ops.flatten_report(_RAW_REPORT)
        assert out["row_count"] == 2
        assert out["returned"] == 2

    def test_empty_response_is_safe(self):
        out = analytics_ops.flatten_report({})
        assert out["rows"] == []
        assert out["returned"] == 0

    def test_totals_are_named(self):
        raw = dict(_RAW_REPORT)
        raw["totals"] = [{"metricValues": [{"value": "59"}, {"value": "21.9"}]}]
        out = analytics_ops.flatten_report(raw)
        assert out["totals"] == {"sessions": 59, "averageSessionDuration": 21.9}

    def test_other_row_is_surfaced_and_is_not_sampling(self):
        raw = dict(_RAW_REPORT)
        raw["metadata"] = {"dataLossFromOtherRow": True}
        out = analytics_ops.flatten_report(raw)
        assert out["data_loss_from_other_row"] is True
        assert "sampled" not in out

    def test_sampling_is_surfaced_separately(self):
        raw = dict(_RAW_REPORT)
        raw["metadata"] = {"samplingMetadatas": [{"samplesReadCount": "1", "samplingSpaceSize": "2"}]}
        out = analytics_ops.flatten_report(raw)
        assert out["sampled"] is True
        assert "data_loss_from_other_row" not in out

    def test_unparseable_metric_value_is_kept_as_is(self):
        raw = {
            "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}],
            "rows": [{"dimensionValues": [], "metricValues": [{"value": "n/a"}]}],
        }
        assert analytics_ops.flatten_report(raw)["rows"][0]["sessions"] == "n/a"


# ---------------------------------------------------------------------------
# report() against a mocked service
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_data_service():
    svc = MagicMock()
    svc.properties().runReport().execute.return_value = _RAW_REPORT
    return svc


@pytest.fixture
def patch_data(mock_data_service):
    with patch("analytics_ops.build_service", return_value=mock_data_service):
        yield analytics_ops, mock_data_service


class TestReport:
    def test_returns_flattened_rows_with_window(self, patch_data):
        mod, _ = patch_data
        out = mod.report(property_id="123", metrics="sessions", dimensions="date",
                         since="7daysAgo", until="yesterday")
        assert out["property"] == "properties/123"
        assert out["window"] == {"since": "7daysAgo", "until": "yesterday"}
        assert out["rows"][0]["sessions"] == 42

    def test_body_shape_sent_to_the_api(self, patch_data):
        mod, svc = patch_data
        mod.report(property_id="123", metrics="sessions,activeUsers",
                   dimensions="date", limit=5, totals=True,
                   filters=("hostname==example.com",))
        body = svc.properties().runReport.call_args.kwargs["body"]
        assert body["metrics"] == [{"name": "sessions"}, {"name": "activeUsers"}]
        assert body["dimensions"] == [{"name": "date"}]
        assert body["limit"] == 5
        assert body["metricAggregations"] == ["TOTAL"]
        assert body["dimensionFilter"]["filter"]["fieldName"] == "hostname"

    def test_no_metrics_is_rejected(self, patch_data):
        mod, _ = patch_data
        with pytest.raises(ValidationError, match="At least one metric"):
            mod.report(property_id="123", metrics="")

    def test_more_than_nine_dimensions_is_rejected(self, patch_data):
        mod, _ = patch_data
        with pytest.raises(ValidationError, match="at most 9 dimensions"):
            mod.report(property_id="123", dimensions=",".join(f"d{i}" for i in range(10)))

    def test_order_by_a_dimension_uses_the_dimension_variant(self, patch_data):
        """OrderBy carries a dimension field; a metric OrderBy for 'date' 400s."""
        mod, svc = patch_data
        mod.report(property_id="123", metrics="sessions", dimensions="date", order_by="date")
        body = svc.properties().runReport.call_args.kwargs["body"]
        assert body["orderBys"] == [{"desc": False, "dimension": {"dimensionName": "date"}}]

    def test_order_by_a_metric_descending(self, patch_data):
        mod, svc = patch_data
        mod.report(property_id="123", metrics="sessions", dimensions="date", order_by="-sessions")
        body = svc.properties().runReport.call_args.kwargs["body"]
        assert body["orderBys"] == [{"desc": True, "metric": {"metricName": "sessions"}}]

    def test_order_by_an_unrequested_field_is_rejected_by_name(self, patch_data):
        mod, _ = patch_data
        with pytest.raises(ValidationError, match="neither a requested metric"):
            mod.report(property_id="123", metrics="sessions", dimensions="date",
                       order_by="activeUsers")

    def test_bare_minus_order_by_is_rejected(self, patch_data):
        """'-' alone used to emit an empty OrderBy and an opaque 400."""
        mod, _ = patch_data
        with pytest.raises(ValidationError, match="names no field"):
            mod.report(property_id="123", metrics="sessions", order_by="-")


class TestListProperties:
    def test_follows_pagination(self):
        """A truncated property list is a silently wrong answer."""
        svc = MagicMock()
        page1 = {
            "accountSummaries": [
                {"displayName": "Acme", "propertySummaries": [
                    {"property": "properties/1", "displayName": "One"}]}
            ],
            "nextPageToken": "tok",
        }
        page2 = {
            "accountSummaries": [
                {"displayName": "Acme", "propertySummaries": [
                    {"property": "properties/2", "displayName": "Two"}]}
            ]
        }
        svc.accountSummaries().list().execute.side_effect = [page1, page2]
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.list_properties()
        assert out["count"] == 2
        assert [p["property_id"] for p in out["properties"]] == ["1", "2"]
        assert "truncated" not in out

    def test_flattens_account_summaries(self):
        svc = MagicMock()
        svc.accountSummaries().list().execute.return_value = {
            "accountSummaries": [
                {
                    "displayName": "Acme",
                    "propertySummaries": [
                        {"property": "properties/123", "displayName": "Site", "propertyType": "PROPERTY_TYPE_ORDINARY"}
                    ],
                }
            ]
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.list_properties()
        assert out["count"] == 1
        assert out["properties"][0]["property_id"] == "123"
        assert out["properties"][0]["account"] == "Acme"


class TestMetadata:
    def _svc(self):
        svc = MagicMock()
        svc.properties().getMetadata().execute.return_value = {
            "dimensions": [
                {"apiName": "sessionDefaultChannelGroup", "uiName": "Session default channel group"},
                {"apiName": "pagePath", "uiName": "Page path"},
            ],
            "metrics": [{"apiName": "sessions", "uiName": "Sessions"}],
        }
        return svc

    def test_names_only_by_default(self):
        with patch("analytics_ops.build_service", return_value=self._svc()):
            out = analytics_ops.metadata(property_id="123")
        assert out["dimensions"] == ["sessionDefaultChannelGroup", "pagePath"]
        assert out["metrics"] == ["sessions"]

    def test_grep_narrows(self):
        with patch("analytics_ops.build_service", return_value=self._svc()):
            out = analytics_ops.metadata(property_id="123", kind="dimensions", grep="channel")
        assert out["dimensions"] == ["sessionDefaultChannelGroup"]
        assert "metrics" not in out

    def test_full_includes_descriptions(self):
        with patch("analytics_ops.build_service", return_value=self._svc()):
            out = analytics_ops.metadata(property_id="123", kind="metrics", full=True)
        assert out["metrics"][0]["ui_name"] == "Sessions"


class TestCheckCompatibility:
    def test_does_not_filter_the_response_to_compatible_only(self):
        """compatibilityFilter=COMPATIBLE makes the API strip the very entries
        this command exists to surface, so it must not be sent."""
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [], "metricCompatibilities": []
        }
        with patch("analytics_ops.build_service", return_value=svc):
            analytics_ops.check_compatibility(property_id="123", dimensions="date")
        body = svc.properties().checkCompatibility.call_args.kwargs["body"]
        assert "compatibilityFilter" not in body

    def test_reports_incompatible_fields(self):
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [
                {"dimensionMetadata": {"apiName": "pagePath"}, "compatibility": "COMPATIBLE"},
                {"dimensionMetadata": {"apiName": "transactionId"}, "compatibility": "INCOMPATIBLE"},
            ],
            "metricCompatibilities": [
                {"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"},
            ],
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.check_compatibility(
                property_id="123", metrics="sessions", dimensions="pagePath,transactionId"
            )
        assert out["compatible"] is False
        assert out["incompatible"] == ["transactionId"]

    def test_narrows_to_the_fields_the_caller_asked_about(self):
        """The API judges EVERY field in the property against the request, so a
        raw pass-through answers about ~490 fields nobody asked about -- and
        reports incompatible for unrelated ones, which is a wrong answer to
        'can I query this?' as well as ~30KB of it."""
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [
                {"dimensionMetadata": {"apiName": "date"}, "compatibility": "COMPATIBLE"},
                {"dimensionMetadata": {"apiName": "cohortNthDay"}, "compatibility": "INCOMPATIBLE"},
                {"dimensionMetadata": {"apiName": "unrelatedOne"}, "compatibility": "INCOMPATIBLE"},
            ],
            "metricCompatibilities": [
                {"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"},
                {"metricMetadata": {"apiName": "advertiserAdCost"}, "compatibility": "INCOMPATIBLE"},
            ],
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.check_compatibility(
                property_id="123", metrics="sessions", dimensions="date"
            )
        assert [d["name"] for d in out["dimensions"]] == ["date"]
        assert [m["name"] for m in out["metrics"]] == ["sessions"]
        assert out["compatible"] is True
        assert out["incompatible"] == []
        assert "could_add" not in out

    def test_asked_for_field_missing_from_response_is_unknown(self):
        """A name the property does not carry must not read as compatible."""
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [
                {"dimensionMetadata": {"apiName": "date"}, "compatibility": "COMPATIBLE"}
            ],
            "metricCompatibilities": [
                {"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"}
            ],
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.check_compatibility(
                property_id="123", metrics="sessions", dimensions="date,notAThing"
            )
        assert {"name": "notAThing", "compatibility": "UNKNOWN_FIELD"} in out["dimensions"]
        assert out["compatible"] is False
        assert out["incompatible"] == ["notAThing"]

    def test_suggest_lists_other_compatible_fields_only(self):
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [
                {"dimensionMetadata": {"apiName": "date"}, "compatibility": "COMPATIBLE"},
                {"dimensionMetadata": {"apiName": "country"}, "compatibility": "COMPATIBLE"},
                {"dimensionMetadata": {"apiName": "cohortNthDay"}, "compatibility": "INCOMPATIBLE"},
            ],
            "metricCompatibilities": [
                {"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"},
                {"metricMetadata": {"apiName": "activeUsers"}, "compatibility": "COMPATIBLE"},
            ],
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.check_compatibility(
                property_id="123", metrics="sessions", dimensions="date", suggest=True
            )
        # Only compatible, and never the fields already requested.
        assert out["could_add"]["dimensions"] == ["country"]
        assert out["could_add"]["metrics"] == ["activeUsers"]

    def test_all_compatible(self):
        svc = MagicMock()
        svc.properties().checkCompatibility().execute.return_value = {
            "dimensionCompatibilities": [
                {"dimensionMetadata": {"apiName": "date"}, "compatibility": "COMPATIBLE"}
            ],
            "metricCompatibilities": [
                {"metricMetadata": {"apiName": "sessions"}, "compatibility": "COMPATIBLE"}
            ],
        }
        with patch("analytics_ops.build_service", return_value=svc):
            out = analytics_ops.check_compatibility(property_id="123", dimensions="date")
        assert out["compatible"] is True
        assert out["incompatible"] == []


class TestPropertyIdConfusions:
    """The measurement id, the GTM container id and the property id all sit next
    to each other in the GA4 UI. Naming the mistake beats a generic rejection."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("G-ABC1234567", "measurement id"),
            ("GTM-ABC1234", "Tag Manager"),
            ("UA-12345-1", "Universal Analytics"),
        ],
    )
    def test_names_the_confusion(self, value, expected):
        with pytest.raises(ValidationError) as err:
            analytics_ops._property_path(value)
        assert expected in str(err.value)

    def test_plain_garbage_still_rejected(self):
        with pytest.raises(ValidationError):
            analytics_ops._property_path("not-an-id")

    def test_valid_forms_still_accepted(self):
        assert analytics_ops._property_path("123") == "properties/123"
        assert analytics_ops._property_path("properties/123") == "properties/123"

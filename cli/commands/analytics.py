"""Google Workspace CLI — Google Analytics 4 commands (read-only)."""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register GA4 commands on the CLI group."""

    @cli_group.group()
    def analytics():
        """Google Analytics 4 — properties, reports, realtime, metadata (read-only)."""
        pass

    @analytics.command(name="properties")
    @account_option
    @compact_option
    def analytics_properties(account, compact):
        """List the GA4 properties this account can reach."""
        import analytics_ops
        with _handle_errors(compact):
            output_success(analytics_ops.list_properties(account=account), compact=compact)

    @analytics.command(name="report")
    @click.option("--property", "property_id", required=True, help="GA4 property id (numeric)")
    @click.option("--metrics", default="sessions", help="Comma-separated metrics (default: sessions)")
    @click.option("--dimensions", default=None, help="Comma-separated dimensions (max 9)")
    @click.option("--since", default="28daysAgo", help="Start: YYYY-MM-DD, today, yesterday or NdaysAgo")
    @click.option("--until", default="yesterday", help="End: YYYY-MM-DD, today, yesterday or NdaysAgo")
    @click.option("--limit", type=int, default=100, help="Max rows (default: 100)")
    @click.option("--offset", type=int, default=0, help="Row offset for paging")
    @click.option("--order-by", default=None, help="Metric to sort by; prefix with - for descending")
    @click.option("--filter", "filters", multiple=True, help="dim==value | dim!=value | dim=~regex | dim=@substring (repeatable, ANDed)")
    @click.option("--totals", is_flag=True, help="Include a totals row")
    @click.option("--keep-empty-rows", is_flag=True, help="Keep rows whose metrics are all zero")
    @account_option
    @compact_option
    def analytics_report(property_id, metrics, dimensions, since, until, limit, offset,
                         order_by, filters, totals, keep_empty_rows, account, compact):
        """Run a GA4 report and return flattened rows."""
        import analytics_ops
        with _handle_errors(compact):
            result = analytics_ops.report(
                property_id=property_id, metrics=metrics, dimensions=dimensions,
                since=since, until=until, limit=limit, offset=offset,
                order_by=order_by, filters=filters, totals=totals,
                keep_empty_rows=keep_empty_rows, account=account,
            )
            output_success(result, compact=compact)

    @analytics.command(name="realtime")
    @click.option("--property", "property_id", required=True, help="GA4 property id (numeric)")
    @click.option("--metrics", default="activeUsers", help="Comma-separated metrics (default: activeUsers)")
    @click.option("--dimensions", default=None, help="Comma-separated dimensions")
    @click.option("--limit", type=int, default=50, help="Max rows (default: 50)")
    @account_option
    @compact_option
    def analytics_realtime(property_id, metrics, dimensions, limit, account, compact):
        """Active users in the last 30 minutes."""
        import analytics_ops
        with _handle_errors(compact):
            result = analytics_ops.realtime(
                property_id=property_id, metrics=metrics,
                dimensions=dimensions, limit=limit, account=account,
            )
            output_success(result, compact=compact)

    @analytics.command(name="metadata")
    @click.option("--property", "property_id", required=True, help="GA4 property id (numeric)")
    @click.option("--kind", type=click.Choice(["all", "dimensions", "metrics"]), default="all")
    @click.option("--grep", default=None, help="Only names containing this text")
    @click.option("--full", is_flag=True, help="Include UI names and descriptions")
    @account_option
    @compact_option
    def analytics_metadata(property_id, kind, grep, full, account, compact):
        """List the dimension and metric names this property accepts."""
        import analytics_ops
        with _handle_errors(compact):
            result = analytics_ops.metadata(
                property_id=property_id, kind=kind, grep=grep, full=full, account=account,
            )
            output_success(result, compact=compact)

    @analytics.command(name="check")
    @click.option("--property", "property_id", required=True, help="GA4 property id (numeric)")
    @click.option("--metrics", default="sessions", help="Comma-separated metrics")
    @click.option("--dimensions", default=None, help="Comma-separated dimensions")
    @click.option("--suggest", is_flag=True, help="Also list the other fields that could be added to this request")
    @account_option
    @compact_option
    def analytics_check(property_id, metrics, dimensions, suggest, account, compact):
        """Check whether a dimension/metric combination is queryable."""
        import analytics_ops
        with _handle_errors(compact):
            result = analytics_ops.check_compatibility(
                property_id=property_id, metrics=metrics,
                dimensions=dimensions, suggest=suggest, account=account,
            )
            output_success(result, compact=compact)

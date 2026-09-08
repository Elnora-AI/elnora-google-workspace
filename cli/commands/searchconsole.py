"""Google Workspace CLI — Google Search Console commands (read-only)."""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Search Console commands on the CLI group."""

    @cli_group.group()
    def searchconsole():
        """Google Search Console — sites, search analytics, sitemaps, URL inspection (read-only)."""
        pass

    @searchconsole.command(name="sites")
    @account_option
    @compact_option
    def sc_sites(account, compact):
        """List properties this account can reach.

        sc-domain:example.com and https://example.com/ are different
        properties. Use the exact string this returns.
        """
        import searchconsole_ops
        with _handle_errors(compact):
            output_success(searchconsole_ops.list_sites(account=account), compact=compact)

    @searchconsole.command(name="query")
    @click.option("--site", required=True, help="Property, exactly as 'searchconsole sites' returns it")
    @click.option("--dimensions", default="query", help="query,page,country,device,searchAppearance,date")
    @click.option("--since", default="28daysAgo", help="Start: YYYY-MM-DD, today, yesterday or NdaysAgo")
    @click.option("--until", default="3daysAgo", help="End (default 3daysAgo — the data lags 2-3 days)")
    @click.option("--limit", type=int, default=25, help="Rows, max 25000 (default: 25)")
    @click.option("--start-row", type=int, default=0, help="Row offset for paging")
    @click.option("--type", "search_type", default="web", help="web, image, video, news, discover or googleNews")
    @click.option("--data-state", default="final", help="final (default) or all, which includes fresh partial data")
    @click.option("--filter", "filters", multiple=True, help="dim==value | dim!=value | dim~~substring | dim=~regex (repeatable, ANDed)")
    @account_option
    @compact_option
    def sc_query(site, dimensions, since, until, limit, start_row, search_type,
                 data_state, filters, account, compact):
        """Query search analytics and return flattened rows."""
        import searchconsole_ops
        with _handle_errors(compact):
            result = searchconsole_ops.query(
                site=site, dimensions=dimensions, since=since, until=until,
                limit=limit, start_row=start_row, search_type=search_type,
                data_state=data_state, filters=filters, account=account,
            )
            output_success(result, compact=compact)

    @searchconsole.command(name="sitemaps")
    @click.option("--site", required=True, help="Property, exactly as 'searchconsole sites' returns it")
    @account_option
    @compact_option
    def sc_sitemaps(site, account, compact):
        """List sitemaps registered for a property."""
        import searchconsole_ops
        with _handle_errors(compact):
            output_success(searchconsole_ops.list_sitemaps(site=site, account=account), compact=compact)

    @searchconsole.command(name="inspect")
    @click.option("--site", required=True, help="Property, exactly as 'searchconsole sites' returns it")
    @click.option("--url", required=True, help="The URL to inspect (must be inside the property)")
    @account_option
    @compact_option
    def sc_inspect(site, url, account, compact):
        """Index status for one URL."""
        import searchconsole_ops
        with _handle_errors(compact):
            output_success(
                searchconsole_ops.inspect_url(site=site, url=url, account=account),
                compact=compact,
            )

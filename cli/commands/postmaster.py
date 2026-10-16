"""Google Workspace CLI — Gmail Postmaster Tools commands (read-only)."""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Postmaster Tools commands on the CLI group."""

    @cli_group.group()
    def postmaster():
        """Gmail Postmaster Tools — registered domains, spam rate, authentication, reputation (read-only)."""
        pass

    @postmaster.command(name="domains")
    @account_option
    @compact_option
    def pm_domains(account, compact):
        """List the domains this account registered in Postmaster Tools."""
        import postmaster_ops
        with _handle_errors(compact):
            output_success(postmaster_ops.list_domains(account=account), compact=compact)

    @postmaster.command(name="stats")
    @click.option("--domain", required=True, help="A registered domain, e.g. example.com ('postmaster domains' lists them)")
    @click.option("--since", default="30daysAgo", help="Start: YYYY-MM-DD, today, yesterday or NdaysAgo")
    @click.option("--until", default="yesterday", help="End, inclusive (default: yesterday)")
    @click.option("--metrics", default="spam_rate,dkim,spf,dmarc,delivery_error_rate",
                  help="Comma list: spam_rate, dkim, spf, dmarc, tls_outbound, delivery_error_rate, delivery_errors")
    @account_option
    @compact_option
    def pm_stats(domain, since, until, metrics, account, compact):
        """Daily spam rate, DKIM/SPF/DMARC pass rates, delivery errors and domain reputation.

        One row per day Google reported. Google reports a day only when enough
        mail reached Gmail users, so a missing day is under that threshold.
        """
        import postmaster_ops
        with _handle_errors(compact):
            output_success(
                postmaster_ops.stats(domain=domain, since=since, until=until, metrics=metrics, account=account),
                compact=compact,
            )

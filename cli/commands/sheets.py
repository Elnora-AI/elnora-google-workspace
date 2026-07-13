"""Google Workspace CLI — Sheets commands."""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Sheets commands on the CLI group."""

    @cli_group.group()
    def sheets():
        """Google Sheets operations — read, write, append, list."""
        pass

    @sheets.command()
    @click.argument("spreadsheet_id")
    @click.option("--range", "-r", "range_", default="Sheet1", help="Cell range (e.g., 'Sheet1!A1:F100')")
    @account_option
    @compact_option
    def read(spreadsheet_id, range_, account, compact):
        """Read values from a spreadsheet range."""
        import sheets as sheets_lib
        with _handle_errors(compact):
            result = sheets_lib.read(spreadsheet_id=spreadsheet_id, range=range_, account=account)
            output_success(result, compact=compact)

    @sheets.command()
    @click.argument("spreadsheet_id")
    @click.option("--range", "-r", "range_", required=True, help="Target cell/range")
    @click.option("--value", "-v", required=True, help="Value to write (string or JSON array)")
    @account_option
    @compact_option
    def write(spreadsheet_id, range_, value, account, compact):
        """Write a value to a cell or range."""
        import sheets as sheets_lib
        with _handle_errors(compact):
            result = sheets_lib.write(spreadsheet_id=spreadsheet_id, range=range_, value=value, account=account)
            output_success(result, compact=compact)

    @sheets.command()
    @click.argument("spreadsheet_id")
    @click.option("--values", "-v", required=True, help='JSON array to append, e.g., \'["name","email"]\'')
    @click.option("--range", "-r", "range_", default="Sheet1", help="Target sheet (default: Sheet1)")
    @account_option
    @compact_option
    def append(spreadsheet_id, values, range_, account, compact):
        """Append a row to a spreadsheet."""
        import sheets as sheets_lib
        with _handle_errors(compact):
            result = sheets_lib.append(spreadsheet_id=spreadsheet_id, values=values, range=range_, account=account)
            output_success(result, compact=compact)

    @sheets.command(name="list")
    @account_option
    @compact_option
    def sheets_list(account, compact):
        """List accessible spreadsheets."""
        import sheets as sheets_lib
        with _handle_errors(compact):
            result = sheets_lib.list_spreadsheets(account=account)
            output_success(result, compact=compact)

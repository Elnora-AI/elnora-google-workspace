"""Google Workspace CLI — CRM commands.

Part of the OPTIONAL knowledge-base connector. `crm init` scaffolds an empty CRM
(``contacts.csv`` + ``companies.csv``) under your vault so `gw gmail sync-crm` and
`gw calendar sync-crm` have somewhere to write. Needs only ``vault_path`` in
``.claude/knowledge-base.local.md`` — the file any knowledge-vault-style plugin
writes. Everything no-ops cleanly when no knowledge base is configured.
"""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register CRM commands on the CLI group."""

    @cli_group.group()
    def crm():
        """CRM scaffolding and status for the knowledge-base connector."""
        pass

    @crm.command(name="init")
    @compact_option
    def crm_init(compact):
        """Scaffold an empty CRM (contacts.csv + companies.csv) under the vault.

        Idempotent — existing files are never overwritten.
        """
        import crm as crm_lib
        with _handle_errors(compact):
            result = crm_lib.init_crm()
            result["crm_path"] = str(crm_lib.crm_path())
            output_success(result, compact=compact)

    @crm.command(name="status")
    @compact_option
    def crm_status(compact):
        """Show the resolved CRM location and row counts."""
        import crm as crm_lib
        with _handle_errors(compact):
            contacts_path = crm_lib.contacts_csv_path()
            companies_path = crm_lib.companies_csv_path()
            result = {
                "crm_path": str(crm_lib.crm_path()),
                "contacts_csv": str(contacts_path),
                "contacts_exists": contacts_path.exists(),
                "contacts_count": len(crm_lib.read_contacts_csv()) if contacts_path.exists() else 0,
                "companies_exists": companies_path.exists(),
            }
            output_success(result, compact=compact)

    @crm.command(name="path")
    @compact_option
    def crm_path_cmd(compact):
        """Print the resolved CRM directory path."""
        import crm as crm_lib
        with _handle_errors(compact):
            output_success({"crm_path": str(crm_lib.crm_path())}, compact=compact)

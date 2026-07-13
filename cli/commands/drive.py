"""Google Workspace CLI — Drive commands."""

from __future__ import annotations

import click

from output import output_success, _handle_errors

_EXPORT_FORMATS = ["pdf", "docx", "xlsx", "pptx", "csv", "txt", "md", "html"]


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Drive commands on the CLI group."""

    @cli_group.group()
    def drive():
        """Google Drive — list, get, upload, download, export, folders, sharing.

        Deliberate safety: no permanent delete (trash/untrash only) and no
        anyone-with-link creation (share grants access to specific people).
        FILE arguments accept a bare ID or a full Drive/Docs/Sheets/Slides URL.
        """
        pass

    @drive.command(name="list")
    @click.option("--query", default=None, help="Raw Drive query (q syntax) passthrough. Cannot combine with the filters below.")
    @click.option("--name-contains", default=None, help="Filter: file name contains this substring")
    @click.option("--folder", default=None, help="Filter: direct children of this folder (ID or URL)")
    @click.option("--type", "file_type", type=click.Choice(["folder", "doc", "sheet", "slide", "pdf", "image", "any"]), default="any", help="Filter by file type (default: any)")
    @click.option("--trashed", is_flag=True, help="List trashed files (excluded by default)")
    @click.option("--limit", type=click.IntRange(1, 1000), default=20, help="Max results (default 20, max 1000)")
    @account_option
    @compact_option
    def drive_list(query, name_contains, folder, file_type, trashed, limit, account, compact):
        """List/search Drive files (shared drives included)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.list_files(
                query=query, name_contains=name_contains, folder=folder,
                file_type=file_type, trashed=trashed, limit=limit, account=account,
            )
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @account_option
    @compact_option
    def get(file_id, account, compact):
        """Full metadata for a file. Accepts file ID or full URL."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.get_file(file_id, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("path")
    @click.option("--folder", default=None, help="Destination folder (ID or URL). Defaults to My Drive root.")
    @click.option("--name", default=None, help="Name in Drive (default: local basename)")
    @click.option("--mime", default=None, help="Source MIME type (default: guessed from the filename)")
    @click.option("--convert", is_flag=True, help="Import to the Google-native equivalent (markdown/csv/Office)")
    @account_option
    @compact_option
    def upload(path, folder, name, mime, convert, account, compact):
        """Upload a local file to Drive (resumable)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.upload(
                path, folder=folder, name=name, mime=mime,
                convert=convert, account=account,
            )
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--dest", required=True, help="Destination directory or file path")
    @click.option("--format", "export_format", type=click.Choice(_EXPORT_FORMATS), default=None, help="Export format — required for Google-native files (Docs/Sheets/Slides)")
    @click.option("--force", is_flag=True, help="Overwrite an existing file")
    @account_option
    @compact_option
    def download(file_id, dest, export_format, force, account, compact):
        """Download a file. Google-native files require --format. Accepts ID or URL."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.download(
                file_id, dest, export_format=export_format,
                force=force, account=account,
            )
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--format", "export_format", type=click.Choice(_EXPORT_FORMATS), required=True, help="Export format (validated per source type)")
    @click.option("--dest", default=None, help="Destination directory or file path (default: ./NAME.FORMAT)")
    @click.option("--force", is_flag=True, help="Overwrite an existing file")
    @account_option
    @compact_option
    def export(file_id, export_format, dest, force, account, compact):
        """Export a Google-native file (Doc/Sheet/Slides). Accepts ID or URL."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.export(
                file_id, export_format, dest=dest, force=force, account=account,
            )
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("name")
    @click.option("--parent", default=None, help="Parent folder (ID or URL). Defaults to My Drive root.")
    @account_option
    @compact_option
    def mkdir(name, parent, account, compact):
        """Create a Drive folder."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.mkdir(name, parent=parent, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--to", "to_folder", required=True, help="Destination folder (ID or URL)")
    @account_option
    @compact_option
    def move(file_id, to_folder, account, compact):
        """Move a file into a folder (replaces current parents)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.move(file_id, to_folder, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--name", required=True, help="New file name")
    @account_option
    @compact_option
    def rename(file_id, name, account, compact):
        """Rename a file."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.rename(file_id, name, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--name", default=None, help="Name for the copy (default: 'Copy of ...')")
    @click.option("--folder", default=None, help="Destination folder (ID or URL)")
    @account_option
    @compact_option
    def copy(file_id, name, folder, account, compact):
        """Copy a file, optionally into a folder."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.copy(file_id, name=name, folder=folder, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @account_option
    @compact_option
    def trash(file_id, account, compact):
        """Move a file to the trash (recoverable — permanent delete is deliberately unsupported)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.set_trashed(file_id, True, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @account_option
    @compact_option
    def untrash(file_id, account, compact):
        """Restore a file from the trash."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.set_trashed(file_id, False, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--with", "with_email", required=True, help="Email address to grant access to")
    @click.option("--role", type=click.Choice(["reader", "commenter", "writer"]), required=True, help="Access role")
    @click.option("--notify/--no-notify", default=True, help="Send a notification email (default: notify)")
    @click.option("--message", default=None, help="Custom message in the notification email")
    @account_option
    @compact_option
    def share(file_id, with_email, role, notify, message, account, compact):
        """Share a file with a specific person (anyone-with-link is deliberately unsupported)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.share(
                file_id, with_email, role, notify=notify,
                message=message, account=account,
            )
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @account_option
    @compact_option
    def shares(file_id, account, compact):
        """List a file's permissions (including anyone-with-link entries)."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.list_permissions(file_id, account=account)
            output_success(result, compact=compact)

    @drive.command()
    @click.argument("file_id")
    @click.option("--permission-id", required=True, help="Permission ID (see 'drive shares')")
    @account_option
    @compact_option
    def unshare(file_id, permission_id, account, compact):
        """Remove a permission from a file."""
        import drive as drive_lib
        with _handle_errors(compact):
            result = drive_lib.unshare(file_id, permission_id, account=account)
            output_success(result, compact=compact)

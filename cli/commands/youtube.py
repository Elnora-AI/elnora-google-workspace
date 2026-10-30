"""Google Workspace CLI — YouTube commands (upload; reads go through `gw api call`)."""

from __future__ import annotations

from pathlib import Path

import click

from output import ValidationError, output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register YouTube commands on the CLI group."""

    @cli_group.group()
    def youtube():
        """YouTube — upload a video. Reads run through gw api call (see the gw-youtube skill)."""
        pass

    @youtube.command(name="upload")
    @click.argument("path")
    @click.option("--title", required=True, help="Up to 100 characters")
    @click.option("--description", default=None, help="Up to 5000 bytes")
    @click.option("--description-file", type=click.Path(exists=True, dir_okay=False), default=None,
                  help="Read the description from a UTF-8 file instead")
    @click.option("--privacy", type=click.Choice(["private", "unlisted", "public"]), required=True,
                  help="No default: say who can see it")
    @click.option("--made-for-kids/--not-made-for-kids", "made_for_kids", default=None,
                  help="Required: YouTube's made-for-kids declaration")
    @click.option("--tags", default=None, help="Comma-separated tags")
    @click.option("--category-id", default=None, help="YouTube category id, e.g. 28 = Science & Technology")
    @click.option("--publish-at", default=None,
                  help="ISO 8601 time to go public, e.g. 2026-11-03T15:00:00Z (needs --privacy private)")
    @account_option
    @compact_option
    def yt_upload(path, title, description, description_file, privacy, made_for_kids, tags,
                  category_id, publish_at, account, compact):
        """Upload a video file to the account's channel (resumable)."""
        import youtube_ops
        with _handle_errors(compact):
            if (description is None) == (description_file is None):
                raise ValidationError("Pass exactly one of --description or --description-file.")
            if description_file:
                description = Path(description_file).read_text(encoding="utf-8")
            output_success(
                youtube_ops.upload(
                    path, title=title, description=description, privacy=privacy,
                    made_for_kids=made_for_kids,
                    tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else None,
                    category_id=category_id, publish_at=publish_at, account=account,
                ),
                compact=compact,
            )

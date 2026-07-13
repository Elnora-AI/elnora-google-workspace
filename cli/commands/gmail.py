"""Google Workspace CLI — Gmail commands."""

from __future__ import annotations

import sys

import click

from output import output_success, _handle_errors


def _resolve_body(body: str | None, body_file: str | None) -> str:
    """Return body text from --body or --body-file (stdin when path is '-')."""
    if body_file is not None:
        if body_file == "-":
            return sys.stdin.read()
        with open(body_file, "r", encoding="utf-8") as f:
            return f.read()
    if body is not None:
        return body
    raise click.UsageError("Either --body or --body-file is required.")


def _merge_addresses(values: tuple[str, ...]) -> str | None:
    """Merge repeated --to/--cc flags into one comma-separated string.

    Handles both ``--cc a@b,c@d`` and ``--cc a@b --cc c@d`` (and mixes).
    """
    if not values:
        return None
    parts = []
    for v in values:
        parts.extend(addr.strip() for addr in v.split(",") if addr.strip())
    return ",".join(parts) if parts else None


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Gmail commands on the CLI group."""

    @cli_group.group()
    def gmail():
        """Gmail operations — send, draft, list, get, reply, scan."""
        pass

    @gmail.command()
    @click.option("--to", required=True, multiple=True, help="Recipient email (repeat or comma-separate for multiple)")
    @click.option("--subject", required=True, help="Email subject")
    @click.option("--body", default=None, help="Email body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--cc", multiple=True, help="CC recipient(s) (repeat or comma-separate for multiple)")
    @click.option("--thread-id", "thread_id", default=None, help="Thread ID to reply in (preserves thread)")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def send(to, subject, body, body_file, cc, thread_id, attach, account, compact):
        """Send an email. Use --thread-id to send as a reply in an existing thread."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.send(to=_merge_addresses(to), subject=subject, body=resolved_body, cc=_merge_addresses(cc), thread_id=thread_id, account=account, attachments=attachments)
            output_success(result, compact=compact)

    @gmail.command()
    @click.option("--to", required=True, multiple=True, help="Recipient email (repeat or comma-separate for multiple)")
    @click.option("--subject", required=True, help="Email subject")
    @click.option("--body", default=None, help="Email body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--cc", multiple=True, help="CC recipient(s) (repeat or comma-separate for multiple)")
    @click.option("--thread-id", "thread_id", default=None, help="Thread ID to reply in (preserves thread)")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def draft(to, subject, body, body_file, cc, thread_id, attach, account, compact):
        """Create an email draft. Use --thread-id to draft as a reply in an existing thread."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.draft(to=_merge_addresses(to), subject=subject, body=resolved_body, cc=_merge_addresses(cc), thread_id=thread_id, account=account, attachments=attachments)
            output_success(result, compact=compact)

    @gmail.command(name="list")
    @click.option("--query", "-q", default="", help="Gmail search query (e.g., 'is:unread')")
    @click.option("--limit", "-l", default=20, type=click.IntRange(1, 500), help="Max results (1-500, default: 20)")
    @account_option
    @compact_option
    def list_cmd(query, limit, account, compact):
        """List messages matching a search query."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.list_messages(query=query, limit=limit, account=account)
            output_success(result, compact=compact)

    @gmail.command("list-drafts")
    @click.option("--query", "-q", default="", help="Gmail search query (e.g., 'from:someone@example.com')")
    @click.option("--limit", "-l", default=20, type=click.IntRange(1, 500), help="Max results (1-500, default: 20)")
    @account_option
    @compact_option
    def list_drafts_cmd(query, limit, account, compact):
        """List drafts matching a search query. Each result includes draftId for chaining."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.list_drafts(query=query, limit=limit, account=account)
            output_success(result, compact=compact)

    @gmail.command()
    @click.argument("message_id")
    @account_option
    @compact_option
    def get(message_id, account, compact):
        """Get a message with full body."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.get(message_id=message_id, account=account)
            output_success(result, compact=compact)

    @gmail.command()
    @click.argument("message_id")
    @click.option("--body", default=None, help="Reply body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--to", default=None, help="Override recipient (default: original sender)")
    @click.option("--cc", default=None, help="Override Cc list (default: preserve original Cc). Empty string clears.")
    @click.option("--no-cc", "no_cc", is_flag=True, default=False, help="Force Cc empty (conflicts with --cc).")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def reply(message_id, body, body_file, to, cc, no_cc, attach, account, compact):
        """Reply to a message. Preserves the thread and auto-preserves original Cc recipients."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.reply(
                message_id=message_id,
                body=resolved_body,
                to=to,
                cc=cc,
                no_cc=no_cc,
                account=account,
                attachments=attachments,
            )
            output_success(result, compact=compact)

    @gmail.command("reply-all")
    @click.argument("message_id")
    @click.option("--body", default=None, help="Reply body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--to", default=None, help="Override primary recipient (default: original sender)")
    @click.option("--cc", default=None, help="Override Cc list (default: original To + Cc minus self).")
    @click.option("--no-cc", "no_cc", is_flag=True, default=False, help="Force Cc empty (conflicts with --cc).")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def reply_all_cmd(message_id, body, body_file, to, cc, no_cc, attach, account, compact):
        """Reply-all: send to the original sender with everyone else (original To + Cc, minus self) in Cc."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.reply_all(
                message_id=message_id,
                body=resolved_body,
                to=to,
                cc=cc,
                no_cc=no_cc,
                account=account,
                attachments=attachments,
            )
            output_success(result, compact=compact)

    @gmail.command("draft-reply")
    @click.argument("message_id")
    @click.option("--body", default=None, help="Reply body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--to", default=None, help="Override recipient (useful for follow-ups on sent messages)")
    @click.option("--cc", default=None, help="Override Cc list (default: preserve original Cc). Empty string clears.")
    @click.option("--no-cc", "no_cc", is_flag=True, default=False, help="Force Cc empty (conflicts with --cc).")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def draft_reply(message_id, body, body_file, to, cc, no_cc, attach, account, compact):
        """Create a draft reply to a message. Auto-preserves original Cc. Does NOT send."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.draft_reply(
                message_id=message_id,
                body=resolved_body,
                to=to,
                cc=cc,
                no_cc=no_cc,
                account=account,
                attachments=attachments,
            )
            output_success(result, compact=compact)

    @gmail.command("draft-reply-all")
    @click.argument("message_id")
    @click.option("--body", default=None, help="Reply body (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--to", default=None, help="Override primary recipient (default: original sender)")
    @click.option("--cc", default=None, help="Override Cc list (default: original To + Cc minus self).")
    @click.option("--no-cc", "no_cc", is_flag=True, default=False, help="Force Cc empty (conflicts with --cc).")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def draft_reply_all_cmd(message_id, body, body_file, to, cc, no_cc, attach, account, compact):
        """Draft a reply-all. Same semantics as reply-all, but creates a draft."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.draft_reply_all(
                message_id=message_id,
                body=resolved_body,
                to=to,
                cc=cc,
                no_cc=no_cc,
                account=account,
                attachments=attachments,
            )
            output_success(result, compact=compact)

    @gmail.command("get-draft")
    @click.argument("draft_id")
    @account_option
    @compact_option
    def get_draft(draft_id, account, compact):
        """Get a draft with full body text."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.get_draft(draft_id=draft_id, account=account)
            output_success(result, compact=compact)

    @gmail.command("send-draft")
    @click.argument("draft_id")
    @account_option
    @compact_option
    def send_draft_cmd(draft_id, account, compact):
        """Send an existing draft."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.send_draft(draft_id=draft_id, account=account)
            output_success(result, compact=compact)

    @gmail.command("delete-draft")
    @click.argument("draft_id")
    @account_option
    @compact_option
    def delete_draft_cmd(draft_id, account, compact):
        """Delete a draft. Permanent — Gmail does not trash drafts."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.delete_draft(draft_id=draft_id, account=account)
            output_success(result, compact=compact)

    @gmail.command("attach-to-draft")
    @click.argument("draft_id")
    @click.option("--attach", multiple=True, required=True, help="File path to attach (repeat for multiple)")
    @account_option
    @compact_option
    def attach_to_draft_cmd(draft_id, attach, account, compact):
        """Attach files to an existing draft. Preserves body, subject, recipients, and existing attachments."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.update_draft(
                draft_id=draft_id,
                attachments=list(attach),
                append_attachments=True,
                account=account,
            )
            # Annotate with count for ergonomic output
            result["attached"] = len(attach)
            output_success(result, compact=compact)

    @gmail.command("update-draft")
    @click.argument("draft_id")
    @click.option("--body", default=None, help="New body (plain text). Omit to keep existing.")
    @click.option("--body-file", "body_file", default=None, help="Read body from file (use '-' for stdin)")
    @click.option("--subject", default=None, help="New subject. Omit to keep existing.")
    @click.option("--to", default=None, help="New recipient. Omit to keep existing.")
    @click.option("--cc", default=None, help="New CC recipient(s). Omit to keep existing.")
    @click.option("--attach", multiple=True, help="File path to attach (repeat for multiple). See --append-attachments.")
    @click.option("--append-attachments", "append_attachments", is_flag=True, default=False, help="Keep existing attachments and add new ones (default replaces).")
    @account_option
    @compact_option
    def update_draft_cmd(draft_id, body, body_file, subject, to, cc, attach, append_attachments, account, compact):
        """Update an existing draft. Any field omitted is preserved from the current draft."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            resolved_body = None
            if body is not None or body_file is not None:
                resolved_body = _resolve_body(body, body_file)
            attachments = list(attach) if attach else None
            result = gmail_lib.update_draft(
                draft_id=draft_id,
                body=resolved_body,
                subject=subject,
                to=to,
                cc=cc,
                attachments=attachments,
                append_attachments=append_attachments,
                account=account,
            )
            output_success(result, compact=compact)

    @gmail.command()
    @account_option
    @compact_option
    def labels(account, compact):
        """List all Gmail labels."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.labels(account=account)
            output_success(result, compact=compact)

    @gmail.command()
    @click.argument("message_id")
    @account_option
    @compact_option
    def trash(message_id, account, compact):
        """Move a message to Trash."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.trash(message_id=message_id, account=account)
            output_success(result, compact=compact)

    @gmail.command("download-attachments")
    @click.argument("message_id")
    @click.option("--dest", required=True, help="Destination directory for downloaded files")
    @click.option(
        "--ext", "ext", default=None,
        help="Filter by extension(s), comma-separated (e.g. 'pdf' or 'pdf,docx,xlsx'). Default: all.",
    )
    @account_option
    @compact_option
    def download_attachments(message_id, dest, ext, account, compact):
        """Download attachments from a message to a directory.

        Supports any format Gmail stores as a MIME attachment: pdf, doc/docx,
        xls/xlsx, ppt/pptx, csv, txt, zip, png/jpg, etc. Use --ext to filter.
        """
        import gmail as gmail_lib
        with _handle_errors(compact):
            extensions = [e.strip() for e in ext.split(",")] if ext else None
            result = gmail_lib.download_attachments(
                message_id=message_id, dest=dest, account=account, extensions=extensions,
            )
            output_success(result, compact=compact)

    @gmail.command("get-thread")
    @click.argument("thread_id")
    @account_option
    @compact_option
    def get_thread(thread_id, account, compact):
        """Get all messages in a thread (oldest first)."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            messages = gmail_lib.get_thread(thread_id=thread_id, account=account)
            output_success({"messages": messages, "count": len(messages), "threadId": thread_id}, compact=compact)

    @gmail.command()
    @click.option("--since", default="1d", help="Time window: 1d, 3d, 1w (default: 1d)")
    @account_option
    @compact_option
    def scan(since, account, compact):
        """Scan inbox for recent messages."""
        import gmail as gmail_lib
        with _handle_errors(compact):
            result = gmail_lib.scan(since=since, account=account)
            output_success(result, compact=compact)

    @gmail.command(name="sync-crm")
    @click.option("--lookback-days", default=2, type=int, help="How many days back to scan (default: 2)")
    @click.option("--limit", default=0, type=int, help="Max messages to process (0 = unlimited)")
    @click.option("--dry-run", is_flag=True, help="Show what would change without writing CSV")
    @account_option
    @compact_option
    def sync_crm(lookback_days, limit, dry_run, account, compact):
        """Bump last_contact_date in contacts.csv from recent Gmail activity."""
        import gw_config
        if not gw_config.kb_configured():
            click.echo(click.style(gw_config.KB_NOT_CONFIGURED, fg="yellow"))
            return
        import email_crm_sync
        with _handle_errors(compact):
            result = email_crm_sync.sync(
                lookback_days=lookback_days, limit=limit,
                dry_run=dry_run, account=account,
            )
            output_success(result, compact=compact)

    @gmail.command(name="sync-crm-status")
    @compact_option
    def sync_crm_status(compact):
        """Show last email→CRM sync stats."""
        import gw_config
        if not gw_config.kb_configured():
            click.echo(click.style(gw_config.KB_NOT_CONFIGURED, fg="yellow"))
            return
        import email_crm_sync
        with _handle_errors(compact):
            result = email_crm_sync.status()
            output_success(result, compact=compact)

    @gmail.command(name="sync-crm-install")
    @click.option("--interval-hours", default=2, type=int, help="Hours between runs (default: 2)")
    def sync_crm_install(interval_hours):
        """Schedule the email→CRM sync to run periodically.

        Registered on the host's native scheduler automatically: launchd (macOS),
        Task Scheduler (Windows), or the user crontab (Linux). Falls back to
        printing the exact command if the scheduler can't be driven.
        """
        import scheduler
        scheduler.install("gmail", interval_hours)

    @gmail.command(name="sync-crm-uninstall")
    def sync_crm_uninstall():
        """Remove the scheduled email→CRM sync job from the native scheduler."""
        import scheduler
        scheduler.uninstall("gmail")

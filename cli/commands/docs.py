"""Google Workspace CLI — Docs commands."""

from __future__ import annotations

import sys

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Docs commands on the CLI group."""

    @cli_group.group()
    def docs():
        """Google Docs operations — get, create, import, append, replace."""
        pass

    @docs.command()
    @click.argument("doc_id")
    @account_option
    @compact_option
    def get(doc_id, account, compact):
        """Get a document's text content. Accepts doc ID or full URL."""
        import docs as docs_lib
        with _handle_errors(compact):
            result = docs_lib.get(doc_id=doc_id, account=account)
            output_success(result, compact=compact)

    @docs.command()
    @click.option("--title", required=True, help="Document title")
    @click.option("--body", default=None, help="Initial content (plain text)")
    @click.option("--body-file", "body_file", default=None, help="Read content from file (use '-' for stdin)")
    @click.option("--folder", default=None, help="Drive folder to create the doc in (folder ID or URL). Defaults to My Drive root.")
    @account_option
    @compact_option
    def create(title, body, body_file, folder, account, compact):
        """Create a new Google Doc with optional content."""
        import docs as docs_lib
        with _handle_errors(compact):
            content = _resolve_body(body, body_file)
            result = docs_lib.create(title=title, content=content, folder=folder, account=account)
            output_success(result, compact=compact)

    @docs.command(name="import")
    @click.argument("path")
    @click.option("--title", default=None, help="Doc title (defaults to the filename)")
    @click.option("--doc-id", "doc_id", default=None, help="Replace this existing doc instead of creating a new one (preserves its link). Accepts doc ID or full URL.")
    @click.option("--folder", default=None, help="Drive folder to create the doc in (folder ID or URL). Ignored with --doc-id. Defaults to My Drive root.")
    @click.option("--keep-frontmatter", "keep_frontmatter", is_flag=True, help="Keep YAML frontmatter (stripped by default).")
    @account_option
    @compact_option
    def import_md(path, title, doc_id, folder, keep_frontmatter, account, compact):
        """Import a Markdown file as a native Google Doc — renders headings, bold, lists, tables and links."""
        import docs as docs_lib
        with _handle_errors(compact):
            result = docs_lib.import_markdown(
                path=path, title=title, doc_id=doc_id,
                strip_frontmatter=not keep_frontmatter, folder=folder, account=account,
            )
            output_success(result, compact=compact)

    @docs.command()
    @click.argument("doc_id")
    @click.option("--body", default=None, help="Text to append")
    @click.option("--body-file", "body_file", default=None, help="Read content from file (use '-' for stdin)")
    @account_option
    @compact_option
    def append(doc_id, body, body_file, account, compact):
        """Append text to an existing document. Accepts doc ID or full URL."""
        import docs as docs_lib
        with _handle_errors(compact):
            content = _resolve_body(body, body_file)
            if content is None:
                raise click.UsageError("Either --body or --body-file is required.")
            result = docs_lib.append(doc_id=doc_id, content=content, account=account)
            output_success(result, compact=compact)

    @docs.command()
    @click.argument("doc_id")
    @click.option("--find", required=True, help="Text to find")
    @click.option("--replace-with", "replace_text", required=True, help="Replacement text")
    @account_option
    @compact_option
    def replace(doc_id, find, replace_text, account, compact):
        """Replace all occurrences of a string in a document. Accepts doc ID or full URL."""
        import docs as docs_lib
        with _handle_errors(compact):
            result = docs_lib.replace(doc_id=doc_id, find=find, replace_text=replace_text, account=account)
            output_success(result, compact=compact)


def _resolve_body(body: str | None, body_file: str | None) -> str | None:
    """Return body text from --body or --body-file (stdin when path is '-')."""
    if body_file is not None:
        if body_file == "-":
            return sys.stdin.read()
        with open(body_file, "r", encoding="utf-8") as f:
            return f.read()
    return body

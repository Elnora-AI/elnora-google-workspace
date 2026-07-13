"""Google Workspace CLI — Forms commands (read + write)."""

from __future__ import annotations

import json

import click

from output import _handle_errors, output_success


def _load_spec(path: str) -> tuple[str | None, str | None, list[dict]]:
    """Load a JSON spec file. Returns (title, description, items).

    Accepts either a top-level array of item specs, or a {title, description, items} object.
    """
    with open(path) as f:
        spec = json.load(f)
    if isinstance(spec, list):
        return None, None, spec
    if isinstance(spec, dict):
        items = spec.get("items") or []
        if not isinstance(items, list):
            raise click.UsageError(f"'items' in {path} must be an array.")
        return spec.get("title"), spec.get("description"), items
    raise click.UsageError(
        f"{path} must contain either an array of item specs or an object with 'items'."
    )


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Forms commands on the CLI group."""

    @cli_group.group()
    def forms():
        """Google Forms — read metadata + responses, create and edit forms."""
        pass

    @forms.command()
    @click.argument("form_id")
    @account_option
    @compact_option
    def get(form_id, account, compact):
        """Get form metadata and item summary. Accepts form ID or editor URL."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.get(form_id=form_id, account=account)
            output_success(result, compact=compact)

    @forms.command()
    @click.argument("form_id")
    @click.option(
        "--page-size",
        type=int,
        default=None,
        help="Cap the API page size (default: API default).",
    )
    @click.option(
        "--no-answers",
        is_flag=True,
        default=False,
        help="Return only response metadata (cheaper — skips form lookup and answer mapping).",
    )
    @account_option
    @compact_option
    def responses(form_id, page_size, no_answers, account, compact):
        """List responses on a form. Accepts form ID or editor URL."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.responses_list(
                form_id=form_id,
                page_size=page_size,
                include_answers=not no_answers,
                account=account,
            )
            output_success(result, compact=compact)

    @forms.command()
    @click.argument("form_id")
    @click.argument("response_id")
    @account_option
    @compact_option
    def response(form_id, response_id, account, compact):
        """Get a single response by ID. Accepts form ID or editor URL."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.response_get(
                form_id=form_id, response_id=response_id, account=account
            )
            output_success(result, compact=compact)

    # ----- write side -----

    @forms.command()
    @click.option("--title", default=None, help="Form title (required unless --from-json supplies one).")
    @click.option("--description", default=None, help="Form description (optional).")
    @click.option(
        "--from-json",
        "from_json_path",
        type=click.Path(exists=True, dir_okay=False),
        default=None,
        help="Path to JSON spec — either an array of item specs, or {title, description, items}.",
    )
    @account_option
    @compact_option
    def create(title, description, from_json_path, account, compact):
        """Create a new Google Form. Use --from-json to add items at creation time.

        Item structure is JSON-only because Forms questions are too schema-heavy
        for per-question flags. Supported item types: section, text, paragraph,
        linear_scale, radio, checkbox, dropdown, date.
        """
        import forms as forms_lib

        spec_title = title
        spec_description = description
        items: list[dict] = []
        if from_json_path:
            json_title, json_description, items = _load_spec(from_json_path)
            spec_title = spec_title or json_title
            spec_description = spec_description or json_description

        if not spec_title:
            raise click.UsageError(
                "Title is required (pass --title or include 'title' in --from-json spec)."
            )

        with _handle_errors(compact):
            result = forms_lib.create(
                title=spec_title,
                description=spec_description,
                items=items or None,
                account=account,
            )
            output_success(result, compact=compact)

    @forms.command("add-items")
    @click.argument("form_id")
    @click.option(
        "--from-json",
        "from_json_path",
        type=click.Path(exists=True, dir_okay=False),
        required=True,
        help="Path to JSON spec — array of item specs (or object with 'items').",
    )
    @click.option(
        "--at",
        "at_index",
        type=int,
        default=None,
        help="Insert position (0-based). Default: append at the end.",
    )
    @account_option
    @compact_option
    def add_items(form_id, from_json_path, at_index, account, compact):
        """Add items to an existing form. Items come from a JSON spec file."""
        import forms as forms_lib

        _, _, items = _load_spec(from_json_path)
        if not items:
            raise click.UsageError(f"No items found in {from_json_path}.")

        with _handle_errors(compact):
            result = forms_lib.add_items(
                form_id=form_id, items=items, at_index=at_index, account=account
            )
            output_success(result, compact=compact)

    @forms.command("update-info")
    @click.argument("form_id")
    @click.option("--title", default=None, help="New form title.")
    @click.option("--description", default=None, help="New form description.")
    @account_option
    @compact_option
    def update_info(form_id, title, description, account, compact):
        """Update a form's title and/or description."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.update_info(
                form_id=form_id, title=title, description=description, account=account
            )
            output_success(result, compact=compact)

    @forms.command("update-item")
    @click.argument("form_id")
    @click.argument("index", type=int)
    @click.option(
        "--from-json",
        "from_json_path",
        type=click.Path(exists=True, dir_okay=False),
        required=True,
        help="Path to JSON file containing a single item spec.",
    )
    @account_option
    @compact_option
    def update_item(form_id, index, from_json_path, account, compact):
        """Replace the item at INDEX with a new spec. Preserves the questionId."""
        import forms as forms_lib

        with open(from_json_path) as f:
            spec = json.load(f)
        if isinstance(spec, list):
            if len(spec) != 1:
                raise click.UsageError(
                    f"--from-json must contain a single item spec (got {len(spec)})."
                )
            spec = spec[0]
        if not isinstance(spec, dict):
            raise click.UsageError("--from-json must contain a single item spec object.")

        with _handle_errors(compact):
            result = forms_lib.update_item(
                form_id=form_id, index=index, spec=spec, account=account
            )
            output_success(result, compact=compact)

    @forms.command("move-item")
    @click.argument("form_id")
    @click.argument("from_index", type=int)
    @click.argument("to_index", type=int)
    @account_option
    @compact_option
    def move_item(form_id, from_index, to_index, account, compact):
        """Move the item at FROM_INDEX to TO_INDEX (both 0-based)."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.move_item(
                form_id=form_id, from_index=from_index, to_index=to_index, account=account
            )
            output_success(result, compact=compact)

    @forms.command("delete-item")
    @click.argument("form_id")
    @click.argument("index", type=int)
    @account_option
    @compact_option
    def delete_item(form_id, index, account, compact):
        """Delete the item at the given 0-based index."""
        import forms as forms_lib
        with _handle_errors(compact):
            result = forms_lib.delete_item(form_id=form_id, index=index, account=account)
            output_success(result, compact=compact)

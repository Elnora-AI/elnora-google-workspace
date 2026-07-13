"""Google Workspace CLI — Tasks commands."""

from __future__ import annotations

import click

from output import output_success, _handle_errors


def register(cli_group: click.Group, account_option, compact_option) -> None:
    """Register Tasks commands on the CLI group."""

    @cli_group.group()
    def tasks():
        """Google Tasks operations — create, list, complete."""
        pass

    @tasks.command(name="create")
    @click.option("--title", required=True, help="Task title")
    @click.option("--due", default=None, help="Due date (YYYY-MM-DD)")
    @click.option("--notes", default=None, help="Task notes")
    @account_option
    @compact_option
    def tasks_create(title, due, notes, account, compact):
        """Create a task."""
        import tasks_ops
        with _handle_errors(compact):
            result = tasks_ops.create(title=title, due=due, notes=notes, account=account)
            output_success(result, compact=compact)

    @tasks.command(name="list")
    @account_option
    @compact_option
    def tasks_list(account, compact):
        """List open tasks."""
        import tasks_ops
        with _handle_errors(compact):
            result = tasks_ops.list_tasks(account=account)
            output_success(result, compact=compact)

    @tasks.command()
    @click.argument("task_id")
    @account_option
    @compact_option
    def complete(task_id, account, compact):
        """Mark a task as completed."""
        import tasks_ops
        with _handle_errors(compact):
            result = tasks_ops.complete(task_id=task_id, account=account)
            output_success(result, compact=compact)

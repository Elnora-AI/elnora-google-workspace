"""Google Tasks operations — create, list, complete."""

from __future__ import annotations

from datetime import datetime

from auth import build_service
from googleapiclient.errors import HttpError
from output import NotFoundError, ValidationError, handle_http_error


def _get_service(account: str | None = None):
    return build_service("tasks", "v1", account)


_tasklist_cache: dict[str, str] = {}


def _get_default_tasklist(service, account: str | None = None) -> str:
    """Get the ID of the default task list (cached per account)."""
    cache_key = account or "main"
    if cache_key in _tasklist_cache:
        return _tasklist_cache[cache_key]
    try:
        result = service.tasklists().list(maxResults=1).execute()
    except HttpError as e:
        handle_http_error(e, "tasks list tasklists")
        raise  # unreachable
    items = result.get("items", [])
    if not items:
        try:
            created = service.tasklists().insert(body={"title": "Tasks"}).execute()
        except HttpError as e:
            handle_http_error(e, "tasks create tasklist")
            raise  # unreachable
        _tasklist_cache[cache_key] = created["id"]
        return created["id"]
    _tasklist_cache[cache_key] = items[0]["id"]
    return items[0]["id"]


def create(
    title: str,
    due: str | None = None,
    notes: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a task in the default task list."""
    service = _get_service(account)
    tasklist_id = _get_default_tasklist(service, account)

    task: dict = {"title": title}
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            raise ValidationError(
                f"Invalid --due date: '{due}'.",
                suggestion="Use YYYY-MM-DD format, e.g. --due 2026-03-05",
            )
        task["due"] = f"{due}T00:00:00.000Z"
    if notes:
        task["notes"] = notes

    try:
        result = service.tasks().insert(tasklist=tasklist_id, body=task).execute()
    except HttpError as e:
        handle_http_error(e, "tasks create")
        raise  # unreachable
    return {
        "created": True,
        "id": result["id"],
        "title": result.get("title", title),
        "due": result.get("due"),
        "status": result.get("status", "needsAction"),
    }


def list_tasks(account: str | None = None) -> dict:
    """List all tasks in the default task list."""
    service = _get_service(account)
    tasklist_id = _get_default_tasklist(service, account)

    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            showCompleted=False,
            maxResults=100,
        ).execute()
    except HttpError as e:
        handle_http_error(e, "tasks list")
        raise  # unreachable

    tasks = []
    for t in result.get("items", []):
        tasks.append({
            "id": t["id"],
            "title": t.get("title", ""),
            "due": t.get("due"),
            "notes": t.get("notes", ""),
            "status": t.get("status", "needsAction"),
            "updated": t.get("updated", ""),
        })

    return {"tasks": tasks, "count": len(tasks)}


def complete(task_id: str, account: str | None = None) -> dict:
    """Mark a task as completed."""
    service = _get_service(account)
    tasklist_id = _get_default_tasklist(service, account)

    try:
        task = service.tasks().get(tasklist=tasklist_id, task=task_id).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Task", task_id)
        handle_http_error(e, "tasks get")
        raise  # unreachable

    task["status"] = "completed"
    try:
        result = service.tasks().update(
            tasklist=tasklist_id, task=task_id, body=task
        ).execute()
    except HttpError as e:
        handle_http_error(e, "tasks complete")
        raise  # unreachable

    return {
        "completed": True,
        "id": result["id"],
        "title": result.get("title", ""),
    }

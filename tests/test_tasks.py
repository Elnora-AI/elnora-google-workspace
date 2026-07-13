"""Tests for tasks_ops module — mocked Google API tests for create, list, complete."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import NotFoundError, ValidationError


@pytest.fixture
def mock_tasks_service():
    svc = MagicMock()
    svc.tasklists().list().execute.return_value = {
        "items": [{"id": "tasklist1", "title": "My Tasks"}]
    }
    return svc


@pytest.fixture
def patch_tasks(mock_tasks_service):
    with patch("tasks_ops.build_service", return_value=mock_tasks_service):
        import tasks_ops
        # Clear the tasklist cache between tests
        tasks_ops._tasklist_cache.clear()
        yield tasks_ops, mock_tasks_service


def test_create_basic(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    mock_svc.tasks().insert().execute.return_value = {
        "id": "task1", "title": "Follow up", "status": "needsAction"
    }
    result = tasks_mod.create(title="Follow up")
    assert result["created"] is True
    assert result["id"] == "task1"


def test_create_with_due_date(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    mock_svc.tasks().insert().execute.return_value = {
        "id": "task2", "title": "Task", "due": "2026-03-05T00:00:00.000Z", "status": "needsAction"
    }
    result = tasks_mod.create(title="Task", due="2026-03-05")
    assert result["due"] == "2026-03-05T00:00:00.000Z"


def test_create_invalid_due(patch_tasks):
    tasks_mod, _ = patch_tasks
    with pytest.raises(ValidationError, match="Invalid --due"):
        tasks_mod.create(title="Task", due="not-a-date")


def test_list_tasks_success(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    mock_svc.tasks().list().execute.return_value = {
        "items": [
            {"id": "t1", "title": "Task 1", "status": "needsAction", "updated": "2026-01-01"},
            {"id": "t2", "title": "Task 2", "status": "needsAction", "updated": "2026-01-02"},
        ]
    }
    result = tasks_mod.list_tasks()
    assert result["count"] == 2


def test_list_tasks_empty(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    mock_svc.tasks().list().execute.return_value = {"items": []}
    result = tasks_mod.list_tasks()
    assert result["count"] == 0


def test_complete_success(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    mock_svc.tasks().get().execute.return_value = {
        "id": "t1", "title": "Task 1", "status": "needsAction"
    }
    mock_svc.tasks().update().execute.return_value = {
        "id": "t1", "title": "Task 1", "status": "completed"
    }
    result = tasks_mod.complete(task_id="t1")
    assert result["completed"] is True


def test_complete_not_found(patch_tasks):
    tasks_mod, mock_svc = patch_tasks
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 404
    mock_svc.tasks().get().execute.side_effect = HttpError(resp, b'{}')
    with pytest.raises(NotFoundError, match="Task"):
        tasks_mod.complete(task_id="nonexistent")

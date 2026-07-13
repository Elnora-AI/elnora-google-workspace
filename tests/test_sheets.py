"""Tests for sheets module — mocked Google API tests for read, write, append, list."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import NotFoundError, ValidationError


@pytest.fixture
def mock_sheets_service():
    service = MagicMock()
    return service


@pytest.fixture
def mock_drive_service():
    service = MagicMock()
    return service


@pytest.fixture
def patch_sheets(mock_sheets_service, mock_drive_service):
    def _mock_build(api, version, account=None):
        if api == "sheets":
            return mock_sheets_service
        elif api == "drive":
            return mock_drive_service
        return MagicMock()

    with patch("sheets.build_service", side_effect=_mock_build):
        import sheets
        yield sheets, mock_sheets_service, mock_drive_service


def test_read_success(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    mock_svc.spreadsheets().values().get().execute.return_value = {
        "range": "Sheet1!A1:F10",
        "values": [["name", "email"], ["Alice", "alice@test.com"]],
    }
    result = sheets_mod.read("sheet123", range="Sheet1!A1:F10")
    assert result["count"] == 2
    assert result["rows"][0] == ["name", "email"]


def test_read_empty(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    mock_svc.spreadsheets().values().get().execute.return_value = {
        "range": "Sheet1",
        "values": [],
    }
    result = sheets_mod.read("sheet123")
    assert result["count"] == 0


def test_read_404(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 404
    mock_svc.spreadsheets().values().get().execute.side_effect = HttpError(resp, b'{}')
    with pytest.raises(NotFoundError, match="Spreadsheet"):
        sheets_mod.read("nonexistent")


def test_write_string_value(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    mock_svc.spreadsheets().values().update().execute.return_value = {
        "updatedRange": "Sheet1!E2",
        "updatedCells": 1,
    }
    result = sheets_mod.write("sheet123", range="Sheet1!E2", value="bounced")
    assert result["updated"] is True
    assert result["updatedCells"] == 1


def test_write_json_array(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    mock_svc.spreadsheets().values().update().execute.return_value = {
        "updatedRange": "Sheet1!A1:C1",
        "updatedCells": 3,
    }
    result = sheets_mod.write("sheet123", range="Sheet1!A1:C1", value='["a","b","c"]')
    assert result["updated"] is True


def test_append_valid_json(patch_sheets):
    sheets_mod, mock_svc, _ = patch_sheets
    mock_svc.spreadsheets().values().append().execute.return_value = {
        "updates": {"updatedRange": "Sheet1!A5:C5"},
    }
    result = sheets_mod.append("sheet123", values='["Alice","alice@test.com","pending"]')
    assert result["appended"] is True


def test_append_invalid_json(patch_sheets):
    sheets_mod, _, _ = patch_sheets
    with pytest.raises(ValidationError, match="Invalid --values"):
        sheets_mod.append("sheet123", values="not json")


def test_list_spreadsheets(patch_sheets):
    sheets_mod, _, mock_drive = patch_sheets
    mock_drive.files().list().execute.return_value = {
        "files": [
            {"id": "s1", "name": "Contacts", "modifiedTime": "2026-01-01T00:00:00Z"},
        ]
    }
    result = sheets_mod.list_spreadsheets()
    assert result["count"] == 1
    assert result["spreadsheets"][0]["name"] == "Contacts"

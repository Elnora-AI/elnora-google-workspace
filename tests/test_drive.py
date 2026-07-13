"""Tests for the drive module — ID parsing, query building, download/export/share safety."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import drive
from output import ValidationError


FILE_ID = "1rUv-68UbDsi30DuCh30Z0EjdDACSQ_GS"


# ---------------------------------------------------------------------------
# URL / ID extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (f"https://docs.google.com/document/d/{FILE_ID}/edit", FILE_ID),
        (f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit#gid=0", FILE_ID),
        (f"https://docs.google.com/presentation/d/{FILE_ID}/edit", FILE_ID),
        (f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing", FILE_ID),
        (f"https://drive.google.com/drive/folders/{FILE_ID}", FILE_ID),
        (f"https://drive.google.com/drive/u/0/folders/{FILE_ID}?usp=sharing", FILE_ID),
        (f"https://drive.google.com/open?id={FILE_ID}", FILE_ID),
        (FILE_ID, FILE_ID),  # bare ID passes through
    ],
)
def test_extract_file_id(value, expected):
    assert drive.extract_file_id(value) == expected


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

def test_build_query_default_excludes_trash():
    assert drive.build_query() == "trashed = false"


def test_build_query_trashed_flag():
    assert drive.build_query(trashed=True) == "trashed = true"


def test_build_query_ergonomic_filters():
    q = drive.build_query(
        name_contains="report",
        folder=f"https://drive.google.com/drive/folders/{FILE_ID}",
        file_type="sheet",
    )
    assert q == (
        f"name contains 'report' and '{FILE_ID}' in parents and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )


def test_build_query_image_uses_contains():
    assert "mimeType contains 'image/'" in drive.build_query(file_type="image")


def test_build_query_escapes_quotes():
    q = drive.build_query(name_contains="it's")
    assert "name contains 'it\\'s'" in q


def test_build_query_raw_passthrough():
    raw = "name contains 'x' and starred = true"
    assert drive.build_query(raw) == raw


def test_build_query_rejects_raw_plus_filters():
    with pytest.raises(ValidationError):
        drive.build_query("name contains 'x'", name_contains="x")


def test_list_files_rejects_bad_limit():
    with pytest.raises(ValidationError):
        drive.list_files(limit=0)
    with pytest.raises(ValidationError):
        drive.list_files(limit=1001)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_list_files_paginates_and_truncates():
    service = MagicMock()
    files_mock = service.files.return_value
    page1 = {
        "files": [{"id": f"f{i}", "name": f"n{i}"} for i in range(100)],
        "nextPageToken": "TOK",
    }
    page2 = {"files": [{"id": "f100"}, {"id": "f101"}]}
    files_mock.list.return_value.execute.side_effect = [page1, page2]

    with patch("drive.build_service", return_value=service):
        result = drive.list_files(limit=101)

    assert result["count"] == 101
    assert result["files"][-1]["id"] == "f100"

    calls = files_mock.list.call_args_list
    assert len(calls) == 2
    first, second = calls[0].kwargs, calls[1].kwargs
    assert first["pageSize"] == 100 and "pageToken" not in first
    assert second["pageSize"] == 1 and second["pageToken"] == "TOK"
    for kwargs in (first, second):
        assert kwargs["supportsAllDrives"] is True
        assert kwargs["includeItemsFromAllDrives"] is True


def test_list_files_stops_without_next_page():
    service = MagicMock()
    files_mock = service.files.return_value
    files_mock.list.return_value.execute.side_effect = [{"files": [{"id": "f1"}]}]

    with patch("drive.build_service", return_value=service):
        result = drive.list_files(limit=50)

    assert result["count"] == 1
    assert len(files_mock.list.call_args_list) == 1


# ---------------------------------------------------------------------------
# Download / export routing + overwrite safety
# ---------------------------------------------------------------------------

def _service_with_meta(meta: dict) -> MagicMock:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = meta
    return service


def test_download_refuses_overwrite_without_force(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"old")
    service = _service_with_meta(
        {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"}
    )
    with patch("drive.build_service", return_value=service):
        with pytest.raises(ValidationError, match="Refusing to overwrite"):
            drive.download("f1", str(tmp_path))
    service.files.return_value.get_media.assert_not_called()


def test_download_overwrites_with_force(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"old")
    service = _service_with_meta(
        {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"}
    )
    with patch("drive.build_service", return_value=service), \
         patch("drive.MediaIoBaseDownload") as dl:
        dl.return_value.next_chunk.side_effect = [(None, True)]
        result = drive.download("f1", str(tmp_path), force=True)
    assert result["downloaded"] is True
    assert result["path"] == str(tmp_path / "report.pdf")


def test_download_binary_routes_to_get_media(tmp_path):
    service = _service_with_meta(
        {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"}
    )
    with patch("drive.build_service", return_value=service), \
         patch("drive.MediaIoBaseDownload") as dl:
        dl.return_value.next_chunk.side_effect = [(None, True)]
        drive.download("f1", str(tmp_path))
    service.files.return_value.get_media.assert_called_once_with(
        fileId="f1", supportsAllDrives=True
    )
    service.files.return_value.export_media.assert_not_called()


def test_download_native_requires_format(tmp_path):
    service = _service_with_meta(
        {"id": "d1", "name": "Doc", "mimeType": "application/vnd.google-apps.document"}
    )
    with patch("drive.build_service", return_value=service):
        with pytest.raises(ValidationError, match="--format is required"):
            drive.download("d1", str(tmp_path))


def test_download_native_routes_to_export_media(tmp_path):
    service = _service_with_meta(
        {"id": "d1", "name": "Doc", "mimeType": "application/vnd.google-apps.document"}
    )
    with patch("drive.build_service", return_value=service), \
         patch("drive.MediaIoBaseDownload") as dl:
        dl.return_value.next_chunk.side_effect = [(None, True)]
        result = drive.download("d1", str(tmp_path), export_format="pdf")
    service.files.return_value.export_media.assert_called_once_with(
        fileId="d1", mimeType="application/pdf"
    )
    service.files.return_value.get_media.assert_not_called()
    assert result["exported"] is True
    assert result["path"] == str(tmp_path / "Doc.pdf")


def test_download_binary_rejects_format(tmp_path):
    service = _service_with_meta(
        {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"}
    )
    with patch("drive.build_service", return_value=service):
        with pytest.raises(ValidationError, match="Google-native"):
            drive.download("f1", str(tmp_path), export_format="pdf")


def test_export_validates_format_per_source_type(tmp_path):
    service = _service_with_meta(
        {"id": "d1", "name": "Doc", "mimeType": "application/vnd.google-apps.document"}
    )
    with patch("drive.build_service", return_value=service):
        with pytest.raises(ValidationError, match="not valid for"):
            drive.export("d1", "xlsx", str(tmp_path))


def test_export_rejects_non_native_file(tmp_path):
    service = _service_with_meta(
        {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"}
    )
    with patch("drive.build_service", return_value=service):
        with pytest.raises(ValidationError, match="not a Google-native file"):
            drive.export("f1", "pdf", str(tmp_path))


def test_export_spreadsheet_csv(tmp_path):
    service = _service_with_meta(
        {"id": "s1", "name": "Data", "mimeType": "application/vnd.google-apps.spreadsheet"}
    )
    with patch("drive.build_service", return_value=service), \
         patch("drive.MediaIoBaseDownload") as dl:
        dl.return_value.next_chunk.side_effect = [(None, True)]
        result = drive.export("s1", "csv", str(tmp_path))
    service.files.return_value.export_media.assert_called_once_with(
        fileId="s1", mimeType="text/csv"
    )
    assert result["path"] == str(tmp_path / "Data.csv")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def test_upload_convert_markdown_to_doc(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# Hi\n", encoding="utf-8")
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "f1", "name": "note.md",
    }
    with patch("drive.build_service", return_value=service), \
         patch("drive.MediaFileUpload") as media:
        drive.upload(str(md), convert=True, folder=f"https://drive.google.com/drive/folders/{FILE_ID}")

    _, kwargs = service.files.return_value.create.call_args
    assert kwargs["body"]["mimeType"] == "application/vnd.google-apps.document"
    assert kwargs["body"]["parents"] == [FILE_ID]
    assert media.call_args.kwargs["mimetype"] == "text/markdown"
    assert media.call_args.kwargs["resumable"] is True


def test_upload_convert_rejects_unknown_type(tmp_path):
    blob = tmp_path / "data.bin"
    blob.write_bytes(b"\x00")
    with pytest.raises(ValidationError, match="--convert does not support"):
        drive.upload(str(blob), convert=True)


def test_upload_missing_file():
    with pytest.raises(ValidationError, match="File not found"):
        drive.upload("/nonexistent/file.txt")


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------

def test_share_rejects_invalid_role():
    with pytest.raises(ValidationError, match="Invalid role"):
        drive.share("f1", "a@example.com", "owner")


def test_share_rejects_invalid_email():
    with pytest.raises(ValidationError, match="Invalid email"):
        drive.share("f1", "not-an-email", "reader")


def test_share_message_requires_notify():
    with pytest.raises(ValidationError, match="--message requires"):
        drive.share("f1", "a@example.com", "reader", notify=False, message="hi")


def test_share_builds_user_permission():
    service = MagicMock()
    service.permissions.return_value.create.return_value.execute.return_value = {
        "id": "p1", "type": "user", "role": "writer", "emailAddress": "a@example.com",
    }
    with patch("drive.build_service", return_value=service):
        result = drive.share("f1", "a@example.com", "writer", message="see attached")

    _, kwargs = service.permissions.return_value.create.call_args
    assert kwargs["body"] == {"type": "user", "role": "writer", "emailAddress": "a@example.com"}
    assert kwargs["sendNotificationEmail"] is True
    assert kwargs["emailMessage"] == "see attached"
    assert result["shared"] is True


def test_share_no_notify_omits_message_param():
    service = MagicMock()
    service.permissions.return_value.create.return_value.execute.return_value = {"id": "p1"}
    with patch("drive.build_service", return_value=service):
        drive.share("f1", "a@example.com", "reader", notify=False)
    _, kwargs = service.permissions.return_value.create.call_args
    assert kwargs["sendNotificationEmail"] is False
    assert "emailMessage" not in kwargs


# ---------------------------------------------------------------------------
# Organize
# ---------------------------------------------------------------------------

def test_move_replaces_parents():
    service = MagicMock()
    files_mock = service.files.return_value
    files_mock.get.return_value.execute.return_value = {"parents": ["oldFolder"]}
    files_mock.update.return_value.execute.return_value = {
        "id": "f1", "name": "n", "parents": [FILE_ID],
    }
    with patch("drive.build_service", return_value=service):
        result = drive.move("f1", FILE_ID)

    _, kwargs = files_mock.update.call_args
    assert kwargs["addParents"] == FILE_ID
    assert kwargs["removeParents"] == "oldFolder"
    assert result["moved"] is True


def test_trash_sets_trashed_flag():
    service = MagicMock()
    service.files.return_value.update.return_value.execute.return_value = {
        "id": "f1", "name": "n", "trashed": True,
    }
    with patch("drive.build_service", return_value=service):
        result = drive.set_trashed("f1", True)
    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {"trashed": True}
    assert result["trashed"] is True

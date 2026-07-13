"""Tests for docs module — folder placement on create/import and ID parsing."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import docs


FOLDER_ID = "1rUv-68UbDsi30DuCh30Z0EjdDACSQ_GS"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://drive.google.com/drive/folders/" + FOLDER_ID, FOLDER_ID),
        ("https://drive.google.com/drive/u/0/folders/" + FOLDER_ID + "?usp=sharing", FOLDER_ID),
        ("https://drive.google.com/open?id=" + FOLDER_ID, FOLDER_ID),
        (FOLDER_ID, FOLDER_ID),  # bare ID passes through
    ],
)
def test_extract_folder_id(value, expected):
    assert docs._extract_folder_id(value) == expected


def test_import_markdown_with_folder_sets_parents(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# Hello\n\nBody.\n", encoding="utf-8")

    drive = MagicMock()
    drive.files().create().execute.return_value = {
        "id": "doc123", "name": "note", "webViewLink": "https://docs.google.com/document/d/doc123/edit",
    }

    with patch("docs.build_service", return_value=drive):
        docs.import_markdown(path=str(md), folder="https://drive.google.com/drive/folders/" + FOLDER_ID)

    # The create call must carry the folder as a parent.
    _, kwargs = drive.files().create.call_args
    assert kwargs["body"]["parents"] == [FOLDER_ID]


def test_import_markdown_without_folder_has_no_parents(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# Hello\n", encoding="utf-8")

    drive = MagicMock()
    drive.files().create().execute.return_value = {
        "id": "doc123", "name": "note", "webViewLink": "https://docs.google.com/document/d/doc123/edit",
    }

    with patch("docs.build_service", return_value=drive):
        docs.import_markdown(path=str(md))

    _, kwargs = drive.files().create.call_args
    assert "parents" not in kwargs["body"]


def test_create_with_folder_moves_doc():
    docs_service = MagicMock()
    docs_service.documents().create().execute.return_value = {"documentId": "doc123", "title": "T"}

    drive = MagicMock()
    drive.files().get().execute.return_value = {"parents": ["rootFolder"]}

    def _build(api, version, account=None):
        return docs_service if api == "docs" else drive

    with patch("docs.build_service", side_effect=_build):
        docs.create(title="T", folder=FOLDER_ID)

    _, kwargs = drive.files().update.call_args
    assert kwargs["addParents"] == FOLDER_ID
    assert kwargs["removeParents"] == "rootFolder"
    assert kwargs["fileId"] == "doc123"

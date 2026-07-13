"""Google Docs operations — get, create, append, replace.

All functions return dicts. Minimal wrapper around the Google Docs API v1.
"""

from __future__ import annotations

import re
from pathlib import Path

from auth import build_service
from googleapiclient.errors import HttpError
from output import NotFoundError, ValidationError, handle_http_error


def _get_service(account: str | None = None):
    return build_service("docs", "v1", account)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (--- ... ---) if present."""
    if text.startswith("---"):
        match = re.match(r"^---\r?\n.*?\r?\n---\r?\n", text, re.DOTALL)
        if match:
            return text[match.end():].lstrip("\n")
    return text


def _extract_doc_id(doc_id_or_url: str) -> str:
    """Extract document ID from a Google Docs URL or return as-is if already an ID."""
    # Match URLs like https://docs.google.com/document/d/XXXXX/edit
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", doc_id_or_url)
    if match:
        return match.group(1)
    return doc_id_or_url


def _extract_folder_id(folder_id_or_url: str) -> str:
    """Extract a Drive folder ID from a folder URL or return as-is if already an ID."""
    # Match URLs like https://drive.google.com/drive/folders/XXXXX or .../u/0/folders/XXXXX
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", folder_id_or_url)
    if match:
        return match.group(1)
    # Match ?id=XXXX / open?id=XXXX style links
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", folder_id_or_url)
    if match:
        return match.group(1)
    return folder_id_or_url


def _move_to_folder(doc_id: str, folder: str, account: str | None = None) -> None:
    """Move an existing file into a Drive folder (replacing its current parents)."""
    drive = build_service("drive", "v3", account)
    folder_id = _extract_folder_id(folder)
    current = drive.files().get(
        fileId=doc_id, fields="parents", supportsAllDrives=True,
    ).execute()
    previous = ",".join(current.get("parents", []))
    drive.files().update(
        fileId=doc_id, addParents=folder_id, removeParents=previous,
        fields="id,parents", supportsAllDrives=True,
    ).execute()


def _get_end_index(doc: dict) -> int:
    """Get the end index of the document body (insert point for appending)."""
    body = doc.get("body", {}).get("content", [])
    if not body:
        return 1
    last = body[-1]
    return last.get("endIndex", 1) - 1


def get(doc_id: str, account: str | None = None) -> dict:
    """Get a document's content and metadata."""
    service = _get_service(account)
    doc_id = _extract_doc_id(doc_id)
    try:
        doc = service.documents().get(documentId=doc_id).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Document", doc_id)
        handle_http_error(e, "docs get")
        raise

    # Extract plain text from body
    body = doc.get("body", {}).get("content", [])
    text_parts = []
    for element in body:
        paragraph = element.get("paragraph")
        if paragraph:
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if text_run:
                    text_parts.append(text_run.get("content", ""))

    return {
        "documentId": doc.get("documentId"),
        "title": doc.get("title"),
        "text": "".join(text_parts),
        "revisionId": doc.get("revisionId"),
    }


def create(
    title: str,
    content: str | None = None,
    folder: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new Google Doc with optional initial content.

    When ``folder`` (a Drive folder ID or URL) is given, the new doc is moved
    into that folder instead of landing in My Drive root.
    """
    service = _get_service(account)
    try:
        doc = service.documents().create(body={"title": title}).execute()
    except HttpError as e:
        handle_http_error(e, "docs create")
        raise

    doc_id = doc["documentId"]

    if content:
        requests = [{"insertText": {"location": {"index": 1}, "text": content}}]
        try:
            service.documents().batchUpdate(
                documentId=doc_id, body={"requests": requests}
            ).execute()
        except HttpError as e:
            handle_http_error(e, "docs create (insert content)")
            raise

    if folder:
        try:
            _move_to_folder(doc_id, folder, account)
        except HttpError as e:
            handle_http_error(e, "docs create (move to folder)")
            raise

    return {
        "documentId": doc_id,
        "title": doc["title"],
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


def import_markdown(
    path: str,
    title: str | None = None,
    doc_id: str | None = None,
    strip_frontmatter: bool = True,
    folder: str | None = None,
    account: str | None = None,
) -> dict:
    """Create (or replace) a Google Doc by import-converting a Markdown file.

    Google Docs renders Markdown — headings, bold/italic, bullet and numbered
    lists, pipe tables, and links — into native Doc formatting on upload, which
    plain-text `create` does not. If ``doc_id`` is given, that existing doc's
    content is replaced and its ID/link are preserved; otherwise a new doc is
    created. When ``folder`` (a Drive folder ID or URL) is given on creation,
    the new doc lands directly in that folder. YAML frontmatter is stripped by
    default.
    """
    from googleapiclient.http import MediaInMemoryUpload

    drive = build_service("drive", "v3", account)
    file_path = Path(path)
    if not file_path.is_file():
        raise ValidationError(f"File not found: {path}")

    body = file_path.read_text(encoding="utf-8")
    if strip_frontmatter:
        body = _strip_frontmatter(body)

    media = MediaInMemoryUpload(
        body.encode("utf-8"), mimetype="text/markdown", resumable=False
    )

    try:
        if doc_id:
            doc_id = _extract_doc_id(doc_id)
            meta = {"name": title} if title else {}
            result = drive.files().update(
                fileId=doc_id, body=meta, media_body=media,
                fields="id,name,webViewLink", supportsAllDrives=True,
            ).execute()
        else:
            meta = {
                "name": title or file_path.stem,
                "mimeType": "application/vnd.google-apps.document",
            }
            if folder:
                meta["parents"] = [_extract_folder_id(folder)]
            result = drive.files().create(
                body=meta, media_body=media,
                fields="id,name,webViewLink", supportsAllDrives=True,
            ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if doc_id and resp is not None and resp.status == 404:
            raise NotFoundError("Document", doc_id)
        handle_http_error(e, "docs import")
        raise

    return {
        "documentId": result["id"],
        "title": result["name"],
        "url": result["webViewLink"],
    }


def append(doc_id: str, content: str, account: str | None = None) -> dict:
    """Append text to the end of an existing document."""
    service = _get_service(account)
    doc_id = _extract_doc_id(doc_id)

    try:
        doc = service.documents().get(documentId=doc_id).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Document", doc_id)
        handle_http_error(e, "docs append (get)")
        raise

    end_index = _get_end_index(doc)
    insert_text = "\n" + content if end_index > 1 else content

    requests = [{"insertText": {"location": {"index": end_index}, "text": insert_text}}]
    try:
        service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()
    except HttpError as e:
        handle_http_error(e, "docs append")
        raise

    return {
        "documentId": doc_id,
        "appended": True,
        "charactersInserted": len(insert_text),
    }


def replace(doc_id: str, find: str, replace_text: str, account: str | None = None) -> dict:
    """Replace all occurrences of a string in a document."""
    service = _get_service(account)
    doc_id = _extract_doc_id(doc_id)

    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": True},
                "replaceText": replace_text,
            }
        }
    ]

    try:
        result = service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Document", doc_id)
        handle_http_error(e, "docs replace")
        raise

    replies = result.get("replies", [])
    occurrences = 0
    if replies:
        occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)

    return {
        "documentId": doc_id,
        "replaced": True,
        "occurrencesChanged": occurrences,
    }

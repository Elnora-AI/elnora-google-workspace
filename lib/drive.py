"""Google Drive operations — list, get, upload, download, export, folders, sharing.

All functions return dicts. Curated wrapper around the Drive API v3.
Every call is shared-drive aware (supportsAllDrives).

Deliberate safety stances:
- No permanent delete — only ``trash``/``untrash`` (recoverable in the UI).
- No anyone-with-link permission creation — grants go to specific people only.
"""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path

from auth import build_service
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from output import (
    CliError,
    NotFoundError,
    ValidationError,
    handle_http_error,
    validate_email,
)

FOLDER_MIME = "application/vnd.google-apps.folder"
_GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."

# --type filter → Drive mimeType (image is a `contains` clause, see build_query)
TYPE_MIME = {
    "folder": FOLDER_MIME,
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slide": "application/vnd.google-apps.presentation",
    "pdf": "application/pdf",
}

# --convert on upload: source MIME → Google-native import target
CONVERT_TARGETS = {
    "text/markdown": "application/vnd.google-apps.document",
    "text/csv": "application/vnd.google-apps.spreadsheet",
    "application/msword": "application/vnd.google-apps.document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "application/vnd.google-apps.document",
    "application/vnd.ms-excel": "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        "application/vnd.google-apps.spreadsheet",
    "application/vnd.ms-powerpoint": "application/vnd.google-apps.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        "application/vnd.google-apps.presentation",
}

# --format on download/export: extension → export MIME
EXPORT_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
}

# Valid export formats per Google-native source type
EXPORTABLE = {
    "application/vnd.google-apps.document": ["pdf", "docx", "txt", "md", "html"],
    "application/vnd.google-apps.spreadsheet": ["pdf", "xlsx", "csv"],
    "application/vnd.google-apps.presentation": ["pdf", "pptx", "txt"],
}

SHARE_ROLES = ("reader", "commenter", "writer")

LIST_FIELDS = "id,name,mimeType,modifiedTime,size,webViewLink,parents"
GET_FIELDS = (
    "id,name,mimeType,description,size,createdTime,modifiedTime,"
    "webViewLink,webContentLink,parents,owners(displayName,emailAddress),"
    "lastModifyingUser(displayName,emailAddress),shared,trashed,starred,"
    "capabilities(canEdit,canShare,canDownload),exportLinks"
)


def _get_service(account: str | None = None):
    return build_service("drive", "v3", account)


# ---------------------------------------------------------------------------
# ID extraction + error handling
# ---------------------------------------------------------------------------

_URL_ID_PATTERNS = (
    r"/(?:document|spreadsheets|presentation|file)/d/([a-zA-Z0-9_-]+)",
    r"/folders/([a-zA-Z0-9_-]+)",
    r"[?&]id=([a-zA-Z0-9_-]+)",
)


def extract_file_id(file_id_or_url: str) -> str:
    """Extract a file/folder ID from a Drive/Docs/Sheets/Slides URL, or pass through."""
    for pattern in _URL_ID_PATTERNS:
        match = re.search(pattern, file_id_or_url)
        if match:
            return match.group(1)
    return file_id_or_url


def _handle_drive_http_error(e, context: str) -> None:
    """Drive-specific 403 handling (scope gap → re-auth hint), else shared handler."""
    resp = getattr(e, "resp", None)
    if resp is not None and resp.status == 403:
        detail = ""
        try:
            detail = json.loads(getattr(e, "content", b"") or b"{}").get(
                "error", {}).get("message", "")
        except Exception:
            pass
        if "insufficient" in detail.lower() or "scope" in detail.lower():
            raise CliError(
                f"Drive scope missing during {context}: {detail}",
                suggestion=(
                    "The stored token lacks full Drive access (it may only have "
                    "drive.file). Re-authenticate with: gw auth login --scopes drive "
                    "(include the other services you use, e.g. "
                    "--scopes gmail,calendar,sheets,docs,tasks,forms,drive)."
                ),
                code="INSUFFICIENT_SCOPE",
            ) from e
    handle_http_error(e, context)


def _get_meta(service, file_id: str, fields: str, context: str) -> dict:
    """files().get with 404 → NotFoundError. Always shared-drive aware."""
    try:
        return service.files().get(
            fileId=file_id, fields=fields, supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, context)
        raise  # unreachable


# ---------------------------------------------------------------------------
# List / search
# ---------------------------------------------------------------------------

def _q_escape(value: str) -> str:
    """Escape a string literal for the Drive q syntax."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_query(
    query: str | None = None,
    *,
    name_contains: str | None = None,
    folder: str | None = None,
    file_type: str | None = None,
    trashed: bool = False,
) -> str:
    """Build a Drive q string from a raw query OR ergonomic filters."""
    if query:
        if name_contains or folder or (file_type and file_type != "any") or trashed:
            raise ValidationError(
                "--query cannot be combined with --name-contains/--folder/--type/--trashed.",
                suggestion="Put everything in the raw query, e.g. --query \"name contains 'x' and trashed = false\"",
            )
        return query
    if file_type and file_type != "any" and file_type != "image" and file_type not in TYPE_MIME:
        raise ValidationError(
            f"Invalid --type '{file_type}'.",
            suggestion=f"Valid types: {', '.join([*TYPE_MIME, 'image', 'any'])}",
        )
    clauses: list[str] = []
    if name_contains:
        clauses.append(f"name contains '{_q_escape(name_contains)}'")
    if folder:
        clauses.append(f"'{extract_file_id(folder)}' in parents")
    if file_type == "image":
        clauses.append("mimeType contains 'image/'")
    elif file_type and file_type != "any":
        clauses.append(f"mimeType = '{TYPE_MIME[file_type]}'")
    clauses.append("trashed = true" if trashed else "trashed = false")
    return " and ".join(clauses)


def list_files(
    query: str | None = None,
    *,
    name_contains: str | None = None,
    folder: str | None = None,
    file_type: str | None = None,
    trashed: bool = False,
    limit: int = 20,
    account: str | None = None,
) -> dict:
    """List/search Drive files (paginated, shared drives included)."""
    if not 1 <= limit <= 1000:
        raise ValidationError("--limit must be between 1 and 1000.")
    q = build_query(
        query, name_contains=name_contains, folder=folder,
        file_type=file_type, trashed=trashed,
    )
    service = _get_service(account)
    files: list[dict] = []
    page_token = None
    while len(files) < limit:
        kwargs: dict = {
            "q": q,
            "fields": f"nextPageToken,files({LIST_FIELDS})",
            "orderBy": "modifiedTime desc",
            "pageSize": min(limit - len(files), 100),
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            result = service.files().list(**kwargs).execute()
        except HttpError as e:
            _handle_drive_http_error(e, "drive list")
            raise  # unreachable
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    files = files[:limit]
    return {"files": files, "count": len(files), "query": q}


# ---------------------------------------------------------------------------
# Get / upload / download / export
# ---------------------------------------------------------------------------

def get_file(file_id: str, account: str | None = None) -> dict:
    """Full metadata for a file. Accepts an ID or a Drive/Docs/Sheets/Slides URL."""
    service = _get_service(account)
    return _get_meta(service, extract_file_id(file_id), GET_FIELDS, "drive get")


def upload(
    path: str,
    *,
    folder: str | None = None,
    name: str | None = None,
    mime: str | None = None,
    convert: bool = False,
    account: str | None = None,
) -> dict:
    """Upload a local file (resumable). --convert imports to the Google-native type."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValidationError(f"File not found: {path}")
    src_mime = mime or mimetypes.guess_type(file_path.name)[0]
    if not src_mime and file_path.suffix.lower() in (".md", ".markdown"):
        src_mime = "text/markdown"
    src_mime = src_mime or "application/octet-stream"

    meta: dict = {"name": name or file_path.name}
    if convert:
        target = CONVERT_TARGETS.get(src_mime)
        if not target:
            raise ValidationError(
                f"--convert does not support source type '{src_mime}'.",
                suggestion="Convertible: markdown, csv, and Office formats (doc/docx/xls/xlsx/ppt/pptx).",
            )
        meta["mimeType"] = target
    if folder:
        meta["parents"] = [extract_file_id(folder)]

    service = _get_service(account)
    media = MediaFileUpload(str(file_path), mimetype=src_mime, resumable=True)
    try:
        result = service.files().create(
            body=meta, media_body=media,
            fields="id,name,mimeType,size,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        _handle_drive_http_error(e, "drive upload")
        raise  # unreachable
    return {"uploaded": True, **result}


def _resolve_dest(dest: str | None, default_name: str, force: bool) -> Path:
    """Resolve --dest (dir or path) to a target path; refuse overwrite without force."""
    target = Path(dest).expanduser() if dest else Path(default_name)
    if target.is_dir():
        target = target / default_name
    if target.exists() and not force:
        raise ValidationError(
            f"Refusing to overwrite existing file: {target}",
            suggestion="Pass --force to overwrite, or choose a different --dest.",
        )
    return target


def _download_media(request, target: Path, context: str) -> int:
    """Stream a media request to disk; remove partial file on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
    except HttpError as e:
        target.unlink(missing_ok=True)
        _handle_drive_http_error(e, context)
        raise  # unreachable
    return target.stat().st_size


def _export_to_disk(service, meta: dict, export_format: str, dest: str | None, force: bool) -> dict:
    """Validate the format for the source type and export to disk."""
    mime = meta["mimeType"]
    valid = EXPORTABLE.get(mime)
    if not valid:
        raise ValidationError(f"File type '{mime}' cannot be exported.")
    if export_format not in valid:
        raise ValidationError(
            f"Format '{export_format}' is not valid for {mime}.",
            suggestion=f"Valid formats: {', '.join(valid)}",
        )
    target = _resolve_dest(dest, f"{meta['name']}.{export_format}", force)
    request = service.files().export_media(
        fileId=meta["id"], mimeType=EXPORT_MIME[export_format],
    )
    size = _download_media(request, target, "drive export")
    return {
        "exported": True,
        "fileId": meta["id"],
        "name": meta["name"],
        "format": export_format,
        "path": str(target),
        "bytes": size,
    }


def download(
    file_id: str,
    dest: str,
    *,
    export_format: str | None = None,
    force: bool = False,
    account: str | None = None,
) -> dict:
    """Download a binary file, or export a Google-native file (requires --format)."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    meta = _get_meta(service, file_id, "id,name,mimeType", "drive download")
    mime = meta.get("mimeType", "")
    if mime.startswith(_GOOGLE_NATIVE_PREFIX):
        if not export_format:
            raise ValidationError(
                f"'{meta.get('name')}' is a Google-native file ({mime}); --format is required.",
                suggestion=f"Valid formats: {', '.join(EXPORTABLE.get(mime, [])) or 'none (not exportable)'}",
            )
        return _export_to_disk(service, meta, export_format, dest, force)
    if export_format:
        raise ValidationError(
            "--format only applies to Google-native files; this file downloads as-is.",
            suggestion="Drop --format, or use 'drive export' on a Docs/Sheets/Slides file.",
        )
    target = _resolve_dest(dest, meta["name"], force)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    size = _download_media(request, target, "drive download")
    return {
        "downloaded": True,
        "fileId": file_id,
        "name": meta["name"],
        "path": str(target),
        "bytes": size,
    }


def export(
    file_id: str,
    export_format: str,
    dest: str | None = None,
    *,
    force: bool = False,
    account: str | None = None,
) -> dict:
    """Export a Google-native file (Doc/Sheet/Slides) to pdf/docx/xlsx/pptx/csv/txt/md/html."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    meta = _get_meta(service, file_id, "id,name,mimeType", "drive export")
    if not meta.get("mimeType", "").startswith(_GOOGLE_NATIVE_PREFIX):
        raise ValidationError(
            f"'{meta.get('name')}' is not a Google-native file; use 'drive download'.",
        )
    return _export_to_disk(service, meta, export_format, dest, force)


# ---------------------------------------------------------------------------
# Folders / organize
# ---------------------------------------------------------------------------

def mkdir(name: str, parent: str | None = None, account: str | None = None) -> dict:
    """Create a Drive folder."""
    meta: dict = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [extract_file_id(parent)]
    service = _get_service(account)
    try:
        result = service.files().create(
            body=meta, fields="id,name,webViewLink", supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        _handle_drive_http_error(e, "drive mkdir")
        raise  # unreachable
    return {"created": True, **result}


def move(file_id: str, to_folder: str, account: str | None = None) -> dict:
    """Move a file into a folder (replaces its current parents)."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    current = _get_meta(service, file_id, "parents", "drive move")
    previous = ",".join(current.get("parents", []))
    try:
        result = service.files().update(
            fileId=file_id, addParents=extract_file_id(to_folder),
            removeParents=previous, fields="id,name,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        _handle_drive_http_error(e, "drive move")
        raise  # unreachable
    return {"moved": True, **result}


def rename(file_id: str, new_name: str, account: str | None = None) -> dict:
    """Rename a file."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    try:
        result = service.files().update(
            fileId=file_id, body={"name": new_name},
            fields="id,name,webViewLink", supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, "drive rename")
        raise  # unreachable
    return {"renamed": True, **result}


def copy(
    file_id: str,
    name: str | None = None,
    folder: str | None = None,
    account: str | None = None,
) -> dict:
    """Copy a file, optionally renaming it and placing it in a folder."""
    body: dict = {}
    if name:
        body["name"] = name
    if folder:
        body["parents"] = [extract_file_id(folder)]
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    try:
        result = service.files().copy(
            fileId=file_id, body=body,
            fields="id,name,webViewLink,parents", supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, "drive copy")
        raise  # unreachable
    return {"copied": True, **result}


def set_trashed(file_id: str, trashed: bool, account: str | None = None) -> dict:
    """Move a file to/out of the trash. There is deliberately no permanent delete."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    try:
        result = service.files().update(
            fileId=file_id, body={"trashed": trashed},
            fields="id,name,trashed", supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, "drive trash" if trashed else "drive untrash")
        raise  # unreachable
    return result


# ---------------------------------------------------------------------------
# Sharing (permissions)
# ---------------------------------------------------------------------------

def share(
    file_id: str,
    email: str,
    role: str,
    *,
    notify: bool = True,
    message: str | None = None,
    account: str | None = None,
) -> dict:
    """Grant a user access. anyone-with-link creation is deliberately unsupported."""
    if role not in SHARE_ROLES:
        raise ValidationError(
            f"Invalid role '{role}'.",
            suggestion=f"Valid roles: {', '.join(SHARE_ROLES)}",
        )
    validate_email(email, field="with")
    if message and not notify:
        raise ValidationError(
            "--message requires notification emails.",
            suggestion="Drop --no-notify to send the message.",
        )
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    kwargs: dict = {
        "fileId": file_id,
        "body": {"type": "user", "role": role, "emailAddress": email},
        "sendNotificationEmail": notify,
        "fields": "id,type,role,emailAddress",
        "supportsAllDrives": True,
    }
    if message:
        kwargs["emailMessage"] = message
    try:
        result = service.permissions().create(**kwargs).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, "drive share")
        raise  # unreachable
    return {"shared": True, "fileId": file_id, "permission": result}


def list_permissions(file_id: str, account: str | None = None) -> dict:
    """List a file's permissions, including any anyone-with-link entries."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    try:
        result = service.permissions().list(
            fileId=file_id,
            fields=(
                "permissions(id,type,role,emailAddress,domain,displayName,"
                "allowFileDiscovery,expirationTime)"
            ),
            supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("File", file_id)
        _handle_drive_http_error(e, "drive shares")
        raise  # unreachable
    perms = result.get("permissions", [])
    return {"fileId": file_id, "permissions": perms, "count": len(perms)}


def unshare(file_id: str, permission_id: str, account: str | None = None) -> dict:
    """Remove a permission from a file."""
    service = _get_service(account)
    file_id = extract_file_id(file_id)
    try:
        service.permissions().delete(
            fileId=file_id, permissionId=permission_id, supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Permission", permission_id)
        _handle_drive_http_error(e, "drive unshare")
        raise  # unreachable
    return {"unshared": True, "fileId": file_id, "permissionId": permission_id}

"""Google Sheets operations — read, write, append, list.

All functions return dicts. Used for managing contact lists in outreach campaigns.
"""

from __future__ import annotations

import json

from auth import build_service
from googleapiclient.errors import HttpError
from output import NotFoundError, ValidationError, handle_http_error


def _get_service(account: str | None = None):
    return build_service("sheets", "v4", account)


def _get_drive_service(account: str | None = None):
    return build_service("drive", "v3", account)


def read(
    spreadsheet_id: str,
    range: str = "Sheet1",
    account: str | None = None,
) -> dict:
    """Read values from a spreadsheet range."""
    service = _get_service(account)
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Spreadsheet", spreadsheet_id)
        handle_http_error(e, "sheets read")
        raise  # unreachable

    values = result.get("values", [])
    return {
        "spreadsheetId": spreadsheet_id,
        "range": result.get("range", range),
        "rows": values,
        "count": len(values),
    }


def write(
    spreadsheet_id: str,
    range: str,
    value: str,
    account: str | None = None,
) -> dict:
    """Write a single value to a cell or range."""
    service = _get_service(account)
    # If value looks like JSON array, parse it as a row
    values: list
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            values = [parsed] if isinstance(parsed, list) else [[parsed]]
        except json.JSONDecodeError:
            values = [[value]]
    else:
        values = [[value]]

    try:
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Spreadsheet", spreadsheet_id)
        handle_http_error(e, "sheets write")
        raise  # unreachable

    return {
        "updated": True,
        "spreadsheetId": spreadsheet_id,
        "range": result.get("updatedRange", range),
        "updatedCells": result.get("updatedCells", 0),
    }


def append(
    spreadsheet_id: str,
    values: str,
    range: str = "Sheet1",
    account: str | None = None,
) -> dict:
    """Append a row to a spreadsheet. values is a JSON array string."""
    service = _get_service(account)
    try:
        parsed = json.loads(values)
    except json.JSONDecodeError:
        raise ValidationError(
            f"Invalid --values: must be a JSON array. Got: {values[:50]}",
            suggestion='Example: --values \'["John Doe","john@co.com","pending"]\'',
        )

    row = parsed if isinstance(parsed, list) else [parsed]

    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Spreadsheet", spreadsheet_id)
        handle_http_error(e, "sheets append")
        raise  # unreachable

    return {
        "appended": True,
        "spreadsheetId": spreadsheet_id,
        "range": result.get("updates", {}).get("updatedRange", range),
    }


def list_spreadsheets(account: str | None = None, max_results: int = 200) -> dict:
    """List spreadsheets accessible to the account (with pagination)."""
    drive = _get_drive_service(account)
    all_files: list[dict] = []
    page_token = None

    while True:
        remaining = max_results - len(all_files)
        if remaining <= 0:
            break
        kwargs: dict = {
            "q": "mimeType='application/vnd.google-apps.spreadsheet'",
            "fields": "nextPageToken,files(id,name,modifiedTime)",
            "orderBy": "modifiedTime desc",
            "pageSize": min(remaining, 100),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            result = drive.files().list(**kwargs).execute()
        except HttpError as e:
            handle_http_error(e, "sheets list spreadsheets")
            raise  # unreachable
        all_files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    files = [
        {"id": f["id"], "name": f["name"], "modified": f.get("modifiedTime", "")}
        for f in all_files
    ]
    return {"spreadsheets": files, "count": len(files)}

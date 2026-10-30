"""YouTube operations — upload a video to the channel the account's token belongs to.

The upload runs under the `youtube-upload` opt-in, whose only scope is youtube.upload:
it inserts videos and nothing else, so it cannot edit, delete or change the privacy of
any video, this one included. Reads (counts, analytics) go through `gw api call` under
the read-only `youtube` opt-in; see the gw-youtube skill.

YouTube's Required Minimum Functionality asks that the uploader sets the title, the
description and the privacy status, so all three are required here and privacy has no
default. A scheduled publish (publishAt) needs privacy private: YouTube makes the video
public at that time.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from auth import build_service
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from output import ValidationError, handle_http_error

PRIVACY = ("private", "unlisted", "public")


def upload(
    path: str,
    *,
    title: str,
    description: str,
    privacy: str,
    made_for_kids: bool | None,
    tags: list[str] | None = None,
    category_id: str | None = None,
    publish_at: str | None = None,
    account: str | None = None,
) -> dict:
    """Upload a local video file (resumable) and return its id, URL and status."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValidationError(f"File not found: {path}")
    if not title.strip() or len(title) > 100:
        raise ValidationError("--title must be 1 to 100 characters.")
    if len(description.encode("utf-8")) > 5000:
        raise ValidationError("The description must be at most 5000 bytes.")
    if privacy not in PRIVACY:
        raise ValidationError(f"--privacy must be one of: {', '.join(PRIVACY)}.")
    if made_for_kids is None:
        raise ValidationError(
            "Say whether the video is made for kids.",
            suggestion="Pass --made-for-kids or --not-made-for-kids.",
        )
    if publish_at and privacy != "private":
        raise ValidationError(
            "--publish-at needs --privacy private: YouTube makes the video public at that time."
        )

    snippet: dict = {"title": title, "description": description}
    if tags:
        snippet["tags"] = tags
    if category_id:
        snippet["categoryId"] = category_id
    status: dict = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status["publishAt"] = publish_at

    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=True)
    service = build_service("youtube", "v3", account)
    try:
        video = service.videos().insert(
            part="snippet,status",
            body={"snippet": snippet, "status": status},
            media_body=media,
        ).execute()
    except HttpError as e:
        handle_http_error(e, "youtube upload")
        raise  # unreachable

    got = video.get("status", {})
    out = {
        "uploaded": True,
        "id": video["id"],
        "url": f"https://www.youtube.com/watch?v={video['id']}",
        "privacy_requested": privacy,
        "privacy": got.get("privacyStatus"),
        "upload_status": got.get("uploadStatus"),
        "publish_at": got.get("publishAt"),
    }
    if privacy != "private" or publish_at:
        out["check_after_processing"] = (
            "YouTube can lock an upload from an unaudited API project to private once it "
            "is processed. Read status.privacyStatus again with: gw api call youtube:v3 "
            f"videos.list --params '{{\"part\":\"status\",\"id\":\"{video['id']}\"}}'"
        )
    return out

"""Gmail operations — send, draft, list, get, reply, labels, scan.

All functions return dicts (JSON-serializable). The CLI layer handles
output formatting. Agents importing this module get dicts directly.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

from auth import build_service
from googleapiclient.errors import HttpError
from output import (
    CliError,
    NotFoundError,
    ValidationError,
    handle_http_error,
    validate_email,
)

_VALID_SINCE_UNITS = {"d": 1, "w": 7}


def _get_service(account: str | None = None):
    return build_service("gmail", "v1", account)


def _crm_track_outbound(to: str | None, cc: str | None = None) -> dict:
    """Bump last_contact_date in contacts.csv for known external recipients.

    Wraps ``email_crm_sync.bump_recipients_last_contact`` with full exception
    isolation — a CRM failure must never break the email send path. Lazy
    import keeps gmail.py independent of CRM at module load.
    """
    try:
        import email_crm_sync  # type: ignore[import-not-found]
        return email_crm_sync.bump_recipients_last_contact(
            to=to, cc=cc, channel="email-out",
        )
    except Exception as exc:
        return {"updated": 0, "matched": 0, "error": f"crm-track-failed: {exc}"}


def _sanitize_header(value: str) -> str:
    """Remove CRLF sequences and null bytes from header values to prevent injection."""
    return value.replace("\r", "").replace("\n", "").replace("\x00", "")


def _parse_addresses(header_value: str) -> list[str]:
    """Parse a To/Cc header into a list of bare lowercase email addresses.

    Handles `Display Name <email@x.com>` format and comma-separated lists via
    Python's email.utils.getaddresses. Empty header returns empty list.
    Results are lowercased for consistent comparison; callers that need to
    preserve the original casing should match against the original header.
    """
    if not header_value:
        return []
    from email.utils import getaddresses
    addrs = getaddresses([header_value])
    return [email.lower() for _, email in addrs if email]


def _plain_to_html(text: str) -> str:
    """Convert plain text body to HTML, preserving formatting and making links clickable."""
    # Escape HTML entities
    html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Convert markdown-style links [text](url) to <a> tags (before bare URL conversion)
    html = re.sub(
        r'\[([^\]]+)\]\((https?://[^\s)]+)\)',
        r'<a href="\2">\1</a>',
        html,
    )
    # Convert markdown bold **text** to <strong>
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    # Convert remaining bare URLs to clickable links (skip already-linked ones)
    html = re.sub(
        r'(?<!")(https?://[^\s<>&]+)(?!</a>)',
        r'<a href="\1">\1</a>',
        html,
    )
    # Convert line breaks: double newline = paragraph, single = <br>
    paragraphs = html.split("\n\n")
    html = "</p><p>".join(p.replace("\n", "<br>") for p in paragraphs)
    return (
        '<div style="font-family:Arial,sans-serif;font-size:14px">'
        f"<p>{html}</p>"
        "</div>"
    )


def _build_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    from_name: str | None = None,
    from_email: str | None = None,
    signature_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    thread_id: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Build a Gmail API message payload (multipart: plain + HTML, optional attachments)."""
    # Validate each recipient — both `to` and `cc` support comma-separated lists
    # (required for reply-all) and `Display Name <email>` form (required when
    # preserving a reply draft's existing To/Cc headers). We validate the bare
    # address extracted from each entry but keep the original string for the
    # header (set below), so display names survive.
    from email.utils import getaddresses

    to_addrs = [addr for _, addr in getaddresses([to]) if addr]
    if not to_addrs:
        raise ValidationError(
            f"Invalid email address in --to: '{to}'",
            suggestion="Use a valid email address, e.g. user@domain.com",
        )
    for addr in to_addrs:
        validate_email(addr, field="to")
    if cc:
        for _, addr in getaddresses([cc]):
            if addr:
                validate_email(addr, field="cc")

    # Build multipart/alternative with plain text + HTML
    alt_part = MIMEMultipart("alternative")
    html_body = _plain_to_html(body)
    if signature_html:
        html_body += '<br><div class="gmail_signature">' + signature_html + "</div>"
    alt_part.attach(MIMEText(body, "plain", "utf-8"))
    alt_part.attach(MIMEText(html_body, "html", "utf-8"))

    # If attachments, wrap in multipart/mixed; otherwise use alt_part as the message
    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt_part)
        for filepath in attachments:
            filepath = os.path.expanduser(filepath)
            if not os.path.isfile(filepath):
                raise ValidationError(
                    f"Attachment not found: {filepath}",
                    suggestion="Check the file path and try again.",
                )
            filename = os.path.basename(filepath)
            content_type, _ = mimetypes.guess_type(filepath)
            if content_type is None:
                content_type = "application/octet-stream"
            main_type, sub_type = content_type.split("/", 1)
            with open(filepath, "rb") as f:
                attachment = MIMEBase(main_type, sub_type)
                attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(attachment)
    else:
        msg = alt_part

    msg["to"] = _sanitize_header(to)
    msg["subject"] = _sanitize_header(subject)
    if from_name and from_email:
        msg["from"] = _sanitize_header(f"{from_name} <{from_email}>")
    if cc:
        msg["cc"] = _sanitize_header(cc)
    if in_reply_to:
        msg["In-Reply-To"] = _sanitize_header(in_reply_to)
        msg["References"] = _sanitize_header(references or in_reply_to)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload: dict = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return payload


def _walk_attachments(part: dict, out: list[dict]) -> None:
    """Recursively collect attachment metadata from a Gmail payload part.

    Each entry: filename, mimeType, size, attachmentId. Only parts with both
    a filename and an attachmentId are included (filters inline-rendered
    bodies and other non-attachment MIME parts).
    """
    filename = part.get("filename")
    body = part.get("body") or {}
    attachment_id = body.get("attachmentId")
    if filename and attachment_id:
        out.append({
            "filename": filename,
            "mimeType": part.get("mimeType", ""),
            "size": body.get("size", 0),
            "attachmentId": attachment_id,
        })
    for sub in part.get("parts", []) or []:
        _walk_attachments(sub, out)


def _format_message(msg: dict) -> dict:
    """Extract useful fields from a Gmail API message resource."""
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    result = {
        "id": msg.get("id", ""),
        "threadId": msg.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "labelIds": msg.get("labelIds", []),
    }
    cc = headers.get("cc", "")
    if cc:
        result["cc"] = cc
    attachments: list[dict] = []
    _walk_attachments(payload, attachments)
    if attachments:
        result["attachments"] = attachments
    return result


def _get_body_text(msg: dict) -> str:
    """Extract body text from message payload (recursive MIME walk).

    Prefers text/plain; falls back to text/html (HTML-stripped to plain) when
    there's no text/plain — many modern senders (Intuit, Slack renewals, etc.)
    only ship the HTML alternative, so a plain-only walk loses all body content
    (and any URLs in it).
    """
    import re

    def _walk_for(mime: str, part: dict) -> str:
        if part.get("mimeType") == mime and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        for subpart in part.get("parts", []):
            result = _walk_for(mime, subpart)
            if result:
                return result
        return ""

    payload = msg.get("payload", {})
    text = _walk_for("text/plain", payload)
    if text:
        return text
    html = _walk_for("text/html", payload)
    if not html:
        return ""
    # Lightweight HTML→text: keep URLs from <a href="..."> intact, strip tags.
    # We don't need pretty rendering — just enough that downstream regexes
    # (e.g. PDF-URL extractors) can find anchors and inline links.
    html = re.sub(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 \1", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    # Decode common HTML entities
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return html


def _get_send_as(service) -> dict:
    """Fetch primary send-as settings (display name + signature) from Gmail API."""
    try:
        send_as_list = service.users().settings().sendAs().list(userId="me").execute()
        for entry in send_as_list.get("sendAs", []):
            if entry.get("isPrimary"):
                return {
                    "displayName": entry.get("displayName", ""),
                    "email": entry.get("sendAsEmail", ""),
                    "signature": entry.get("signature", ""),
                }
    except HttpError:
        pass
    return {}


def send(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Send an email. Pass thread_id to send as a reply in an existing thread."""
    service = _get_service(account)
    send_as = _get_send_as(service)
    message = _build_message(
        to, subject, body, cc=cc,
        from_name=send_as.get("displayName"),
        from_email=send_as.get("email"),
        signature_html=send_as.get("signature"),
        thread_id=thread_id,
        attachments=attachments,
    )
    try:
        result = service.users().messages().send(userId="me", body=message).execute()
    except HttpError as e:
        handle_http_error(e, "gmail send")
        raise  # unreachable — handle_http_error always raises

    # CRM auto-track: bump last_contact_date for known recipients (best-effort)
    crm_stats = _crm_track_outbound(to=to, cc=cc)
    return {
        "sent": True, "id": result["id"], "threadId": result.get("threadId"),
        "crm": crm_stats,
    }


def draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Create a draft. Pass thread_id to draft as a reply in an existing thread."""
    service = _get_service(account)
    send_as = _get_send_as(service)
    message = _build_message(
        to, subject, body, cc=cc,
        from_name=send_as.get("displayName"),
        from_email=send_as.get("email"),
        signature_html=send_as.get("signature"),
        thread_id=thread_id,
        attachments=attachments,
    )
    try:
        result = service.users().drafts().create(
            userId="me", body={"message": message}
        ).execute()
    except HttpError as e:
        handle_http_error(e, "gmail draft")
        raise  # unreachable — handle_http_error always raises
    return {"drafted": True, "id": result["id"], "messageId": result["message"]["id"]}


_DRAFT_QUERY_RE = re.compile(r"(?:^|\s)(?:in:drafts|is:draft)(?:\s|$)", re.IGNORECASE)


def list_messages(
    query: str = "",
    limit: int = 20,
    account: str | None = None,
) -> dict:
    """List messages matching a Gmail search query (with pagination and batch fetch).

    Automatic routing: if the query contains `in:drafts` or `is:draft`, this
    transparently delegates to `list_drafts()` so callers get draft IDs usable
    with get-draft/send-draft/update-draft/delete-draft. Without this routing,
    Gmail's messages.list returns message IDs that aren't valid draft IDs.
    """
    if query and _DRAFT_QUERY_RE.search(query):
        # Strip the draft-marker token(s) — drafts.list is already scoped to
        # drafts, so leaving "in:drafts" in the forwarded query would match
        # zero drafts literally.
        stripped = _DRAFT_QUERY_RE.sub(" ", query).strip()
        return list_drafts(query=stripped, limit=limit, account=account)

    service = _get_service(account)

    # Paginate message ID collection
    all_refs: list[dict] = []
    page_token = None
    while len(all_refs) < limit:
        page_size = min(limit - len(all_refs), 100)
        kwargs: dict = {"userId": "me", "q": query, "maxResults": page_size}
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            result = service.users().messages().list(**kwargs).execute()
        except HttpError as e:
            handle_http_error(e, "gmail list")
            raise  # unreachable
        all_refs.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not all_refs:
        return {"messages": [], "count": 0, "query": query}

    # Use batch API to fetch messages (chunked to 100 per batch — Google API limit)
    fetched: dict[str, dict] = {}
    fetch_errors: list[str] = []

    def _callback(request_id, response, exception):
        if exception is None:
            fetched[request_id] = response
        else:
            fetch_errors.append(f"{request_id}: {exception}")

    for i in range(0, len(all_refs), 100):
        chunk = all_refs[i : i + 100]
        batch = service.new_batch_http_request(callback=_callback)
        for msg_ref in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata",
                    metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
                ),
                request_id=msg_ref["id"],
            )
        try:
            batch.execute()
        except HttpError as e:
            handle_http_error(e, "gmail batch fetch")
            raise  # unreachable

    if not fetched and all_refs:
        raise CliError(
            f"All {len(all_refs)} message fetches failed",
            suggestion="Check account permissions or retry.",
        )

    # Preserve original ordering
    messages = []
    for msg_ref in all_refs:
        if msg_ref["id"] in fetched:
            messages.append(_format_message(fetched[msg_ref["id"]]))

    result_dict: dict = {"messages": messages, "count": len(messages), "query": query}
    if fetch_errors:
        result_dict["warnings"] = fetch_errors
    return result_dict


def list_drafts(
    query: str = "",
    limit: int = 20,
    account: str | None = None,
) -> dict:
    """List drafts matching a Gmail search query (with pagination and batch fetch).

    Each returned draft has `draftId` populated so callers can chain directly
    into `get_draft`, `update_draft`, `send_draft`, or `delete_draft`.
    """
    service = _get_service(account)

    # Paginate draft ref collection
    all_refs: list[dict] = []
    page_token = None
    while len(all_refs) < limit:
        page_size = min(limit - len(all_refs), 100)
        kwargs: dict = {"userId": "me", "q": query, "maxResults": page_size}
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            result = service.users().drafts().list(**kwargs).execute()
        except HttpError as e:
            handle_http_error(e, "gmail list_drafts")
            raise  # unreachable
        all_refs.extend(result.get("drafts", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not all_refs:
        return {"drafts": [], "count": 0, "query": query}

    # Collect the message IDs embedded in each draft ref
    msg_ids: list[str] = []
    for draft_ref in all_refs:
        msg_id = draft_ref.get("message", {}).get("id")
        if msg_id:
            msg_ids.append(msg_id)

    # Use batch API to fetch message metadata (chunked to 100 per batch)
    fetched: dict[str, dict] = {}
    fetch_errors: list[str] = []

    def _callback(request_id, response, exception):
        if exception is None:
            fetched[request_id] = response
        else:
            fetch_errors.append(f"{request_id}: {exception}")

    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i : i + 100]
        batch = service.new_batch_http_request(callback=_callback)
        for msg_id in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
                ),
                request_id=msg_id,
            )
        try:
            batch.execute()
        except HttpError as e:
            handle_http_error(e, "gmail list_drafts batch fetch")
            raise  # unreachable

    if not fetched and msg_ids:
        raise CliError(
            f"All {len(msg_ids)} draft fetches failed",
            suggestion="Check account permissions or retry.",
        )

    # Preserve draft ordering and attach draftId to each formatted entry
    drafts_out: list[dict] = []
    for draft_ref in all_refs:
        msg_id = draft_ref.get("message", {}).get("id")
        if msg_id and msg_id in fetched:
            formatted = _format_message(fetched[msg_id])
            formatted["draftId"] = draft_ref["id"]
            drafts_out.append(formatted)

    result_dict: dict = {"drafts": drafts_out, "count": len(drafts_out), "query": query}
    if fetch_errors:
        result_dict["warnings"] = fetch_errors
    return result_dict


def get(message_id: str, account: str | None = None) -> dict:
    """Get a single message with full body."""
    service = _get_service(account)
    try:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Message", message_id)
        handle_http_error(e, "gmail get")
        raise  # unreachable
    formatted = _format_message(msg)
    formatted["body"] = _get_body_text(msg)
    return formatted


def get_thread(thread_id: str, account: str | None = None) -> list[dict]:
    """Get all messages in a thread, formatted with headers and body text.

    Returns a list of message dicts (same format as get()), ordered oldest-first.
    """
    service = _get_service(account)
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Thread", thread_id)
        handle_http_error(e, "gmail get_thread")
        raise  # unreachable
    messages = []
    for msg in thread.get("messages", []):
        formatted = _format_message(msg)
        formatted["body"] = _get_body_text(msg)
        messages.append(formatted)
    return messages


def _prepare_reply_payload(
    service,
    message_id: str,
    body: str,
    to: str | None,
    cc: str | None,
    no_cc: bool,
    reply_all: bool,
    attachments: list[str] | None,
    context_label: str,
) -> tuple[dict, str | None, str]:
    """Fetch the original message and compute the full reply MIME payload.

    Returns (message_payload, thread_id, final_to).

    Semantics (shared by reply, draft_reply, reply_all, draft_reply_all):
      - To: the From of the original. If From is us (self-reply), flip to the
        original To list (first address for plain reply, all for reply-all).
        Overridden by an explicit `to` argument.
      - Cc:
          * no_cc=True  → always empty
          * cc is not None → override with the provided value (empty string clears)
          * reply_all=True → union of original To and Cc, minus self, minus From
            (or minus final_to if overridden), deduped case-insensitive
          * reply_all=False → preserve the original Cc verbatim, minus self
      - Subject: original subject with "Re: " prefix (unless already prefixed)
      - Threading: In-Reply-To, References, threadId preserved
      - From display name and signature come from _get_send_as (previously
        missing in reply() and draft_reply() — this is a bug fix)
      - Attachments passed through to _build_message unchanged
    """
    if no_cc and cc is not None and cc != "":
        raise ValidationError(
            "--cc and --no-cc are mutually exclusive.",
            suggestion="Pass one or the other, not both.",
        )

    try:
        original = service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["From", "To", "Cc", "Subject", "Message-ID"],
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Message", message_id)
        handle_http_error(e, f"gmail {context_label} (fetch original)")
        raise  # unreachable

    headers = {h["name"].lower(): h["value"] for h in original["payload"].get("headers", [])}
    send_as = _get_send_as(service)
    my_email = (send_as.get("email") or "").lower()

    from_addrs = _parse_addresses(headers.get("from", ""))
    from_addr = from_addrs[0] if from_addrs else ""
    is_self_from = bool(my_email) and from_addr == my_email

    original_to_addrs = _parse_addresses(headers.get("to", ""))
    original_cc_addrs = _parse_addresses(headers.get("cc", ""))

    # Determine To
    if to is not None:
        final_to = to
    elif is_self_from:
        if not original_to_addrs:
            raise ValidationError(
                f"Cannot reply: message {message_id} was sent by you but has no 'To' recipients to flip to.",
                suggestion="Pass --to explicitly.",
            )
        if reply_all:
            final_to = ", ".join(original_to_addrs)
        else:
            final_to = original_to_addrs[0]
    else:
        if not from_addr:
            raise ValidationError(
                f"Cannot reply: message {message_id} has no 'From' header.",
                suggestion=f"Inspect with 'gw.py gmail get {message_id}', or pass --to explicitly.",
            )
        final_to = from_addr

    final_to_addrs_lower = {a.lower() for a in _parse_addresses(final_to)}

    # Determine Cc
    final_cc: str | None
    if no_cc:
        final_cc = None
    elif cc is not None:
        final_cc = cc if cc else None
    elif reply_all:
        # Everyone on the original conversation: From + To + Cc.
        # Filter: self, and anyone already in final_to. This correctly handles
        # the --to override case: when final_to isn't the original From, the
        # original From becomes a Cc candidate instead of being silently dropped.
        all_participants: list[str] = []
        if from_addr:
            all_participants.append(from_addr)
        all_participants.extend(original_to_addrs)
        all_participants.extend(original_cc_addrs)

        seen: set[str] = set()
        cc_list: list[str] = []
        for addr in all_participants:
            addr_lower = addr.lower()
            if addr_lower in seen:
                continue
            if my_email and addr_lower == my_email:
                continue
            if addr_lower in final_to_addrs_lower:
                continue
            seen.add(addr_lower)
            cc_list.append(addr)
        final_cc = ", ".join(cc_list) if cc_list else None
    else:
        # Plain reply: preserve original Cc verbatim (minus self)
        preserved = [
            a for a in original_cc_addrs
            if not my_email or a.lower() != my_email
        ]
        final_cc = ", ".join(preserved) if preserved else None

    # Subject
    subject = headers.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message_id_header = headers.get("message-id", "")

    message = _build_message(
        to=final_to,
        subject=subject,
        body=body,
        cc=final_cc,
        from_name=send_as.get("displayName"),
        from_email=send_as.get("email"),
        signature_html=send_as.get("signature"),
        in_reply_to=message_id_header,
        references=message_id_header,
        thread_id=original.get("threadId"),
        attachments=attachments,
    )
    return message, original.get("threadId"), final_to


def reply(
    message_id: str,
    body: str,
    to: str | None = None,
    cc: str | None = None,
    no_cc: bool = False,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Reply to a message. Preserves the thread and the original Cc list.

    By default:
      - To = the sender of the original message
      - Cc = the original Cc list, minus our own address if present
      - If the original was sent by us, To flips to the first recipient
        of the original To list (follow-up on your own sent message)

    Overrides:
      - `to`: explicit recipient, bypasses From/self-reply detection
      - `cc`: explicit Cc list (empty string clears)
      - `no_cc`: force Cc empty, conflicts with `cc`
    """
    service = _get_service(account)
    message, _thread_id, final_to = _prepare_reply_payload(
        service, message_id, body,
        to=to, cc=cc, no_cc=no_cc, reply_all=False,
        attachments=attachments, context_label="reply",
    )
    try:
        result = service.users().messages().send(userId="me", body=message).execute()
    except HttpError as e:
        handle_http_error(e, "gmail reply")
        raise  # unreachable
    crm_stats = _crm_track_outbound(to=final_to, cc=cc)
    return {
        "replied": True,
        "id": result["id"],
        "threadId": result.get("threadId"),
        "to": final_to,
        "crm": crm_stats,
    }


def draft_reply(
    message_id: str,
    body: str,
    to: str | None = None,
    cc: str | None = None,
    no_cc: bool = False,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Create a draft reply. Preserves the thread and the original Cc list.

    Same semantics as `reply()`, but creates a draft instead of sending.
    """
    service = _get_service(account)
    message, thread_id, final_to = _prepare_reply_payload(
        service, message_id, body,
        to=to, cc=cc, no_cc=no_cc, reply_all=False,
        attachments=attachments, context_label="draft_reply",
    )
    try:
        result = service.users().drafts().create(
            userId="me", body={"message": message}
        ).execute()
    except HttpError as e:
        handle_http_error(e, "gmail draft_reply")
        raise  # unreachable
    return {
        "drafted": True,
        "id": result["id"],
        "messageId": result["message"]["id"],
        "threadId": thread_id,
        "to": final_to,
    }


def reply_all(
    message_id: str,
    body: str,
    to: str | None = None,
    cc: str | None = None,
    no_cc: bool = False,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Reply-all: send to the original sender with everyone else in Cc.

    Expands the Cc list to include the union of the original To and Cc
    recipients, minus ourselves and minus the address we're replying to.
    Self-reply follow-ups keep the full original To list in To and
    preserve the original Cc.
    """
    service = _get_service(account)
    message, _thread_id, final_to = _prepare_reply_payload(
        service, message_id, body,
        to=to, cc=cc, no_cc=no_cc, reply_all=True,
        attachments=attachments, context_label="reply_all",
    )
    try:
        result = service.users().messages().send(userId="me", body=message).execute()
    except HttpError as e:
        handle_http_error(e, "gmail reply_all")
        raise  # unreachable
    crm_stats = _crm_track_outbound(to=final_to, cc=cc)
    return {
        "replied_all": True,
        "id": result["id"],
        "threadId": result.get("threadId"),
        "to": final_to,
        "crm": crm_stats,
    }


def draft_reply_all(
    message_id: str,
    body: str,
    to: str | None = None,
    cc: str | None = None,
    no_cc: bool = False,
    account: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Draft a reply-all. Same semantics as `reply_all()`, but creates a draft."""
    service = _get_service(account)
    message, thread_id, final_to = _prepare_reply_payload(
        service, message_id, body,
        to=to, cc=cc, no_cc=no_cc, reply_all=True,
        attachments=attachments, context_label="draft_reply_all",
    )
    try:
        result = service.users().drafts().create(
            userId="me", body={"message": message}
        ).execute()
    except HttpError as e:
        handle_http_error(e, "gmail draft_reply_all")
        raise  # unreachable
    return {
        "drafted_all": True,
        "id": result["id"],
        "messageId": result["message"]["id"],
        "threadId": thread_id,
        "to": final_to,
    }


def get_draft(draft_id: str, account: str | None = None) -> dict:
    """Get a draft with full body text."""
    service = _get_service(account)
    try:
        result = service.users().drafts().get(
            userId="me", id=draft_id, format="full"
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Draft", draft_id)
        handle_http_error(e, "gmail get_draft")
        raise  # unreachable
    msg = result.get("message", {})
    formatted = _format_message(msg)
    formatted["body"] = _get_body_text(msg)
    formatted["draftId"] = result["id"]
    return formatted


def _extract_existing_attachments(service, msg: dict, dest_dir: str) -> list[str]:
    """Download all attachments from an already-fetched message payload.

    Returns the list of saved filepaths, one per attachment. Used by
    update_draft() to preserve existing attachments across a drafts.update
    call (which replaces the entire message).
    """
    message_id = msg.get("id")
    if not message_id:
        return []
    attachments: list[dict] = []
    _walk_attachments(msg.get("payload", {}), attachments)
    saved: list[str] = []
    for att_meta in attachments:
        att = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=att_meta["attachmentId"]
        ).execute()
        data = base64.urlsafe_b64decode(att["data"])
        filepath = os.path.join(dest_dir, att_meta["filename"])
        with open(filepath, "wb") as f:
            f.write(data)
        saved.append(filepath)
    return saved


def update_draft(
    draft_id: str,
    body: str | None = None,
    subject: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    attachments: list[str] | None = None,
    append_attachments: bool = False,
    account: str | None = None,
) -> dict:
    """Update an existing draft with merge semantics.

    Any field left as None is preserved from the current draft — we fetch
    the draft first, overlay the provided fields, then call drafts.update.
    Gmail's drafts.update replaces the entire message, so this is the only
    way to do partial updates without losing unchanged fields (including
    attachments).

    Attachment merging:
      - attachments=None (default): keep all existing attachments
      - attachments=[...], append_attachments=False: replace all existing
        attachments with the new list
      - attachments=[...], append_attachments=True: keep existing AND add new

    Note: the HTML body is re-rendered from the plain text via our own
    formatter. If the draft was edited in Gmail's web UI with rich formatting,
    that formatting will be normalized back to our plain-to-HTML output.
    The plain text body itself is preserved byte-for-byte.
    """
    if (
        body is None
        and subject is None
        and to is None
        and cc is None
        and attachments is None
    ):
        raise ValidationError(
            "update_draft requires at least one field to change.",
            suggestion="Pass --body, --subject, --to, --cc, or --attach.",
        )

    service = _get_service(account)

    # Fetch existing draft to get current field values
    try:
        existing = service.users().drafts().get(
            userId="me", id=draft_id, format="full"
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Draft", draft_id)
        handle_http_error(e, "gmail update_draft (fetch)")
        raise  # unreachable

    existing_msg = existing.get("message", {})
    existing_headers = {
        h["name"].lower(): h["value"]
        for h in existing_msg.get("payload", {}).get("headers", [])
    }

    # Merge scalar fields — provided value wins, else preserve existing
    merged_to = to if to is not None else existing_headers.get("to", "")
    merged_cc = cc if cc is not None else existing_headers.get("cc")
    merged_subject = subject if subject is not None else existing_headers.get("subject", "")
    merged_body = body if body is not None else _get_body_text(existing_msg)
    thread_id = existing_msg.get("threadId")

    # Merge attachments. If new attachments aren't replacing existing ones,
    # we need to download the existing attachments so _build_message can
    # re-encode them into the updated MIME payload.
    import shutil
    import tempfile

    temp_dir: str | None = None
    try:
        need_existing = attachments is None or append_attachments
        if need_existing:
            temp_dir = tempfile.mkdtemp(prefix="gmail-update-draft-")
            existing_paths = _extract_existing_attachments(service, existing_msg, temp_dir)
            if attachments is None:
                merged_attachments = existing_paths
            else:
                merged_attachments = existing_paths + list(attachments)
        else:
            merged_attachments = list(attachments) if attachments else []

        send_as = _get_send_as(service)
        message = _build_message(
            to=merged_to,
            subject=merged_subject,
            body=merged_body,
            cc=merged_cc,
            from_name=send_as.get("displayName"),
            from_email=send_as.get("email"),
            signature_html=send_as.get("signature"),
            thread_id=thread_id,
            attachments=merged_attachments if merged_attachments else None,
        )

        try:
            result = service.users().drafts().update(
                userId="me", id=draft_id, body={"message": message}
            ).execute()
        except HttpError as e:
            resp = getattr(e, "resp", None)
            if resp is not None and resp.status == 404:
                raise NotFoundError("Draft", draft_id)
            handle_http_error(e, "gmail update_draft")
            raise  # unreachable
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "updated": True,
        "id": result["id"],
        "messageId": result["message"]["id"],
    }


def send_draft(draft_id: str, account: str | None = None) -> dict:
    """Send an existing draft."""
    service = _get_service(account)
    try:
        result = service.users().drafts().send(
            userId="me", body={"id": draft_id}
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Draft", draft_id)
        handle_http_error(e, "gmail send_draft")
        raise  # unreachable
    return {
        "sent": True,
        "id": result["id"],
        "threadId": result.get("threadId"),
    }


def delete_draft(draft_id: str, account: str | None = None) -> dict:
    """Delete a draft. Permanent — Gmail does not trash drafts."""
    service = _get_service(account)
    try:
        service.users().drafts().delete(
            userId="me", id=draft_id
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Draft", draft_id)
        handle_http_error(e, "gmail delete_draft")
        raise  # unreachable
    return {"deleted": True, "id": draft_id}


def labels(account: str | None = None) -> dict:
    """List all Gmail labels."""
    service = _get_service(account)
    try:
        result = service.users().labels().list(userId="me").execute()
    except HttpError as e:
        handle_http_error(e, "gmail labels")
        raise  # unreachable
    label_list = [
        {"id": lbl["id"], "name": lbl["name"], "type": lbl.get("type", "")}
        for lbl in result.get("labels", [])
    ]
    return {"labels": label_list, "count": len(label_list)}


def download_attachments(
    message_id: str,
    dest: str,
    account: str | None = None,
    extensions: list[str] | None = None,
) -> dict:
    """Download attachments from a message to a destination directory.

    extensions: optional list of file extensions to filter on (e.g. ["pdf",
    "docx"]). Case-insensitive, leading dots ignored. None = download all.

    Returns dict with list of saved file paths and count.
    """
    service = _get_service(account)
    try:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Message", message_id)
        handle_http_error(e, "gmail download_attachments")
        raise

    ext_filter: set[str] | None = None
    if extensions:
        ext_filter = {e.lower().lstrip(".") for e in extensions if e}

    attachments: list[dict] = []
    _walk_attachments(msg.get("payload", {}), attachments)

    if ext_filter is not None:
        attachments = [
            a for a in attachments
            if os.path.splitext(a["filename"])[1].lower().lstrip(".") in ext_filter
        ]

    os.makedirs(dest, exist_ok=True)
    saved = []
    for att_meta in attachments:
        att = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=att_meta["attachmentId"]
        ).execute()
        data = base64.urlsafe_b64decode(att["data"])
        filepath = os.path.join(dest, att_meta["filename"])
        with open(filepath, "wb") as f:
            f.write(data)
        saved.append({"filename": att_meta["filename"], "path": filepath, "size": len(data)})

    result = {"files": saved, "count": len(saved), "dest": dest}
    if ext_filter is not None:
        result["filter"] = sorted(ext_filter)
    return result


def trash(message_id: str, account: str | None = None) -> dict:
    """Move a message to Trash."""
    service = _get_service(account)
    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
    except HttpError as e:
        handle_http_error(e, "gmail trash")
        raise  # unreachable
    return {"id": message_id, "trashed": True}


def scan(
    since: str = "1d",
    account: str | None = None,
    limit: int = 100,
) -> dict:
    """Scan inbox for recent messages. 'since' accepts: 1d, 2d, 1w, etc."""
    # Parse since string — guard against empty or single-char input
    if not since or len(since) < 2:
        raise ValidationError(
            f"Invalid --since value: '{since}'. Use format: 1d, 2d, 1w",
            suggestion="Examples: 1d (1 day), 3d (3 days), 1w (1 week)",
        )
    unit = since[-1]
    try:
        num = int(since[:-1])
    except ValueError:
        raise ValidationError(
            f"Invalid --since value: '{since}'. Use format: 1d, 2d, 1w",
            suggestion="Examples: 1d (1 day), 3d (3 days), 1w (1 week)",
        )

    if num <= 0:
        raise ValidationError(
            f"Time value must be positive, got: {num}",
            suggestion="Examples: 1d, 3d, 1w, 12h",
        )

    if unit == "h":
        # Use newer_than for hours to get sub-day precision
        return list_messages(query=f"newer_than:{num}h in:inbox", limit=limit, account=account)

    if unit not in _VALID_SINCE_UNITS:
        raise ValidationError(
            f"Invalid time unit: '{unit}'. Valid units: d (days), w (weeks), h (hours).",
            suggestion="Examples: 1d, 3d, 1w, 12h",
        )

    days = _VALID_SINCE_UNITS[unit] * num
    after = datetime.now(timezone.utc) - timedelta(days=days)
    query = f"after:{after.strftime('%Y/%m/%d')} in:inbox"
    return list_messages(query=query, limit=limit, account=account)

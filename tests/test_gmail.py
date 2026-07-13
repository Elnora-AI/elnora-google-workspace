"""Tests for gmail module — mocked Google API tests for send, draft, list, get, reply, labels, scan."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from output import AuthError, CliError, NotFoundError, RateLimitError, ValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_service():
    """Create a mock Gmail service."""
    service = MagicMock()
    return service


@pytest.fixture
def patch_build_service(mock_service):
    """Patch build_service to return our mock."""
    with patch("gmail.build_service", return_value=mock_service):
        import gmail
        yield gmail, mock_service


# ---------------------------------------------------------------------------
# _sanitize_header
# ---------------------------------------------------------------------------

def test_sanitize_header_removes_crlf():
    import gmail
    assert gmail._sanitize_header("test\r\ninjection") == "testinjection"
    assert gmail._sanitize_header("test\x00null") == "testnull"


def test_sanitize_header_clean_string():
    import gmail
    assert gmail._sanitize_header("Normal Subject") == "Normal Subject"


# ---------------------------------------------------------------------------
# _build_message
# ---------------------------------------------------------------------------

def test_build_message_basic():
    import gmail
    msg = gmail._build_message(to="user@example.com", subject="Hi", body="Hello")
    assert "raw" in msg
    assert "threadId" not in msg


def test_build_message_with_cc():
    import gmail
    msg = gmail._build_message(to="a@b.com", subject="Hi", body="Hello", cc="c@d.com")
    assert "raw" in msg


def test_build_message_invalid_email():
    import gmail
    with pytest.raises(ValidationError, match="Invalid email"):
        gmail._build_message(to="not-an-email", subject="Hi", body="Hello")


def test_build_message_invalid_cc():
    import gmail
    with pytest.raises(ValidationError, match="Invalid email"):
        gmail._build_message(to="a@b.com", subject="Hi", body="Hello", cc="bad")


def test_build_message_with_thread():
    import gmail
    msg = gmail._build_message(
        to="a@b.com", subject="Re: Hi", body="Reply",
        in_reply_to="<msg123@mail>", references="<msg123@mail>", thread_id="thread456"
    )
    assert msg["threadId"] == "thread456"


# ---------------------------------------------------------------------------
# _format_message
# ---------------------------------------------------------------------------

def test_format_message_extracts_headers():
    import gmail
    raw_msg = {
        "id": "msg1",
        "threadId": "thread1",
        "snippet": "Hello world",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 12:00:00 +0000"},
            ]
        }
    }
    formatted = gmail._format_message(raw_msg)
    assert formatted["id"] == "msg1"
    assert formatted["from"] == "sender@example.com"
    assert formatted["subject"] == "Test"
    assert formatted["snippet"] == "Hello world"
    assert "attachments" not in formatted


def test_format_message_surfaces_attachments():
    """get/get-thread must expose attachment metadata so agents don't need
    a second download call to know what's attached. Real-world repro: Linear
    receipts arrive with Invoice + Receipt PDFs as nested multipart parts."""
    import gmail
    raw_msg = {
        "id": "msg2",
        "threadId": "thread2",
        "payload": {
            "headers": [{"name": "Subject", "value": "Receipt"}],
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/plain", "body": {"data": "aGk="}},
                ]},
                {
                    "mimeType": "application/pdf",
                    "filename": "Invoice-F3R3DRNJ-0007.pdf",
                    "body": {"attachmentId": "att-1", "size": 78420},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "Receipt-2437-9985.pdf",
                    "body": {"attachmentId": "att-2", "size": 79234},
                },
            ],
        },
    }
    formatted = gmail._format_message(raw_msg)
    assert formatted["attachments"] == [
        {"filename": "Invoice-F3R3DRNJ-0007.pdf", "mimeType": "application/pdf",
         "size": 78420, "attachmentId": "att-1"},
        {"filename": "Receipt-2437-9985.pdf", "mimeType": "application/pdf",
         "size": 79234, "attachmentId": "att-2"},
    ]


def test_format_message_ignores_inline_parts():
    """Inline body parts (no filename, no attachmentId) must not appear as
    attachments, even though they show up as parts in the payload."""
    import gmail
    raw_msg = {
        "id": "msg3",
        "payload": {
            "headers": [],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "aGk="}},
                {"mimeType": "text/html", "body": {"data": "PGI+aGk8L2I+"}},
            ],
        },
    }
    formatted = gmail._format_message(raw_msg)
    assert "attachments" not in formatted


# ---------------------------------------------------------------------------
# download_attachments
# ---------------------------------------------------------------------------

def _make_attachment_message():
    """Real-shape Gmail message with two PDFs in a nested multipart payload."""
    return {
        "id": "msg-att",
        "payload": {
            "headers": [],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "aGk="}},
                {
                    "mimeType": "application/pdf",
                    "filename": "Invoice.pdf",
                    "body": {"attachmentId": "att-pdf-1", "size": 100},
                },
                {
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "filename": "Notes.docx",
                    "body": {"attachmentId": "att-doc-1", "size": 200},
                },
            ],
        },
    }


def _wire_download_mocks(mock_svc, attachment_data: dict):
    """Configure a mock Gmail service to return the given message + attachment bytes."""
    import base64
    mock_svc.users().messages().get().execute.return_value = _make_attachment_message()

    def _att_get(userId, messageId, id):
        result = MagicMock()
        result.execute.return_value = {
            "data": base64.urlsafe_b64encode(attachment_data[id]).decode()
        }
        return result

    mock_svc.users().messages().attachments().get.side_effect = _att_get


def test_download_attachments_all(patch_build_service, tmp_path):
    gmail, mock_svc = patch_build_service
    _wire_download_mocks(mock_svc, {"att-pdf-1": b"PDF-bytes", "att-doc-1": b"DOCX-bytes"})

    result = gmail.download_attachments(message_id="msg-att", dest=str(tmp_path))

    assert result["count"] == 2
    filenames = sorted(f["filename"] for f in result["files"])
    assert filenames == ["Invoice.pdf", "Notes.docx"]
    assert (tmp_path / "Invoice.pdf").read_bytes() == b"PDF-bytes"
    assert (tmp_path / "Notes.docx").read_bytes() == b"DOCX-bytes"


def test_download_attachments_filter_pdf_only(patch_build_service, tmp_path):
    gmail, mock_svc = patch_build_service
    _wire_download_mocks(mock_svc, {"att-pdf-1": b"PDF-bytes", "att-doc-1": b"DOCX-bytes"})

    result = gmail.download_attachments(
        message_id="msg-att", dest=str(tmp_path), extensions=["pdf"],
    )

    assert result["count"] == 1
    assert result["files"][0]["filename"] == "Invoice.pdf"
    assert result["filter"] == ["pdf"]
    assert not (tmp_path / "Notes.docx").exists()


def test_download_attachments_filter_normalizes_dots_and_case(patch_build_service, tmp_path):
    gmail, mock_svc = patch_build_service
    _wire_download_mocks(mock_svc, {"att-pdf-1": b"PDF-bytes", "att-doc-1": b"DOCX-bytes"})

    result = gmail.download_attachments(
        message_id="msg-att", dest=str(tmp_path), extensions=[".PDF", "DOCX"],
    )

    assert result["count"] == 2
    assert result["filter"] == ["docx", "pdf"]


# ---------------------------------------------------------------------------
# _get_body_text
# ---------------------------------------------------------------------------

def test_get_body_text_plain():
    import gmail
    import base64
    body_data = base64.urlsafe_b64encode(b"Hello plain text").decode()
    msg = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": body_data},
        }
    }
    assert gmail._get_body_text(msg) == "Hello plain text"


def test_get_body_text_multipart():
    import gmail
    import base64
    body_data = base64.urlsafe_b64encode(b"Nested text").decode()
    msg = {
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(b"<b>HTML</b>").decode()}},
                {"mimeType": "text/plain", "body": {"data": body_data}},
            ]
        }
    }
    assert gmail._get_body_text(msg) == "Nested text"


def test_get_body_text_empty():
    import gmail
    msg = {"payload": {"mimeType": "text/html", "body": {}}}
    assert gmail._get_body_text(msg) == ""


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

def test_send_success(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().messages().send().execute.return_value = {
        "id": "msg123", "threadId": "thread456"
    }
    result = gmail_mod.send(to="user@example.com", subject="Hi", body="Hello")
    assert result["sent"] is True
    assert result["id"] == "msg123"


def test_send_http_error_429(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 429
    error = HttpError(resp, b'{"error": {"message": "rate limit"}}')
    mock_svc.users().messages().send().execute.side_effect = error
    with pytest.raises(RateLimitError):
        gmail_mod.send(to="user@example.com", subject="Hi", body="Hello")


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

def test_draft_success(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "draft1", "message": {"id": "msg1"}
    }
    result = gmail_mod.draft(to="user@example.com", subject="Hi", body="Hello")
    assert result["drafted"] is True
    assert result["id"] == "draft1"


# ---------------------------------------------------------------------------
# _parse_addresses
# ---------------------------------------------------------------------------

def test_parse_addresses_display_names():
    import gmail
    result = gmail._parse_addresses('Alice <alice@x.com>, "Bob Smith" <bob@y.com>, carol@z.com')
    assert result == ["alice@x.com", "bob@y.com", "carol@z.com"]


def test_parse_addresses_empty():
    import gmail
    assert gmail._parse_addresses("") == []
    assert gmail._parse_addresses(None) == []


def test_parse_addresses_lowercases():
    import gmail
    assert gmail._parse_addresses("ALICE@X.COM") == ["alice@x.com"]


# ---------------------------------------------------------------------------
# reply / draft_reply / reply_all / draft_reply_all — shared semantics
#
# These all flow through _prepare_reply_payload(), so the semantics tests
# exercise that helper via the public draft_reply() wrapper (chosen because
# drafts.create returns a simple shape that's easy to mock). Per-wrapper
# tests below just verify each function calls the right API endpoint.
# ---------------------------------------------------------------------------

def _make_original_message(
    message_id: str = "msg-orig",
    thread_id: str = "thread-orig",
    from_: str = "Alice <alice@x.com>",
    to: str = '"Me" <me@example.com>, carol@y.com',
    cc: str | None = "Dave <dave@z.com>, eve@z.com",
    subject: str = "Original subject",
) -> dict:
    """Build a mock messages.get() metadata-format response."""
    headers = [
        {"name": "From", "value": from_},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{message_id}@mail>"},
    ]
    if cc is not None:
        headers.append({"name": "Cc", "value": cc})
    return {
        "id": message_id,
        "threadId": thread_id,
        "payload": {"headers": headers},
    }


def _mock_send_as(mock_svc, email: str = "me@example.com", display_name: str = "Me"):
    """Configure the sendAs list API to return a primary matching our email."""
    mock_svc.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "isPrimary": True,
                "displayName": display_name,
                "sendAsEmail": email,
                "signature": "",
            }
        ]
    }


def _decode_sent_message(mock_svc, api: str = "drafts"):
    """Decode the raw MIME from the last drafts.create() or messages.send() call.

    Returns (headers_dict_lowercased, plain_body).
    """
    import base64 as _b64
    from email import message_from_bytes

    if api == "drafts":
        calls = mock_svc.users().drafts().create.call_args_list
    elif api == "send":
        calls = mock_svc.users().messages().send.call_args_list
    else:
        raise ValueError(f"unknown api: {api}")

    # Find the last call where body.message.raw is set (ignore the empty mock-chain calls)
    real_call = None
    for c in reversed(calls):
        body = c.kwargs.get("body")
        if body and isinstance(body, dict):
            msg = body.get("message") if api == "drafts" else body
            if msg and "raw" in msg:
                real_call = (msg, c)
                break
    assert real_call is not None, f"no real {api} call found"
    msg_payload, _call = real_call

    raw = msg_payload["raw"]
    mime_bytes = _b64.urlsafe_b64decode(raw.encode())
    parsed = message_from_bytes(mime_bytes)
    headers = {k.lower(): v for k, v in parsed.items()}
    plain_body = ""
    for part in parsed.walk():
        if part.is_multipart():
            continue
        if part.get_content_type() == "text/plain" and not plain_body:
            plain_body = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return headers, plain_body, msg_payload


def test_reply_preserves_original_cc(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    result = gmail_mod.draft_reply(message_id="msg-orig", body="thanks")

    assert result["drafted"] is True
    headers, _, msg_payload = _decode_sent_message(mock_svc, api="drafts")
    # To = From (alice), Cc preserved (dave + eve), self (me) excluded
    assert headers["to"] == "alice@x.com"
    assert "dave@z.com" in headers["cc"]
    assert "eve@z.com" in headers["cc"]
    assert "me@example.com" not in headers.get("cc", "")
    # Thread preserved
    assert msg_payload.get("threadId") == "thread-orig"
    # Subject prefixed with Re:
    assert headers["subject"] == "Re: Original subject"


def test_reply_explicit_cc_override(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply(message_id="msg-orig", body="thanks", cc="frank@w.com")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    assert headers["cc"] == "frank@w.com"
    assert "dave@z.com" not in headers["cc"]


def test_reply_no_cc_clears(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply(message_id="msg-orig", body="thanks", no_cc=True)

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    assert headers.get("cc") is None or headers.get("cc") == ""


def test_reply_cc_and_no_cc_conflict_raises(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()

    with pytest.raises(ValidationError, match="mutually exclusive"):
        gmail_mod.draft_reply(
            message_id="msg-orig",
            body="thanks",
            cc="frank@w.com",
            no_cc=True,
        )


def test_reply_self_from_flips_to_first_original_to(patch_build_service):
    """When I'm replying to my own sent message, To flips to the original recipient."""
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    # Message I sent to alice, cc dave
    mock_svc.users().messages().get().execute.return_value = _make_original_message(
        from_="Me <me@example.com>",
        to="Alice <alice@x.com>, bob@y.com",
        cc="dave@z.com",
    )
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply(message_id="msg-orig", body="follow-up")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    # Plain reply in self-from mode: To = first address in original To
    assert headers["to"] == "alice@x.com"
    # Cc preserved from original
    assert "dave@z.com" in headers["cc"]


def test_reply_all_expands_to_and_cc(patch_build_service):
    """Reply-all: To=From, Cc=(original To + Cc) minus self, minus From."""
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply_all(message_id="msg-orig", body="thanks all")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    assert headers["to"] == "alice@x.com"
    cc = headers["cc"]
    # Original To carol@y.com expanded into Cc
    assert "carol@y.com" in cc
    # Original Cc dave and eve preserved
    assert "dave@z.com" in cc
    assert "eve@z.com" in cc
    # Self excluded
    assert "me@example.com" not in cc
    # From excluded (it's in To now)
    assert "alice@x.com" not in cc


def test_reply_all_self_from_keeps_all_original_to(patch_build_service):
    """Reply-all on my own sent message: To = all original To, Cc = original Cc."""
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message(
        from_="Me <me@example.com>",
        to="alice@x.com, bob@y.com, carol@w.com",
        cc="dave@z.com",
    )
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply_all(message_id="msg-orig", body="follow-up to all")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    to = headers["to"]
    assert "alice@x.com" in to
    assert "bob@y.com" in to
    assert "carol@w.com" in to
    # Cc = original Cc
    assert headers["cc"] == "dave@z.com"


def test_reply_all_dedup_case_insensitive(patch_build_service):
    """Same address in both original To and Cc should not appear twice."""
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message(
        from_="alice@x.com",
        to="ME@example.com, Dave <dave@z.com>",
        cc="DAVE@Z.COM, eve@w.com",
    )
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply_all(message_id="msg-orig", body="hi all")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    cc = headers["cc"].lower()
    assert cc.count("dave@z.com") == 1
    assert "eve@w.com" in cc
    # Self excluded even with different casing
    assert "me@example.com" not in cc


def test_reply_to_override_excludes_new_to_from_cc(patch_build_service):
    """--to override should also exclude the new To from Cc (not the original From)."""
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message(
        from_="alice@x.com",
        to="me@example.com, bob@y.com",
        cc="dave@z.com",
    )
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    # Override To to dave — dave should NOT appear in Cc, but alice/bob should
    gmail_mod.draft_reply_all(
        message_id="msg-orig",
        body="hi",
        to="dave@z.com",
    )

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    assert headers["to"] == "dave@z.com"
    cc = headers["cc"]
    assert "dave@z.com" not in cc  # excluded from Cc (now in To)
    assert "alice@x.com" in cc     # original From now expands into Cc
    assert "bob@y.com" in cc


def test_reply_404_raises_not_found(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 404
    error = HttpError(resp, b'{"error": {"message": "Not found"}}')
    mock_svc.users().messages().get().execute.side_effect = error

    with pytest.raises(NotFoundError):
        gmail_mod.draft_reply(message_id="msg-nope", body="x")


def test_reply_applies_send_as_signature(patch_build_service):
    """Regression: reply() and draft_reply() must call _get_send_as so the
    signature and display name are included. Previously a silent bug."""
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "isPrimary": True,
                "displayName": "Me Elnora",
                "sendAsEmail": "me@example.com",
                "signature": "<p>-- My Signature</p>",
            }
        ]
    }
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    gmail_mod.draft_reply(message_id="msg-orig", body="thanks")

    headers, _, _ = _decode_sent_message(mock_svc, api="drafts")
    assert "Me Elnora" in headers["from"]
    assert "me@example.com" in headers["from"]


# ---------------------------------------------------------------------------
# Per-wrapper API endpoint tests (make sure each function hits the right endpoint)
# ---------------------------------------------------------------------------

def test_reply_calls_messages_send(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().messages().send().execute.return_value = {
        "id": "msg-sent", "threadId": "thread-orig"
    }

    result = gmail_mod.reply(message_id="msg-orig", body="ok")

    assert result["replied"] is True
    assert result["id"] == "msg-sent"
    # drafts.create was NOT called
    mock_svc.users().drafts().create.assert_not_called()


def test_reply_all_calls_messages_send(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().messages().send().execute.return_value = {
        "id": "msg-sent", "threadId": "thread-orig"
    }

    result = gmail_mod.reply_all(message_id="msg-orig", body="ok all")

    assert result["replied_all"] is True
    assert result["id"] == "msg-sent"
    mock_svc.users().drafts().create.assert_not_called()


def test_draft_reply_all_calls_drafts_create(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    _mock_send_as(mock_svc)
    mock_svc.users().messages().get().execute.return_value = _make_original_message()
    mock_svc.users().drafts().create().execute.return_value = {
        "id": "r-new", "message": {"id": "msg-new"}
    }

    result = gmail_mod.draft_reply_all(message_id="msg-orig", body="ok all")

    assert result["drafted_all"] is True
    assert result["id"] == "r-new"
    # messages.send was NOT called for the drafts path
    mock_svc.users().messages().send.assert_not_called()


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------

def test_list_messages_empty(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().messages().list().execute.return_value = {"messages": []}
    result = gmail_mod.list_messages(query="is:unread", limit=10)
    assert result["count"] == 0
    assert result["messages"] == []


def test_list_messages_routes_in_drafts_to_list_drafts(patch_build_service):
    """Query containing 'in:drafts' should transparently route to drafts.list."""
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "r-7", "message": {"id": "msg-7", "threadId": "t-7"}},
        ],
    }
    mock_svc.new_batch_http_request.side_effect = _make_batch_mock({
        "msg-7": {
            "id": "msg-7",
            "threadId": "t-7",
            "snippet": "draft content",
            "payload": {"headers": [
                {"name": "From", "value": "me@example.com"},
                {"name": "To", "value": "x@y.com"},
                {"name": "Subject", "value": "wip"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 12:00:00 +0000"},
            ]},
        },
    })

    result = gmail_mod.list_messages(query="in:drafts", limit=5)

    # Routed to drafts shape — key is "drafts", not "messages"
    assert "drafts" in result
    assert result["drafts"][0]["draftId"] == "r-7"
    # messages.list was NOT called
    mock_svc.users().messages().list.assert_not_called()


def test_list_messages_routes_is_draft_to_list_drafts(patch_build_service):
    """The alternate 'is:draft' token also routes, and the token is stripped
    from the forwarded query so drafts.list can actually match siblings."""
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().list().execute.return_value = {"drafts": []}

    result = gmail_mod.list_messages(query="is:draft from:foo", limit=5)

    assert "drafts" in result
    assert result["count"] == 0
    mock_svc.users().messages().list.assert_not_called()

    # The 'is:draft' token was stripped — only 'from:foo' reached drafts.list
    drafts_list_calls = [
        c.kwargs for c in mock_svc.users().drafts().list.call_args_list
        if c.kwargs.get("maxResults") == 5
    ]
    assert len(drafts_list_calls) >= 1
    assert drafts_list_calls[0]["q"] == "from:foo"


def test_list_messages_plain_query_still_hits_messages_list(patch_build_service):
    """Queries that don't mention drafts should still hit messages.list."""
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().messages().list().execute.return_value = {"messages": []}

    gmail_mod.list_messages(query="from:foo@bar.com", limit=5)

    # messages.list WAS called; drafts.list was NOT
    assert mock_svc.users().messages().list.called
    mock_svc.users().drafts().list.assert_not_called()


def test_list_messages_empty_query_unchanged(patch_build_service):
    """Empty query is not routed (no draft token to match)."""
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().messages().list().execute.return_value = {"messages": []}

    gmail_mod.list_messages(query="", limit=5)

    assert mock_svc.users().messages().list.called
    mock_svc.users().drafts().list.assert_not_called()


# ---------------------------------------------------------------------------
# list_drafts
# ---------------------------------------------------------------------------

def _make_batch_mock(message_responses: dict):
    """Factory for mocking service.new_batch_http_request.

    Returns a callable that, when invoked with callback=..., yields a batch
    object whose .add() queues request_ids and whose .execute() fires the
    callback once per queued request with the pre-canned response from
    message_responses (keyed by request_id).
    """
    def _factory(callback=None):
        pending: list[str] = []
        batch = MagicMock()

        def _add(request, request_id):
            pending.append(request_id)

        def _execute():
            for req_id in pending:
                if req_id in message_responses:
                    callback(req_id, message_responses[req_id], None)
                else:
                    callback(req_id, None, Exception(f"no canned response for {req_id}"))
            pending.clear()

        batch.add.side_effect = _add
        batch.execute.side_effect = _execute
        return batch
    return _factory


def test_list_drafts_empty(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().list().execute.return_value = {}
    result = gmail_mod.list_drafts(query="", limit=10)
    assert result["count"] == 0
    assert result["drafts"] == []
    assert result["query"] == ""


def test_list_drafts_success(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "r-1", "message": {"id": "msg-a", "threadId": "t-a"}},
            {"id": "r-2", "message": {"id": "msg-b", "threadId": "t-b"}},
        ],
    }
    message_responses = {
        "msg-a": {
            "id": "msg-a",
            "threadId": "t-a",
            "snippet": "hi alice",
            "payload": {"headers": [
                {"name": "From", "value": "me@example.com"},
                {"name": "To", "value": "alice@example.com"},
                {"name": "Subject", "value": "draft to alice"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 12:00:00 +0000"},
            ]},
        },
        "msg-b": {
            "id": "msg-b",
            "threadId": "t-b",
            "snippet": "hi bob",
            "payload": {"headers": [
                {"name": "From", "value": "me@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Subject", "value": "draft to bob"},
                {"name": "Date", "value": "Tue, 2 Jan 2026 12:00:00 +0000"},
            ]},
        },
    }
    mock_svc.new_batch_http_request.side_effect = _make_batch_mock(message_responses)

    result = gmail_mod.list_drafts(query="", limit=10)

    assert result["count"] == 2
    assert len(result["drafts"]) == 2
    # Order is preserved by draft listing order
    assert result["drafts"][0]["draftId"] == "r-1"
    assert result["drafts"][0]["id"] == "msg-a"
    assert result["drafts"][0]["to"] == "alice@example.com"
    assert result["drafts"][0]["subject"] == "draft to alice"
    assert result["drafts"][1]["draftId"] == "r-2"
    assert result["drafts"][1]["to"] == "bob@example.com"


def test_list_drafts_query_passed_through(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().list().execute.return_value = {"drafts": []}

    result = gmail_mod.list_drafts(query="from:someone@example.com", limit=5)

    assert result["count"] == 0
    assert result["query"] == "from:someone@example.com"
    # Verify the query string reached the API — find the call where maxResults=5
    call_kwargs_list = [
        c.kwargs for c in mock_svc.users().drafts().list.call_args_list
        if c.kwargs.get("maxResults") == 5
    ]
    assert len(call_kwargs_list) >= 1
    assert call_kwargs_list[0]["q"] == "from:someone@example.com"
    assert call_kwargs_list[0]["userId"] == "me"


# ---------------------------------------------------------------------------
# update_draft
# ---------------------------------------------------------------------------

def _decode_update_payload(mock_svc, draft_id: str) -> tuple[dict, str, list[str]]:
    """Return (headers, plain_body, attachment_filenames) from the last
    drafts().update() call for the given draft_id.

    Parses the base64url raw MIME the same way Gmail's backend would.
    """
    import base64 as _b64
    from email import message_from_bytes

    real_call = next(
        c for c in reversed(mock_svc.users().drafts().update.call_args_list)
        if c.kwargs.get("id") == draft_id and c.kwargs.get("body") is not None
    )
    raw = real_call.kwargs["body"]["message"]["raw"]
    mime_bytes = _b64.urlsafe_b64decode(raw.encode())
    msg = message_from_bytes(mime_bytes)

    headers = {k.lower(): v for k, v in msg.items()}
    plain_body = ""
    attachment_names: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename:
            attachment_names.append(filename)
            continue
        if part.get_content_type() == "text/plain" and not plain_body:
            plain_body = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return headers, plain_body, attachment_names


def _make_existing_draft(
    draft_id: str = "r-1",
    msg_id: str = "msg-1",
    thread_id: str = "t-1",
    to: str = "alice@example.com",
    cc: str | None = None,
    subject: str = "existing subject",
    body_text: str = "existing body",
    parts: list | None = None,
) -> dict:
    """Build a mock drafts().get() response mirroring Gmail's full-format shape."""
    import base64
    headers = [
        {"name": "From", "value": "me@example.com"},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Mon, 1 Jan 2026 12:00:00 +0000"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if parts is None:
        # Simple text/plain payload, no parts
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": base64.urlsafe_b64encode(body_text.encode()).decode()},
        }
    else:
        payload = {
            "mimeType": "multipart/mixed",
            "headers": headers,
            "parts": parts,
        }
    return {
        "id": draft_id,
        "message": {
            "id": msg_id,
            "threadId": thread_id,
            "payload": payload,
            "snippet": body_text[:50],
        },
    }


def test_update_draft_no_fields_raises():
    import gmail
    with pytest.raises(ValidationError, match="at least one field"):
        gmail.update_draft(draft_id="r-1")


def test_update_draft_subject_only_preserves_other_fields(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().get().execute.return_value = _make_existing_draft(
        to="alice@example.com",
        subject="old subject",
        body_text="hi alice",
    )
    mock_svc.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "msg-1"},
    }

    result = gmail_mod.update_draft(draft_id="r-1", subject="new subject")

    assert result["updated"] is True
    assert result["id"] == "r-1"

    headers, plain_body, _ = _decode_update_payload(mock_svc, "r-1")
    assert headers["subject"] == "new subject"
    assert headers["to"] == "alice@example.com"   # existing preserved
    assert plain_body.strip() == "hi alice"          # existing body preserved


def test_update_draft_body_only_preserves_other_fields(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().get().execute.return_value = _make_existing_draft(
        to="bob@example.com",
        subject="keep this subject",
        body_text="old body",
    )
    mock_svc.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "msg-1"},
    }

    gmail_mod.update_draft(draft_id="r-1", body="fresh body text")

    headers, plain_body, _ = _decode_update_payload(mock_svc, "r-1")
    assert plain_body.strip() == "fresh body text"
    assert headers["subject"] == "keep this subject"
    assert headers["to"] == "bob@example.com"


def test_update_draft_404(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 404
    error = HttpError(resp, b'{"error": {"message": "Not found"}}')
    mock_svc.users().drafts().get().execute.side_effect = error

    with pytest.raises(NotFoundError):
        gmail_mod.update_draft(draft_id="r-nope", subject="never applied")


def test_update_draft_attach_replace_skips_existing_fetch(patch_build_service, tmp_path):
    """When --attach is given without --append-attachments, existing attachments
    are NOT downloaded (faster path, matches Google's replace semantics)."""
    gmail_mod, mock_svc = patch_build_service

    # Existing draft with an attachment part — if we erroneously fetch it,
    # the mocked attachments.get will be called
    existing = _make_existing_draft(
        parts=[
            {
                "mimeType": "text/plain",
                "headers": [],
                "body": {"data": "aGVsbG8="},  # "hello"
            },
            {
                "mimeType": "application/pdf",
                "filename": "existing.pdf",
                "headers": [],
                "body": {"attachmentId": "att-existing", "size": 100},
            },
        ],
    )
    mock_svc.users().drafts().get().execute.return_value = existing
    mock_svc.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "msg-1"},
    }

    new_pdf = tmp_path / "new.pdf"
    new_pdf.write_bytes(b"%PDF-new")

    gmail_mod.update_draft(
        draft_id="r-1",
        attachments=[str(new_pdf)],
        append_attachments=False,
    )

    # Existing attachments should NOT have been fetched
    mock_svc.users().messages().attachments().get.assert_not_called()


def test_update_draft_append_attachments_fetches_existing(patch_build_service, tmp_path):
    """With --append-attachments, existing attachments are downloaded and merged."""
    gmail_mod, mock_svc = patch_build_service

    existing = _make_existing_draft(
        parts=[
            {
                "mimeType": "text/plain",
                "headers": [],
                "body": {"data": "aGVsbG8="},  # "hello"
            },
            {
                "mimeType": "application/pdf",
                "filename": "old.pdf",
                "headers": [],
                "body": {"attachmentId": "att-old", "size": 100},
            },
        ],
    )
    mock_svc.users().drafts().get().execute.return_value = existing
    import base64 as _b64
    mock_svc.users().messages().attachments().get().execute.return_value = {
        "data": _b64.urlsafe_b64encode(b"%PDF-old-content").decode(),
    }
    mock_svc.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "msg-1"},
    }

    new_pdf = tmp_path / "new.pdf"
    new_pdf.write_bytes(b"%PDF-new")

    gmail_mod.update_draft(
        draft_id="r-1",
        attachments=[str(new_pdf)],
        append_attachments=True,
    )

    # Existing attachment was fetched
    get_calls = mock_svc.users().messages().attachments().get.call_args_list
    real_fetches = [c for c in get_calls if c.kwargs.get("id") == "att-old"]
    assert len(real_fetches) >= 1

    # The merged MIME contains both filenames
    _, _, attachment_names = _decode_update_payload(mock_svc, "r-1")
    assert "old.pdf" in attachment_names
    assert "new.pdf" in attachment_names


def test_update_draft_preserves_thread_id(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().get().execute.return_value = _make_existing_draft(
        thread_id="thread-xyz",
    )
    mock_svc.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "msg-1"},
    }

    gmail_mod.update_draft(draft_id="r-1", subject="updated")

    real_call = next(
        c for c in reversed(mock_svc.users().drafts().update.call_args_list)
        if c.kwargs.get("id") == "r-1" and c.kwargs.get("body") is not None
    )
    assert real_call.kwargs["body"]["message"]["threadId"] == "thread-xyz"


# ---------------------------------------------------------------------------
# attach-to-draft (CLI sugar command)
# ---------------------------------------------------------------------------

def test_attach_to_draft_cli_delegates_with_append_true(tmp_path):
    """attach-to-draft is pure CLI sugar: it must call update_draft with
    append_attachments=True so existing attachments are preserved."""
    from click.testing import CliRunner
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
    from gw import cli as gw_cli  # type: ignore
    import output as output_mod

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-test")

    import gmail as gmail_mod
    called_with: dict = {}
    captured: list[str] = []

    def fake_update_draft(**kwargs):
        called_with.update(kwargs)
        return {"updated": True, "id": kwargs["draft_id"], "messageId": "msg-x"}

    def fake_stdout(text):
        captured.append(text)

    with patch.object(gmail_mod, "update_draft", side_effect=fake_update_draft), \
         patch.object(output_mod, "_write_stdout", side_effect=fake_stdout):
        runner = CliRunner()
        result = runner.invoke(
            gw_cli,
            ["gmail", "attach-to-draft", "r-1", "--attach", str(pdf), "--compact"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert called_with["draft_id"] == "r-1"
    assert called_with["attachments"] == [str(pdf)]
    assert called_with["append_attachments"] is True
    # Output includes the attached count annotation
    output_text = "".join(captured)
    assert '"attached":1' in output_text or '"attached": 1' in output_text


# ---------------------------------------------------------------------------
# delete_draft
# ---------------------------------------------------------------------------

def test_delete_draft_success(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().drafts().delete().execute.return_value = ""
    result = gmail_mod.delete_draft(draft_id="r-1")
    assert result == {"deleted": True, "id": "r-1"}


def test_delete_draft_404(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 404
    error = HttpError(resp, b'{"error": {"message": "Not found"}}')
    mock_svc.users().drafts().delete().execute.side_effect = error
    with pytest.raises(NotFoundError):
        gmail_mod.delete_draft(draft_id="r-nope")


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def test_labels_success(patch_build_service):
    gmail_mod, mock_svc = patch_build_service
    mock_svc.users().labels().list().execute.return_value = {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_1", "name": "Custom", "type": "user"},
        ]
    }
    result = gmail_mod.labels()
    assert result["count"] == 2
    assert result["labels"][0]["name"] == "INBOX"


# ---------------------------------------------------------------------------
# scan — input validation
# ---------------------------------------------------------------------------

def test_scan_invalid_empty():
    import gmail
    with pytest.raises(ValidationError, match="Invalid --since"):
        gmail.scan(since="")


def test_scan_invalid_unit():
    import gmail
    with pytest.raises(ValidationError, match="Valid units"):
        gmail.scan(since="3x")


def test_scan_zero_value():
    import gmail
    with pytest.raises(ValidationError, match="positive"):
        gmail.scan(since="0d")


def test_scan_negative_value():
    import gmail
    with pytest.raises(ValidationError, match="positive"):
        gmail.scan(since="-1d")

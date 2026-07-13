"""Google Forms operations — read and write.

Wraps the Google Forms API v1. Read side: `get`, `responses_list`, `response_get`.
Write side: `create`, `add_items`, `update_info`, `delete_item`. Item structure
is genuinely schema-heavy, so writes use a friendly spec dict (see
`_item_spec_to_api`) loaded from JSON on the CLI side rather than per-question
flags.
"""

from __future__ import annotations

import re

from auth import build_service
from googleapiclient.errors import HttpError
from output import NotFoundError, ValidationError, handle_http_error


def _get_service(account: str | None = None):
    return build_service("forms", "v1", account)


def _extract_form_id(form_id_or_url: str) -> str:
    """Extract a form ID from a Google Forms URL, or return the input as-is.

    Accepts:
      - bare form ID (e.g. "1-QkazdtIxAAEx...")
      - edit URL: https://docs.google.com/forms/d/<FORM_ID>/edit

    Rejects published responder URLs of the form
    https://docs.google.com/forms/d/e/<E_ID>/viewform — the segment after
    /d/e/ is a publication ID, not a form ID, and the API will not accept it.
    """
    # Reject /d/e/ responder URLs explicitly with a useful error.
    if re.search(r"/forms/d/e/", form_id_or_url):
        raise ValidationError(
            "This is a responder URL (.../forms/d/e/.../viewform). It contains a "
            "publication ID, not a form ID. Use the editor URL "
            "(https://docs.google.com/forms/d/<FORM_ID>/edit) or the bare form ID."
        )
    match = re.search(r"/forms/d/([a-zA-Z0-9_-]+)", form_id_or_url)
    if match:
        return match.group(1)
    return form_id_or_url


def _question_title_map(form: dict) -> dict[str, str]:
    """Build {questionId: title} from a form's items, for readable response output."""
    qid_to_title: dict[str, str] = {}
    for item in form.get("items", []):
        title = item.get("title", "")
        qi = item.get("questionItem")
        if qi:
            qid = qi.get("question", {}).get("questionId")
            if qid:
                qid_to_title[qid] = title
            continue
        # questionGroupItem (grid questions) — each row has its own questionId
        qgi = item.get("questionGroupItem")
        if qgi:
            for q in qgi.get("questions", []):
                qid = q.get("questionId")
                if qid:
                    row_title = q.get("rowQuestion", {}).get("title") or title
                    qid_to_title[qid] = row_title
    return qid_to_title


def _flatten_answer(ans: dict) -> list[str]:
    """Flatten a single answer dict into a list of string values."""
    text = ans.get("textAnswers", {}).get("answers", [])
    if text:
        return [a.get("value", "") for a in text]
    file_uploads = ans.get("fileUploadAnswers", {}).get("answers", [])
    if file_uploads:
        return [a.get("fileName", a.get("fileId", "")) for a in file_uploads]
    return []


def get(form_id: str, account: str | None = None) -> dict:
    """Get form metadata: title, description, settings, item count, publish state."""
    service = _get_service(account)
    form_id = _extract_form_id(form_id)
    try:
        form = service.forms().get(formId=form_id).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms get")
        raise

    info = form.get("info", {})
    items = form.get("items", [])
    return {
        "formId": form.get("formId"),
        "title": info.get("title"),
        "documentTitle": info.get("documentTitle"),
        "description": info.get("description"),
        "responderUri": form.get("responderUri"),
        "revisionId": form.get("revisionId"),
        "itemCount": len(items),
        "settings": form.get("settings", {}),
        "publishSettings": form.get("publishSettings", {}),
        "items": [
            {
                "itemId": it.get("itemId"),
                "title": it.get("title"),
                "type": _item_type(it),
                "required": _item_required(it),
            }
            for it in items
        ],
    }


def _item_type(item: dict) -> str:
    if "questionItem" in item:
        q = item["questionItem"].get("question", {})
        for key in (
            "textQuestion",
            "choiceQuestion",
            "scaleQuestion",
            "dateQuestion",
            "timeQuestion",
            "fileUploadQuestion",
            "rowQuestion",
            "ratingQuestion",
        ):
            if key in q:
                return key
        return "questionItem"
    if "questionGroupItem" in item:
        return "questionGroupItem"
    if "pageBreakItem" in item:
        return "section"
    if "textItem" in item:
        return "text"
    if "imageItem" in item:
        return "image"
    if "videoItem" in item:
        return "video"
    return "unknown"


def _item_required(item: dict) -> bool:
    qi = item.get("questionItem")
    if qi:
        return bool(qi.get("question", {}).get("required", False))
    return False


def responses_list(
    form_id: str,
    page_size: int | None = None,
    include_answers: bool = True,
    account: str | None = None,
) -> dict:
    """List form responses with question titles mapped onto each answer.

    Args:
        form_id: Form ID or editor URL.
        page_size: Cap the page size (default: API default, ~5000).
        include_answers: When False, return only response metadata
            (responseId, timestamps) — useful for counting submissions cheaply.
        account: Account selector.

    Returns:
        {"formId": ..., "count": N, "responses": [...]}.
    """
    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    qid_to_title: dict[str, str] = {}
    if include_answers:
        try:
            form = service.forms().get(formId=form_id).execute()
        except HttpError as e:
            resp = getattr(e, "resp", None)
            if resp is not None and resp.status == 404:
                raise NotFoundError("Form", form_id)
            handle_http_error(e, "forms responses (form lookup)")
            raise
        qid_to_title = _question_title_map(form)

    out: list[dict] = []
    page_token: str | None = None
    try:
        while True:
            kwargs: dict = {"formId": form_id}
            if page_size:
                kwargs["pageSize"] = page_size
            if page_token:
                kwargs["pageToken"] = page_token
            page = service.forms().responses().list(**kwargs).execute()
            for r in page.get("responses", []):
                out.append(_format_response(r, qid_to_title, include_answers))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms responses list")
        raise

    return {"formId": form_id, "count": len(out), "responses": out}


def response_get(
    form_id: str,
    response_id: str,
    account: str | None = None,
) -> dict:
    """Get a single response by ID, with question titles mapped onto answers."""
    service = _get_service(account)
    form_id = _extract_form_id(form_id)
    try:
        form = service.forms().get(formId=form_id).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms response get (form lookup)")
        raise
    qid_to_title = _question_title_map(form)
    try:
        r = (
            service.forms()
            .responses()
            .get(formId=form_id, responseId=response_id)
            .execute()
        )
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Response", response_id)
        handle_http_error(e, "forms response get")
        raise
    return _format_response(r, qid_to_title, include_answers=True)


def _format_response(r: dict, qid_to_title: dict[str, str], include_answers: bool) -> dict:
    base = {
        "responseId": r.get("responseId"),
        "createTime": r.get("createTime"),
        "lastSubmittedTime": r.get("lastSubmittedTime"),
        "respondentEmail": r.get("respondentEmail"),
    }
    if not include_answers:
        return base
    answers_out: list[dict] = []
    for qid, ans in r.get("answers", {}).items():
        answers_out.append(
            {
                "questionId": qid,
                "question": qid_to_title.get(qid),
                "values": _flatten_answer(ans),
            }
        )
    base["answers"] = answers_out
    return base


# ---------------------------------------------------------------------------
# Write side
# ---------------------------------------------------------------------------

_CHOICE_TYPE_MAP = {"radio": "RADIO", "checkbox": "CHECKBOX", "dropdown": "DROP_DOWN"}


def _item_spec_to_api(spec: dict) -> dict:
    """Translate a friendly item spec to the Forms API item dict.

    Spec shapes (each a JSON object):

    - section:       {"type": "section", "title": "...", "description": "..."}
    - text:          {"type": "text", "title": "...", "paragraph": false, "required": false}
    - paragraph:     {"type": "paragraph", "title": "..."}  # alias: text + paragraph=true
    - linear_scale:  {"type": "linear_scale", "title": "...", "low": 1, "high": 10,
                      "low_label": "...", "high_label": "...", "required": true}
    - radio:         {"type": "radio", "title": "...", "options": ["A", "B"], "shuffle": false,
                      "required": true}
    - checkbox:      {"type": "checkbox", "title": "...", "options": ["A", "B"]}
    - dropdown:      {"type": "dropdown", "title": "...", "options": ["A", "B"]}
    - date:          {"type": "date", "title": "...", "include_time": false, "include_year": true}

    All question types accept an optional "description" string and a "required" boolean.
    """
    if not isinstance(spec, dict):
        raise ValidationError(
            "Item spec must be a JSON object.",
            suggestion=f"Got: {type(spec).__name__}",
        )
    item_type = spec.get("type")
    if not item_type:
        raise ValidationError(
            "Item spec missing 'type' field.",
            suggestion="Valid types: section, text, paragraph, linear_scale, radio, checkbox, dropdown, date.",
        )
    title = spec.get("title", "")

    if item_type == "section":
        item: dict = {"title": title, "pageBreakItem": {}}
        if spec.get("description"):
            item["description"] = spec["description"]
        return item

    if not title:
        raise ValidationError(f"Item of type '{item_type}' requires non-empty 'title'.")

    question: dict = {"required": bool(spec.get("required", False))}

    if item_type in ("text", "paragraph"):
        paragraph = bool(spec.get("paragraph", item_type == "paragraph"))
        question["textQuestion"] = {"paragraph": paragraph}
    elif item_type == "linear_scale":
        low = spec.get("low", 1)
        high = spec.get("high", 5)
        if not (isinstance(low, int) and isinstance(high, int) and low < high):
            raise ValidationError(
                f"linear_scale requires integer low<high (got low={low!r}, high={high!r}).",
            )
        question["scaleQuestion"] = {
            "low": low,
            "high": high,
            "lowLabel": spec.get("low_label", ""),
            "highLabel": spec.get("high_label", ""),
        }
    elif item_type in _CHOICE_TYPE_MAP:
        options = spec.get("options")
        if not options or not isinstance(options, list):
            raise ValidationError(
                f"'{item_type}' requires non-empty 'options' list.",
            )
        api_options: list[dict] = [{"value": str(o)} for o in options]
        if spec.get("include_other"):
            if item_type == "dropdown":
                raise ValidationError(
                    "'include_other' is not supported on dropdown questions "
                    "(only radio and checkbox).",
                )
            api_options.append({"isOther": True})
        question["choiceQuestion"] = {
            "type": _CHOICE_TYPE_MAP[item_type],
            "options": api_options,
            "shuffle": bool(spec.get("shuffle", False)),
        }
    elif item_type == "date":
        question["dateQuestion"] = {
            "includeTime": bool(spec.get("include_time", False)),
            "includeYear": bool(spec.get("include_year", True)),
        }
    else:
        raise ValidationError(
            f"Unknown item type: '{item_type}'.",
            suggestion="Valid types: section, text, paragraph, linear_scale, radio, checkbox, dropdown, date.",
        )

    item = {"title": title, "questionItem": {"question": question}}
    if spec.get("description"):
        item["description"] = spec["description"]
    return item


def create(
    title: str,
    description: str | None = None,
    items: list[dict] | None = None,
    account: str | None = None,
) -> dict:
    """Create a Google Form, optionally with items in the same call.

    Args:
        title: Form title (required).
        description: Optional form description.
        items: Optional list of item specs (see `_item_spec_to_api`).
        account: Account selector.

    Returns:
        {"formId", "title", "description", "editUrl", "responderUri", "itemCount"}.
    """
    if not title:
        raise ValidationError("Form 'title' is required.")

    # Validate item specs up front so we fail before creating an empty form.
    api_items: list[dict] = []
    if items:
        for i, spec in enumerate(items):
            try:
                api_items.append(_item_spec_to_api(spec))
            except ValidationError as e:
                raise ValidationError(
                    f"Item {i} invalid: {e.args[0] if e.args else e}",
                ) from e

    service = _get_service(account)
    try:
        form = service.forms().create(body={"info": {"title": title}}).execute()
    except HttpError as e:
        handle_http_error(e, "forms create")
        raise

    form_id = form["formId"]

    requests: list[dict] = []
    if description:
        requests.append(
            {
                "updateFormInfo": {
                    "info": {"description": description},
                    "updateMask": "description",
                }
            }
        )
    for idx, api_item in enumerate(api_items):
        requests.append({"createItem": {"item": api_item, "location": {"index": idx}}})

    if requests:
        try:
            service.forms().batchUpdate(
                formId=form_id, body={"requests": requests}
            ).execute()
        except HttpError as e:
            handle_http_error(e, "forms create (batchUpdate)")
            raise

    final = service.forms().get(formId=form_id).execute()
    return {
        "formId": form_id,
        "title": title,
        "description": description,
        "editUrl": f"https://docs.google.com/forms/d/{form_id}/edit",
        "responderUri": final.get("responderUri"),
        "itemCount": len(final.get("items", [])),
    }


def add_items(
    form_id: str,
    items: list[dict],
    at_index: int | None = None,
    account: str | None = None,
) -> dict:
    """Insert items into an existing form. Defaults to appending at the end."""
    if not items or not isinstance(items, list):
        raise ValidationError("'items' must be a non-empty list.")

    api_items = [_item_spec_to_api(spec) for spec in items]

    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    if at_index is None:
        try:
            form = service.forms().get(formId=form_id).execute()
        except HttpError as e:
            resp = getattr(e, "resp", None)
            if resp is not None and resp.status == 404:
                raise NotFoundError("Form", form_id)
            handle_http_error(e, "forms add-items (form lookup)")
            raise
        at_index = len(form.get("items", []))

    requests = [
        {"createItem": {"item": api_item, "location": {"index": at_index + i}}}
        for i, api_item in enumerate(api_items)
    ]
    try:
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms add-items (batchUpdate)")
        raise

    return {"formId": form_id, "added": len(items), "atIndex": at_index}


def update_info(
    form_id: str,
    title: str | None = None,
    description: str | None = None,
    account: str | None = None,
) -> dict:
    """Update form title and/or description."""
    if title is None and description is None:
        raise ValidationError("At least one of 'title' or 'description' must be provided.")

    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    info_body: dict = {}
    mask_parts: list[str] = []
    if title is not None:
        info_body["title"] = title
        mask_parts.append("title")
    if description is not None:
        info_body["description"] = description
        mask_parts.append("description")

    requests = [
        {
            "updateFormInfo": {
                "info": info_body,
                "updateMask": ",".join(mask_parts),
            }
        }
    ]
    try:
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms update-info")
        raise

    return {"formId": form_id, "updated": mask_parts}


def update_item(
    form_id: str,
    index: int,
    spec: dict,
    account: str | None = None,
) -> dict:
    """Replace the item at the given index with a new spec.

    Preserves the underlying questionId, so existing responses stay linked
    to the same question (the question text just gets rewritten).
    """
    if index < 0:
        raise ValidationError(f"Index must be >= 0 (got {index}).")

    api_item = _item_spec_to_api(spec)

    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    requests = [
        {
            "updateItem": {
                "item": api_item,
                "location": {"index": index},
                "updateMask": "title,description,questionItem,pageBreakItem",
            }
        }
    ]
    try:
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms update-item")
        raise

    return {"formId": form_id, "updatedIndex": index}


def move_item(
    form_id: str,
    from_index: int,
    to_index: int,
    account: str | None = None,
) -> dict:
    """Move an item from one position to another. Both indices are 0-based."""
    if from_index < 0 or to_index < 0:
        raise ValidationError(
            f"Indices must be >= 0 (got from={from_index}, to={to_index})."
        )

    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    requests = [
        {
            "moveItem": {
                "originalLocation": {"index": from_index},
                "newLocation": {"index": to_index},
            }
        }
    ]
    try:
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms move-item")
        raise

    return {"formId": form_id, "from": from_index, "to": to_index}


def delete_item(form_id: str, index: int, account: str | None = None) -> dict:
    """Delete the item at the given 0-based index."""
    if index < 0:
        raise ValidationError(f"Index must be >= 0 (got {index}).")

    service = _get_service(account)
    form_id = _extract_form_id(form_id)

    requests = [{"deleteItem": {"location": {"index": index}}}]
    try:
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    except HttpError as e:
        resp = getattr(e, "resp", None)
        if resp is not None and resp.status == 404:
            raise NotFoundError("Form", form_id)
        handle_http_error(e, "forms delete-item")
        raise

    return {"formId": form_id, "deletedIndex": index}

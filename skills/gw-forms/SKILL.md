---
name: gw-forms
description: >
  Read and write Google Forms via CLI. Get metadata, list responses, create new forms,
  add or remove items, edit title and description. Accepts form IDs or editor URLs.
  TRIGGERS: "google form", "google forms", "form responses", "form submissions",
  "create google form", "feedback form", "survey form", "whistleblower form",
  "anonymous form", "list form responses", "read form", "add question to form",
  "edit form", "delete form question"
---

# Forms

Read and write access to Google Forms via the Forms API v1. Reads are flag-driven (form ID,
page size, etc.); writes use a JSON spec file because Forms questions are too schema-heavy
for per-question CLI flags.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Read commands

All read commands accept a form ID or editor URL (`https://docs.google.com/forms/d/<FORM_ID>/edit`).
Responder URLs (`.../forms/d/e/<E_ID>/viewform`) are rejected with a clear error — the segment
after `/d/e/` is a publication ID, not a form ID, and the API will not accept it.

```bash
$CLI forms get FORM_ID --compact
$CLI forms responses FORM_ID [--no-answers] [--page-size N] --compact
$CLI forms response FORM_ID RESPONSE_ID --compact
```

## Write commands

```bash
$CLI forms create --title "..." [--description "..."] [--from-json spec.json]
$CLI forms add-items FORM_ID --from-json items.json [--at INDEX]
$CLI forms update-info FORM_ID [--title "..."] [--description "..."]
$CLI forms delete-item FORM_ID INDEX
```

## JSON spec format

`--from-json` accepts either an array of item specs, or an object `{title, description, items}`.

Supported item types — every type accepts an optional `description` and (for questions) `required`:

```jsonc
// Section divider (forces a new page in the responder UI)
{"type": "section", "title": "About you", "description": "Optional intro text"}

// Short text answer
{"type": "text", "title": "Your name", "required": true}

// Long text answer
{"type": "paragraph", "title": "Tell us more"}
// Equivalent: {"type": "text", "title": "...", "paragraph": true}

// 1–10 (or any range) linear scale
{"type": "linear_scale", "title": "Rate it",
 "low": 1, "high": 10, "low_label": "Bad", "high_label": "Great", "required": true}

// Single-choice radio
{"type": "radio", "title": "Your role?",
 "options": ["Engineer", "Scientist", "Founder", "Other"], "required": true}

// Multi-select
{"type": "checkbox", "title": "Which apply?", "options": ["A", "B", "C"]}

// Dropdown
{"type": "dropdown", "title": "Pick one", "options": ["A", "B", "C"]}

// Date
{"type": "date", "title": "Event date", "include_time": false, "include_year": true}
```

Example full spec for a feedback form:

```json
{
  "title": "Workshop Feedback",
  "description": "Two minutes — your answers shape the next workshop.",
  "items": [
    {"type": "linear_scale", "title": "Overall rating",
     "low": 1, "high": 10, "low_label": "Bad", "high_label": "Great", "required": true},
    {"type": "radio", "title": "Your role?",
     "options": ["Scientist", "Engineer", "Founder", "Other"], "required": true},
    {"type": "paragraph", "title": "What did you like most?", "required": true},
    {"type": "paragraph", "title": "What could be improved?"}
  ]
}
```

## Response Shapes

`get`: form metadata with item summaries.
```json
{
  "formId": "FORM_ID",
  "title": "Anonymous Reporting Form",
  "documentTitle": "Anonymous Reporting Form",
  "description": "...",
  "responderUri": "https://docs.google.com/forms/d/e/.../viewform",
  "revisionId": "00000004",
  "itemCount": 6,
  "settings": {"emailCollectionType": "DO_NOT_COLLECT"},
  "publishSettings": {"publishState": {"isPublished": true, "isAcceptingResponses": true}},
  "items": [
    {"itemId": "0de6f9e9", "title": "Details of Suspected Violation", "type": "section", "required": false},
    {"itemId": "214f88dd", "title": "Date of Report", "type": "dateQuestion", "required": true}
  ]
}
```

`responses`: list of responses with question titles mapped onto each answer.
```json
{
  "formId": "FORM_ID",
  "count": 1,
  "responses": [
    {
      "responseId": "ACYDBN...",
      "createTime": "2026-05-01T04:44:39.545Z",
      "lastSubmittedTime": "2026-05-01T04:44:39.545092Z",
      "respondentEmail": null,
      "answers": [
        {"questionId": "542c6873", "question": "Date of Report", "values": ["2026-04-04"]},
        {"questionId": "13d16c0b", "question": "Brief description of incident or concern", "values": ["test"]}
      ]
    }
  ]
}
```

`response`: same shape as a single entry in `responses[]`.

`create`: returns the form ID, edit URL, responder URL, and item count.
```json
{
  "formId": "FORM_ID",
  "title": "Workshop Feedback",
  "description": "...",
  "editUrl": "https://docs.google.com/forms/d/FORM_ID/edit",
  "responderUri": "https://docs.google.com/forms/d/e/.../viewform",
  "itemCount": 4
}
```

## Flags

- `--no-answers` (responses only): skips the form-metadata lookup and answer-title mapping. Useful for cheaply counting submissions or polling for new ones.
- `--page-size N` (responses only): caps the API page size. Default is the API default (~5000). Pagination is followed automatically — `count` reflects the full set.
- `--at INDEX` (add-items only): insert position. Default appends at the end.

## Examples

```bash
# Create a new form from a JSON spec
$CLI forms create --from-json /tmp/feedback-spec.json --compact

# Append two questions to an existing form
$CLI forms add-items FORM_ID \
  --from-json /tmp/extra-questions.json --compact

# Rewrite the title and description
$CLI forms update-info FORM_ID \
  --title "Workshop Feedback (Closed)" \
  --description "Form is closed — thanks for your input."

# Delete the question at index 0 (the first item)
$CLI forms delete-item FORM_ID 0

# Read responses (with question titles mapped)
$CLI forms responses FORM_ID --compact

# By editor URL
$CLI forms get "https://docs.google.com/forms/d/FORM_ID/edit"

# Trim the get output (--fields is a global flag, before the subcommand)
$CLI --fields formId,title,itemCount,responderUri forms get FORM_ID
```

## Anonymity Notes

If the form is configured with `emailCollectionType: DO_NOT_COLLECT`, `respondentEmail` is
always `null` — even when the respondent was signed in. Confirm `settings.emailCollectionType`
on `forms get` before treating responses as anonymous.

## UI Verification

After `create`, `add-items`, `update-info`, or `delete-item`, verify the form looks correct using Chrome DevTools MCP:

1. Open the responder URL to check the public view:
   ```
   mcp__chrome-devtools__navigate_page → result.responderUri
   ```
2. Take a screenshot and confirm: title, description, question order, question types, and required fields all match the spec.
3. For edits, also open the editor URL (`editUrl` or `https://docs.google.com/forms/d/{formId}/edit`) to verify item positions and settings.
4. If anything looks wrong, fix it with `forms update-info`, `forms add-items`, or `forms delete-item` before reporting done.

## Auth Scopes

This CLI uses the `https://www.googleapis.com/auth/forms.body` scope (already in
`lib/auth.py:SCOPES`). The same scope covers create, edit, and read of form metadata
and items. Reading responses works under `forms.responses.readonly` separately, but
`forms.body` is the practical scope for end-to-end authoring + reading.

---
name: gw-drive
description: >
  List, get, upload, download, export, organize, and share Google Drive files via CLI.
  TRIGGERS: "drive", "google drive", "upload file", "download file", "share file",
  "drive folder", "export doc as pdf", "find file in drive", "move file", "trash file"
---

# Drive

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

```bash
$CLI drive list [--query "RAW_Q"] [--name-contains S] [--folder ID_OR_URL] [--type folder|doc|sheet|slide|pdf|image|any] [--trashed] [--limit 20] --compact
$CLI drive get FILE_ID_OR_URL --compact
$CLI drive upload PATH [--folder ID_OR_URL] [--name N] [--mime M] [--convert] --compact
$CLI drive download FILE_ID_OR_URL --dest DIR_OR_PATH [--format pdf|docx|xlsx|pptx|csv|txt|md|html] [--force] --compact
$CLI drive export FILE_ID_OR_URL --format F [--dest DIR_OR_PATH] [--force] --compact
$CLI drive mkdir NAME [--parent ID_OR_URL] --compact
$CLI drive move FILE_ID --to FOLDER_ID_OR_URL --compact
$CLI drive rename FILE_ID --name NEW_NAME --compact
$CLI drive copy FILE_ID [--name N] [--folder ID_OR_URL] --compact
$CLI drive trash FILE_ID --compact
$CLI drive untrash FILE_ID --compact
$CLI drive share FILE_ID --with EMAIL --role reader|commenter|writer [--no-notify] [--message TEXT] --compact
$CLI drive shares FILE_ID --compact
$CLI drive unshare FILE_ID --permission-id ID --compact
```

## IDs and URLs

Every FILE/FOLDER argument accepts a bare ID or a full URL — Drive file links
(`/file/d/ID`), Docs/Sheets/Slides links (`/document/d/ID` etc.), folder links
(`/folders/ID`), and `open?id=ID` links.

## Safety stances (deliberate)

- **No permanent delete.** `trash` only marks files trashed (recoverable);
  emptying the trash must be done by a human in the Drive UI.
- **No anyone-with-link creation.** `share` grants access to specific email
  addresses only. Public links must be created by a human in the Drive UI.
  `shares` still *lists* existing anyone-with-link permissions so you can audit them.

## Downloads and exports

- Binary files (pdf, images, zip, ...) download as-is; `--format` is rejected.
- Google-native files (Docs/Sheets/Slides) cannot be downloaded raw — pass
  `--format`, validated per source type:
  - Doc: `pdf docx txt md html`
  - Sheet: `pdf xlsx csv`
  - Slides: `pdf pptx txt`
- Existing local files are never overwritten unless `--force` is passed.
- `--dest` may be a directory (keeps the Drive name) or an exact file path.

## Uploads

- `--mime` defaults to a guess from the filename; `--convert` imports
  markdown/csv/Office files (doc/docx/xls/xlsx/ppt/pptx) as native
  Docs/Sheets/Slides.

## Scopes

Full Drive access requires the `drive` scope. If a command fails with
`INSUFFICIENT_SCOPE`, re-authenticate:
`$CLI auth login --scopes drive` (include the other services you use, e.g.
`--scopes gmail,calendar,sheets,docs,tasks,forms,drive`). Tokens holding only
`drive.file` see just the files this app created.

## Response Shapes

`list`: `{"files":[{"id","name","mimeType","modifiedTime","size","webViewLink","parents"}],"count":2,"query":"..."}`

`get`: full metadata `{"id","name","mimeType","size","createdTime","modifiedTime","webViewLink","parents","owners",...}`

`upload`: `{"uploaded":true,"id","name","mimeType","size","webViewLink","parents"}`

`download`: `{"downloaded":true,"fileId","name","path","bytes"}`

`export`: `{"exported":true,"fileId","name","format","path","bytes"}`

`mkdir`: `{"created":true,"id","name","webViewLink"}`

`move`: `{"moved":true,"id","name","parents"}` · `rename`: `{"renamed":true,"id","name","webViewLink"}` · `copy`: `{"copied":true,"id","name","webViewLink","parents"}`

`trash`/`untrash`: `{"id","name","trashed":true|false}`

`share`: `{"shared":true,"fileId","permission":{"id","type","role","emailAddress"}}`

`shares`: `{"fileId","permissions":[{"id","type","role","emailAddress",...}],"count":1}`

`unshare`: `{"unshared":true,"fileId","permissionId"}`

## Recipes

```bash
# Find a file by name and get its link
$CLI drive list --name-contains "quarterly report" --compact
# → read webViewLink from the matching entry

# Upload a file and share it with someone
$CLI drive upload ./report.pdf --folder "https://drive.google.com/drive/folders/FOLDER_ID" --compact
$CLI drive share NEW_FILE_ID --with person@example.com --role reader --message "Here is the report."

# Download a Google Doc as PDF
$CLI drive download "https://docs.google.com/document/d/DOC_ID/edit" --dest ./downloads --format pdf

# Import a markdown file as a native Google Doc
$CLI drive upload ./notes.md --convert --name "Meeting Notes"

# Everything in a folder, folders only
$CLI drive list --folder FOLDER_ID --type folder --compact

# Raw Drive query passthrough (full q syntax)
$CLI drive list --query "name contains 'invoice' and modifiedTime > '2026-01-01T00:00:00'" --limit 50
```

## Notes

- All calls are shared-drive aware (`supportsAllDrives=true`).
- `list` excludes trashed files by default; `--trashed` lists the trash instead.
- `--query` is a raw passthrough and cannot be combined with the ergonomic filters.
- Default account is the configured default (see `$CLI auth list`).

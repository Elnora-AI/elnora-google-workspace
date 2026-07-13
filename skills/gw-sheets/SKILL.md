---
name: gw-sheets
description: >
  Read, write, append, and list Google Sheets via CLI.
  TRIGGERS: "sheets", "spreadsheet", "google sheets", "read sheet", "write sheet",
  "append row", "update cell", "list spreadsheets"
---

# Sheets

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

```bash
$CLI sheets read SPREADSHEET_ID [--range "Sheet1!A1:F100"] --compact
$CLI sheets write SPREADSHEET_ID --range "Sheet1!E2" --value "bounced" --compact
$CLI sheets append SPREADSHEET_ID --values '["name","email","status"]' [--range "Sheet1"] --compact
$CLI sheets list --compact
```

## Response Shapes (validated)

`list`: `{"spreadsheets":[{"id","name","modified"}],"count":5}`

`read`: `{"spreadsheetId","range","rows":[["A1","B1"],["A2","B2"]],"count":2}`

`write`: `{"updated":true,"spreadsheetId","range","updatedCells":1}`

`append`: `{"appended":true,"spreadsheetId","range"}`

## Examples

```bash
# Read first 10 rows
$CLI sheets read "1BxiMVs..." --range "Sheet1!A1:F10" --compact

# Update a single cell
$CLI sheets write "1BxiMVs..." --range "Sheet1!E2" --value "sent"

# Append a new row (must be JSON array)
$CLI sheets append "1BxiMVs..." --values '["John","john@co.com","pending"]'
```

## UI Verification

After `write` or `append`, verify the spreadsheet looks correct using Chrome DevTools MCP:

1. Open the sheet in the browser:
   ```
   mcp__chrome-devtools__navigate_page → https://docs.google.com/spreadsheets/d/{spreadsheetId}/edit
   ```
2. Take a screenshot and confirm: the written/appended cells contain the expected values in the correct positions.
3. If data is wrong, fix it with `sheets write` before reporting done.

## Notes

- `--values` must be a JSON array string.
- Range defaults to `"Sheet1"` if omitted on read/append.
- Default account is `main`.

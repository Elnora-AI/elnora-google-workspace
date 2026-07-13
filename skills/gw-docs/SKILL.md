---
name: gw-docs
description: >
  Get, create, import, append, and replace text in Google Docs via CLI. Accepts doc IDs or full URLs.
  Import renders a Markdown file as a native Doc (headings, tables, lists, links).
  TRIGGERS: "google docs", "google doc", "create doc", "read doc", "append to doc",
  "replace in doc", "document", "gdoc", "write to doc", "paste into doc",
  "import markdown to doc", "md to google doc", "render markdown as doc", "open md as google doc"
---

# Docs

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Commands

All commands accept a document ID or full Google Docs URL.

```bash
$CLI docs get DOC_ID --compact
$CLI docs create --title "Title" [--body "text"] [--body-file path] [--folder FOLDER] --compact
$CLI docs import PATH.md [--title "Title"] [--doc-id DOC_ID] [--folder FOLDER] [--keep-frontmatter] --compact
$CLI docs append DOC_ID --body "text" --compact
$CLI docs append DOC_ID --body-file - <<'BODY'
Multi-line content here.
BODY
$CLI docs replace DOC_ID --find "old" --replace-with "new" --compact
```

## Response Shapes (validated)

`get`: `{"documentId":"1abc...","title":"My Doc","text":"full plain text content","revisionId":"AMHa..."}`

`create`: `{"documentId":"1FYi...","title":"Title","url":"https://docs.google.com/document/d/1FYi.../edit"}`

`import`: `{"documentId":"1gB2...","title":"Title","url":"https://docs.google.com/document/d/1gB2.../edit?usp=drivesdk"}`

`append`: `{"documentId":"1abc...","appended":true,"charactersInserted":22}`

`replace`: `{"documentId":"1abc...","replaced":true,"occurrencesChanged":1}`

## Examples

```bash
# Read a doc (by URL)
$CLI docs get "https://docs.google.com/document/d/1bhGOtwf.../edit" --compact

# Create a doc with content from stdin
$CLI docs create --title "Meeting Notes" --body-file - <<'BODY'
Notes from today's meeting.
Action items:
- Follow up with Triin
- Send proposal
BODY

# Import a Markdown file as a native Google Doc (renders headings, bold, lists, tables, links)
$CLI docs import /path/to/notes.md --title "Notes" --compact

# Import directly into a Drive folder (accepts a folder ID or full folder URL)
$CLI docs import /path/to/notes.md --title "Notes" --folder "https://drive.google.com/drive/folders/1rUv-..." --compact

# Re-import into the SAME doc (keeps the ID and shareable link; replaces content)
$CLI docs import /path/to/notes.md --doc-id "1bhGOtwf..." --compact

# Append to an existing doc
$CLI docs append "1bhGOtwf..." --body "New section added."

# Find and replace (case-sensitive)
$CLI docs replace "1bhGOtwf..." --find "DRAFT" --replace-with "FINAL"
```

## UI Verification

After `create`, `append`, or `replace`, verify the document looks correct in Google Docs using Chrome DevTools MCP:

1. Open the doc URL returned in the response:
   ```
   mcp__chrome-devtools__navigate_page → result.url (create) or https://docs.google.com/document/d/{documentId}/edit
   ```
2. Take a screenshot and confirm: title, content, and formatting are as expected.
3. If content is wrong, fix it with `docs replace` or `docs append` before reporting done.

## Notes

- `get` returns plain text only. Formatting, images, and tables are not preserved.
- `create --body` inserts **plain text** — Markdown syntax shows as raw characters. To render Markdown (headings, bold, bullet/numbered lists, pipe tables, links) into native Doc formatting, use `import` instead.
- `import` converts a Markdown file on upload via Drive. It strips YAML frontmatter by default (`--keep-frontmatter` to retain). Without `--doc-id` it creates a new doc; with `--doc-id` it replaces that doc's content while preserving the ID and shareable link.
- `import` needs a file path (not inline text); write content to a `.md` file first if needed.
- `--folder` (on `create` and `import`) puts the doc straight into a Drive folder — pass a folder ID or full folder URL. Without it, docs land in My Drive root. Ignored when `--doc-id` is set (a replace keeps the existing location). No separate "move" step needed.
- `append` auto-prepends a newline when the doc is non-empty.
- `replace` is case-sensitive and replaces all occurrences.
- Use `--body-file -` with `<<'BODY'` heredoc for content with special characters.

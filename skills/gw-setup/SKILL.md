---
name: gw-setup
description: >
  First-run setup for the Google Workspace (gw) plugin. Creates the plugin venv,
  installs deps, walks the user through a Google Cloud project + Desktop OAuth
  client (driving a browser when possible), authenticates, and verifies. Use for
  "set up gw", "gw setup", "authenticate google workspace", "gw first run",
  "connect my google account", "install gw".
  TRIGGERS: "gw setup", "set up google workspace", "gw auth setup", "gw first run",
  "connect google account", "google workspace login", "oauth setup"
---

# gw-setup — get from install to first result

Do everything you safely can **for** the user. The only unavoidable human actions
are clicking buttons inside the Google Cloud Console (Enable, tick the consent
box, download the client JSON) and approving the OAuth consent screen. Navigate to
the exact page, tell the user precisely what to click, wait, then verify by reading
the page before moving on. Report progress as you go.

Throughout, run the CLI via the launcher `"$PLUGIN_ROOT/bin/gw"` once it exists, or
`python3 "$PLUGIN_ROOT/cli/gw.py"` (fall back to `python`) before that. Resolve
`PLUGIN_ROOT` to this plugin's root (`$CLAUDE_PLUGIN_ROOT` if set, else the repo
root that contains `cli/gw.py`).

## Step 1 — Environment

1. Detect Python: prefer `uv` if present (`uv --version`), else `python3 --version`
   (fall back to `python`). Require Python 3.10+.
2. Create the plugin-local venv and install deps from `requirements.txt`
   (idempotent — the single source of truth for the dependency set):
   - With uv: `uv venv "$PLUGIN_ROOT/.venv"` then
     `uv pip install --python "$PLUGIN_ROOT/.venv" -r "$PLUGIN_ROOT/requirements.txt"`
   - Without uv: `python3 -m venv "$PLUGIN_ROOT/.venv"` then
     `"$PLUGIN_ROOT/.venv/bin/pip" install -r "$PLUGIN_ROOT/requirements.txt"`
     (Windows: `"$PLUGIN_ROOT\.venv\Scripts\pip.exe"`).
   `keyring` is listed in `requirements.txt` but optional at runtime (OS-keychain
   token storage); if it fails to build, install the rest and continue — tokens
   fall back to a 0600 file.
3. Ensure the launcher is executable: `chmod +x "$PLUGIN_ROOT/bin/gw"` (POSIX).
   On Windows use `bin/gw.cmd` or `bin/gw.ps1`.
4. Verify: `gw --version` prints `gw, version 1.0.0`. If not, fix before continuing.

## Step 2 — Google Cloud project + Desktop OAuth client

This plugin ships **no** OAuth client — each user brings their own. This is the
part that needs a browser.

1. **Get the machine-readable checklist.** Run `gw auth setup` (or
   `gw auth setup --compact`). It returns JSON:
   `{ "config_dir": "...", "steps": [ {step,title,url,instructions,verify}, ... ] }`.
   Drive the rest of this step from that output — do not hardcode URLs.

2. **Offer browser automation.** If a browser-automation MCP is available
   (e.g. Chrome DevTools MCP tools `mcp__chrome-devtools__*`), use it. If none is
   available, offer to add one:
   `claude mcp add chrome-devtools -- npx chrome-devtools-mcp@latest`
   (claude-in-chrome is an alternative). If the user declines automation, **print
   each step's `url` + `instructions` + `verify`** and let them do it manually,
   then resume at Step 3.

3. **Walk the steps** (drive the browser or guide the user), verifying each via the
   step's `verify` string before continuing:
   - Create a Google Cloud project (`projectcreate`). No billing needed.
   - Enable the 7 APIs: Gmail, Calendar, Drive, Docs, Sheets, Tasks, Forms. For
     each, open the library `url` and click **Enable**; verify it shows "API Enabled".
   - Configure the **OAuth consent screen**: user type **External**; add the user's
     own Google account as a **Test user** (Gmail scopes are sensitive — consent is
     blocked for unlisted users). Explain the unverified-app ~25-scope cap; the gw
     default set is well under it. Verify status is "Testing" and the email is listed.
   - Create a **Desktop app** OAuth client (Credentials → OAuth client ID →
     Application type **Desktop app**). Click Create, then **Download** the client
     JSON. Some pages require you to reopen the client to download — do so and verify
     a Desktop client is listed.
   - **Save the client JSON** to `<config_dir>/client_secret.json` (the `config_dir`
     from the checklist, default `~/.config/gw/`). If the browser downloaded it to
     the Downloads folder, move it there for the user and confirm the file exists.

   Where Google requires a relaunch/reopen (e.g. after first enabling APIs, or to
   reveal the download), navigate back to the exact page and re-verify rather than
   assuming success.

## Step 3 — Authenticate

Run `gw auth login`. It starts a localhost callback and opens the consent URL; you
may open that URL in the browser too. On the consent screen the user picks their
Google account and approves. Use `--no-browser` to print the URL instead of opening
one (headless/SSH), and `--account <name>` to add more accounts later.

If login reports **no refresh token**, have the user revoke prior access at
`https://myaccount.google.com/permissions` and log in again.

## Step 4 — Verify it works

1. `gw auth status` → expect `token_present: true` and `valid: true`.
2. A real read, e.g. `gw calendar calendars` or `gw gmail labels`. A successful JSON
   response means setup is complete.

## Step 5 — Optional: knowledge-base connector

The CRM sync, meeting-prep (`/prep-meeting`), and outreach features are optional and
need a knowledge base. Suggest (do not force):
`/plugin marketplace add Elnora-AI/knowledge-vault` then
`/plugin install knowledge-base@knowledge-vault`, and run its setup. Once
`.claude/knowledge-base.local.md` exists, the connector auto-enables. Without it,
those commands no-op with a message and every core Google command still works.

## Notes / troubleshooting

- **Config lives outside the repo**: tokens, `accounts.json`, and
  `client_secret.json` live under `$GW_CONFIG_DIR` (default `~/.config/gw`), never in
  the repo. Nothing secret is ever written into the plugin directory.
- **Keyring vs file**: with the `keyring` package and an OS backend, tokens go to the
  OS keychain; otherwise a 0600 JSON file. `GW_KEYRING=off` forces file storage.
- **Alternate OAuth client sources**: instead of `client_secret.json` you can set
  `GW_CLIENT_ID`/`GW_CLIENT_SECRET`, or pass `gw auth login --client-secret-file PATH`.
- **Narrow scopes**: `gw auth login --scopes gmail,calendar` (and `--readonly`) if the
  user wants a smaller grant than the default all-services set.

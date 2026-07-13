# INSTALL_FOR_AGENTS.md — `gw` Google Workspace setup

> **Step 0 — Identity gate.** If you are NOT an autonomous coding agent (Claude
> Code, Codex CLI, Cursor, Aider, Continue, Amp, Jules, Roo, Windsurf, etc.),
> stop reading this file and open `README.md` instead. This file walks an agent
> through a multi-step setup that includes driving the user through a Google
> Cloud OAuth client, writing config to `~/.config/gw/`, running an OAuth
> browser flow, and making live calls against Google Workspace APIs. Humans
> should follow the README's Install section and `/gw-setup`.

You are the agent handing off after the user installed the `gw` CLI (as a Claude
Code plugin via `/plugin install google-workspace@elnora-google-workspace`, or as
a standalone clone of this repo). Your job is to verify the install, walk the user
through creating their OWN Google Cloud OAuth client, authenticate, run a
read-only smoke test, and hand them a working environment. The optional CRM
connector is the last step.

Be transparent: announce each step before you run it, show the output, and
explain what you found. The user may not have a Google Cloud project yet and may
not know what an "OAuth client" is — keep your language plain, navigate them to
the exact console page, and ask one question at a time.

**Universal:** every step here uses the `gw` CLI, which works identically under
any agent harness. The only Claude-Code-specific note is the optional plugin
check in Step 1. Portable driving rules (invocation, JSON output, safety) live in
[`AGENTS.md`](AGENTS.md) — read it once before you start; do not duplicate it here.

**Never embed an OAuth client.** This plugin ships none by design. The user
supplies their own `client_secret.json`. Do not paste a client ID/secret from
anywhere else, and never print token material or credential file contents back to
the user.

## Step 1 — Verify the install

The CLI is invoked through the launcher `bin/gw` (POSIX) or `bin/gw.cmd` /
`bin/gw.ps1` (Windows), which finds the plugin's `.venv`, else `python3` /
`python` on `PATH`. Direct fallback: `python3 cli/gw.py …`. Run, in this order:

```sh
gw --version
gw --help
```

Gates:
- `--version` exits 0 and prints `gw, version <x.y.z>` (e.g. `gw, version 1.0.0`).
- `--help` lists the command groups (`auth`, `gmail`, `calendar`, `drive`,
  `docs`, `sheets`, `forms`, `tasks`, `api`, `schema`, `crm`). If the binary runs
  but the group list is empty or truncated, the build is broken — surface it.

**If `gw --version` fails with `ModuleNotFoundError`** (a standalone clone with no
`.venv` and deps not on `PATH`), build the plugin-local venv before continuing —
this is what `/gw-setup` does under Claude Code. Prefer `uv` if present:

```sh
# with uv:
uv venv .venv
uv pip install --python .venv google-api-python-client google-auth-oauthlib click keyring
# without uv:
python3 -m venv .venv
.venv/bin/pip install google-api-python-client google-auth-oauthlib click keyring
```

`keyring` is optional (OS-keychain token storage); if it fails to build, install
the rest and continue — tokens fall back to a `0600` file. On POSIX, ensure the
launcher is executable: `chmod +x bin/gw`. Re-run `gw --version` to confirm.

**Claude Code only — optional.** If the user installed the plugin (not a
standalone clone), confirm it loaded:

```sh
ls .claude/plugins 2>/dev/null || ls ~/.claude/plugins 2>/dev/null
```

You should see `google-workspace` somewhere. If not, the `/plugin install` didn't
complete — ask the user to rerun it. Skip this check entirely under Codex /
Cursor / Aider / Continue / Amp / Jules / Roo — those harnesses call the launcher
directly via [`AGENTS.md`](AGENTS.md), no plugin install required.

## Step 2 — Google Cloud OAuth client

`gw` talks to Google on the user's OWN OAuth client — the plugin ships none.
Get the machine-readable checklist and walk it with the user:

```sh
gw auth setup
```

This prints JSON: a `config_dir` (default `~/.config/gw`, override with
`$GW_CONFIG_DIR`) and an ordered `steps` array. Each step has a `title`, a console
`url`, `instructions`, and a `verify` string. Walk them in order — most are
one-click console actions only a human can do. Navigate the user to each `url`,
tell them precisely what to click, wait, then confirm the step's `verify`
condition before moving on. The steps are:

1. Create a Google Cloud project (no billing account required).
2–8. Enable the APIs the CLI uses: Gmail, Calendar, Drive, Docs, Sheets, Tasks,
   Forms. Each is an **Enable** click on that API's library page.
9. Configure the OAuth consent screen — user type **External**, app name +
   support email, and **add the user's own Google account as a test user**.
   Gmail scopes are sensitive; consent is blocked for unlisted users.
10. Create a **Desktop** OAuth client and download the client JSON.
11. Save that JSON as `~/.config/gw/client_secret.json` (or set
    `GW_CLIENT_ID` / `GW_CLIENT_SECRET`, or pass `--client-secret-file PATH` to
    `gw auth login` in Step 3).

Gates:
- After step 11, the client file exists. Verify and lock its mode — it is a
  credential:

  ```sh
  mkdir -p ~/.config/gw
  chmod 600 ~/.config/gw/client_secret.json
  test -f ~/.config/gw/client_secret.json && echo "client_secret.json present"
  ```

- If the user prefers env vars over the file, that's fine — record that they'll
  pass `GW_CLIENT_ID` / `GW_CLIENT_SECRET` (or `--client-secret-file`) in Step 3,
  and skip the file check.
- Do NOT invent or reuse a client ID/secret. If the user pastes one that isn't a
  **Desktop** client, the localhost callback in Step 3 will fail — flag it now.

**Automation shortcut (Claude Code with the Chrome DevTools MCP).** If you can
drive a browser, you may navigate the console pages and click through the enable/
consent/create steps yourself, stopping only where Google requires a human
(approving consent, downloading the JSON). Everything else is the same — the
checklist from `gw auth setup` is the source of truth for URLs and click targets.

## Step 3 — Authenticate

Run the OAuth flow. This opens a browser to Google's consent screen and captures
the token on a localhost callback:

```sh
gw auth login
```

Useful flags: `--account <name>` names an account (for multiple mailboxes — repeat
login per account); `--email <addr>` pre-selects a Google account; `--no-browser`
prints the consent URL instead of opening one (use this on a headless machine and
hand the user the URL); `--client-secret-file PATH` if the client JSON isn't at
the default location; `--readonly` for read-only scope variants. On success the
token is stored in the OS keyring when available, else a `0600` file under the
config dir. Then verify:

```sh
gw auth status
```

Gates:
- `auth status` exits 0 and reports `token_present: true`. Ideally `valid: true`;
  but `valid: false` with `refreshable: true` is also fine — the token expired and
  the next API call refreshes it automatically. `token_present: false` means login
  didn't land — re-run `gw auth login` and watch for a consent error.
- `403 access_denied` on the consent screen means the user's Google account isn't
  listed as a **test user** on the consent screen (Step 2, item 9). Send them back
  to add it, then retry.
- If the token is stored as a file (not keyring), it is written `0600` by the CLI;
  you don't need to chmod it. `gw auth list` shows every configured account and
  where its token lives.

## Step 4 — Smoke test

Prove auth end-to-end with the cheapest read-only call:

```sh
gw gmail list --limit 1
```

Gates:
- Exit 0 and JSON on stdout with a `messages` array. One message, or an empty
  array for an empty/filtered mailbox, both count as success — distinguish an
  empty result from a failed call (a failure is JSON on stderr with a non-zero
  exit).
- If it returns `403` / `PERMISSION_DENIED` naming the Gmail API, that API wasn't
  enabled in Step 2 — send the user back to enable it, then retry.
- For a multi-account setup, add `--account <name>` and confirm each account
  independently.

## Step 5 — Optional CRM connector

Mention this only if the user wants a self-updating CRM; it needs the companion
[`Elnora-AI/knowledge-vault`](https://github.com/Elnora-AI/knowledge-vault)
knowledge base. Without it, every CRM command cleanly no-ops and the core Google
commands are unaffected — so there's nothing to configure and you can skip this
step entirely.

If the user has a knowledge base configured (`vault_path` in
`.claude/knowledge-base.local.md`):

```sh
gw crm init                   # scaffold contacts.csv + companies.csv in the vault (idempotent)
gw gmail sync-crm-install     # schedule email → CRM on the OS scheduler
gw calendar sync-crm-install  # schedule calendar → CRM
```

`sync-crm-install` registers the job on the native scheduler (launchd / Task
Scheduler / crontab) and prints the exact command if it can't drive the scheduler
itself — it never takes elevated permissions on the user's behalf. `--interval-hours N`
sets the cadence (default 2); remove either job with the matching
`sync-crm-uninstall`. Confirm with `gw crm status` and `gw gmail sync-crm-status`.

## Step 6 — Handoff summary

Tell the user, in this order:

1. **What's installed and where config lives** — the `gw` launcher (plugin or
   clone), `~/.config/gw/client_secret.json` (their OAuth client), and the token
   in the OS keyring or a `0600` file under `~/.config/gw/`. Nothing is written
   into the repo.
2. **What works now** — read straight from the final `gw auth status` and the
   Step 4 smoke test; don't paraphrase. Name the account(s) authenticated.
3. **How to use it** — two entry points (use the form matching the user's harness):
   - **Under Claude Code with the plugin:** `/gw-inbox`, `/draft-email …`, and the
     per-service skills (`gw-gmail`, `gw-calendar`, `gw-drive`, …).
   - **Under any agent or standalone:** `gw gmail list -q "is:unread"`,
     `gw calendar list`, `gw drive list`, `gw docs create …`, and `gw api` for any
     uncovered Google API. `gw schema <command>` shows parameters and scopes; full
     dispatch rules in [`AGENTS.md`](AGENTS.md).
4. **Safety posture** — read-only by default where it matters, destructive ops
   need explicit confirmation, Drive trashes (never hard-deletes), outreach drafts
   (never auto-sends). Point them at [SAFETY.md](SAFETY.md).
5. **If they enabled the CRM (Step 5)** — note the sync is scheduled and mention
   `sync-crm-status` / `sync-crm-uninstall`.

## Completion checklist

Before declaring the setup complete, verify ALL of these. If any item fails,
finish it before reporting done.

1. `gw --version` exits 0 and prints a `gw, version <x.y.z>` string.
2. `gw --help` lists the command groups (not empty/truncated).
3. `~/.config/gw/client_secret.json` exists at mode `600` (OR the user opted for
   `GW_CLIENT_ID` / `GW_CLIENT_SECRET` / `--client-secret-file` instead).
4. `gw auth status` exits 0 with `token_present: true` (and `valid: true`, or
   `valid: false` with `refreshable: true`).
5. `gw gmail list --limit 1` exits 0 (a message OR an empty array is OK; a
   non-zero exit is not).
6. If the user set up multiple accounts: `gw auth list` shows each, and
   `gw auth status --account <name>` passes for each.
7. If the user enabled the CRM in Step 5: `gw crm status` resolves a vault path,
   and `gw gmail sync-crm-status` / `gw calendar sync-crm-status` report the
   scheduled job.
8. You have NOT printed token material, `client_secret.json` contents, or any
   credential back to the user, and NOT embedded an OAuth client anywhere.

When all applicable items pass, print `GW_WORKSPACE_READY` on its own line so the
user (and any wrapping harness) can grep for it.

# INSTALL_FOR_AGENTS.md — `elnora-google-workspace` setup

> **Step 0 — Identity gate.** If you are NOT an autonomous coding agent (Claude
> Code, Codex CLI, Cursor, Aider, Continue, Amp, Jules, Roo, Windsurf, etc.),
> stop reading this file and open [`README.md`](README.md) instead. This file
> walks an agent through a multi-step setup that creates a Python venv, drives
> the Google Cloud console to mint an OAuth client, and runs a live OAuth
> sign-in on the user's behalf. Humans should follow the README's Install
> section and run `/gw-setup`.

You are the agent handing off after the user installed the `google-workspace`
plugin (`/plugin marketplace add Elnora-AI/elnora-google-workspace` then
`/plugin install google-workspace@elnora-google-workspace`). Your job is to
build the venv, help the user create their own Google Cloud OAuth client,
authenticate one or more Google accounts, and confirm a real read works.

Be transparent: announce each step before you run it, show the output, and do
the browser work **for** the user wherever a browser-automation MCP is
available. Do the steps in order. Do not skip verification. If a step fails,
stop and report — do not proceed.

**Universal:** every step here uses the `gw` CLI, which works identically under
any agent harness. The only Claude-Code-specific note is the optional plugin
check in Step 1 and the `/gw-setup` alternative — under Codex / Cursor / Aider /
Continue / Amp / Jules / Roo, drive the same CLI directly per
[`AGENTS.md`](AGENTS.md).

## How to run the CLI

Resolve `PLUGIN_ROOT` to this plugin's root — `$CLAUDE_PLUGIN_ROOT` if set, else
the repo directory that contains `cli/gw.py`. Invoke the CLI via the launcher
once the venv exists, otherwise fall through to the interpreter:

```sh
"$PLUGIN_ROOT/bin/gw" <args>              # POSIX, once Step 2 built the venv
python3 "$PLUGIN_ROOT/cli/gw.py" <args>   # fallback (try python if python3 is absent)
```

On Windows use `"$PLUGIN_ROOT\bin\gw.cmd"` (or `bin\gw.ps1`). The examples below
write `gw` for brevity — substitute whichever form resolves on this machine.
Output is **JSON on stdout, exit 0 on success**; errors are JSON on stderr with
a non-zero exit. Add `--compact` to any command to save tokens.

## Step 1 — Verify the plugin loaded

**Claude Code only — optional.** If the user installed the plugin, confirm it's
present:

```sh
ls .claude/plugins 2>/dev/null || ls ~/.claude/plugins 2>/dev/null
```

You should see `google-workspace` (or `elnora-google-workspace`) somewhere. If
not, the `/plugin install` didn't complete — ask the user to rerun it. Skip
this check entirely under Codex / Cursor / Aider / Continue / Amp / Jules / Roo —
those harnesses call the CLI directly via [`AGENTS.md`](AGENTS.md), no plugin
install required.

> **Shortcut for Claude Code users.** Everything below is exactly what the
> `/gw-setup` slash command automates. If the user is in Claude Code and would
> rather run that, point them at it and stop here. This file exists so the same
> flow works under any harness (and so you can drive it step by step).

## Step 2 — Build the venv and install dependencies

Create a plugin-local virtual environment and install the runtime deps from
`requirements.txt` (the single source of truth for the dependency set — never
hardcode a package list that can drift from it). This is idempotent; re-running
is safe.

Detect the toolchain first: prefer `uv` if present (`uv --version`), else
`python3 --version` (fall back to `python`). Require Python 3.10+.

```sh
# With uv (preferred):
uv venv "$PLUGIN_ROOT/.venv"
uv pip install --python "$PLUGIN_ROOT/.venv" -r "$PLUGIN_ROOT/requirements.txt"

# Without uv:
python3 -m venv "$PLUGIN_ROOT/.venv"
"$PLUGIN_ROOT/.venv/bin/pip" install -r "$PLUGIN_ROOT/requirements.txt"
# Windows: "$PLUGIN_ROOT\.venv\Scripts\pip.exe" install -r "$PLUGIN_ROOT\requirements.txt"
```

`keyring` is listed in `requirements.txt` but optional at runtime (it enables
OS-keychain token storage). If it fails to build, install the rest and continue
— tokens fall back to a 0600 file. On POSIX, make the launcher executable:
`chmod +x "$PLUGIN_ROOT/bin/gw"`.

Gate:

```sh
gw --version
```

Must exit 0 and print `gw, version 1.0.0`. Anything else means the venv or
launcher didn't land — surface the actual error and fix it before continuing.

## Step 3 — Google Cloud project + Desktop OAuth client

This plugin ships **no** OAuth client — each user brings their own. This is the
one part that needs a browser. **Offer to drive it for the user.**

**First, offer browser automation.** If a browser-automation MCP is available
(e.g. Chrome DevTools MCP — the `mcp__chrome-devtools__*` tools), tell the user
you can complete the console clicks for them and use it. If none is available,
offer to add one:

```sh
claude mcp add chrome-devtools -- npx chrome-devtools-mcp@latest
```

(`claude-in-chrome` is an alternative.) If the user declines automation, print
each step's `url` + `instructions` + `verify` from the checklist below and let
them click through manually, then resume at Step 4. Either way, after every
browser action, **verify by reading the page** before moving on — Google often
requires a reopen/relaunch before a control appears.

**Get the machine-readable checklist** — do not hardcode console URLs, they
change:

```sh
gw auth setup
```

This prints JSON `{ "config_dir": "...", "steps": [ {step,title,url,instructions,verify}, ... ] }`.
Note that `gw auth setup` only **emits this checklist** — it does not sign you in
(that's Step 4, `gw auth login`). Drive the rest of this step from its output:

1. **Create a Google Cloud project** (`projectcreate`). No billing needed.
2. **Enable the 7 APIs** — Gmail, Calendar, Drive, Docs, Sheets, Tasks, Forms.
   For each, open the library `url` and click **Enable**; verify it shows "API
   Enabled".
3. **Configure the OAuth consent screen** — user type **External**; add the
   user's own Google account as a **Test user** (Gmail scopes are sensitive, so
   consent is blocked for unlisted users). Verify status is "Testing" and the
   email is listed.
4. **Create a Desktop OAuth client** — Credentials → OAuth client ID →
   Application type **Desktop app** → Create, then **Download** the client JSON.
   Some pages require reopening the client to reveal the download.
5. **Save the client JSON** to `<config_dir>/client_secret.json` (the
   `config_dir` from the checklist, default `~/.config/gw/`). If the browser put
   it in Downloads, move it there for the user.

Gate: `<config_dir>/client_secret.json` exists. (Alternatives to a client-secret
file: set `GW_CLIENT_ID`/`GW_CLIENT_SECRET`, or pass
`gw auth login --client-secret-file PATH` in Step 4.)

## Step 4 — Authenticate

```sh
gw auth login
```

This starts a localhost callback and opens the Google consent screen; the user
picks their account and approves. Flags:

- `--no-browser` prints the URL instead of opening one (headless / SSH).
- `--account <name>` names this account (see Step 5); omit it for the default.
- `--scopes gmail,calendar` (and `--readonly`) narrows the grant if the user
  wants less than the default all-services set.

If login reports **no refresh token**, have the user revoke prior access at
`https://myaccount.google.com/permissions` and log in again.

Gate:

```sh
gw auth status
```

Must exit 0 with `token_present: true` and `valid: true`. If `valid` is false or
the token is absent, re-run `gw auth login` — do not proceed to the smoke test.

## Step 5 — Multi-account (optional)

Skip if the user only needs one account. Otherwise add each additional account
under its own name — the CLI stores them side by side in
`$GW_CONFIG_DIR/accounts.json`:

```sh
gw auth login --account work
gw auth login --account personal
gw auth list
```

Gate: `gw auth list` shows every account you added. Commands select an account
with `--account <name>`; when the flag is omitted the CLI resolves the config's
`default`, falling back to `main` — so do NOT hardcode `--account main` anywhere.
To make a non-`main` account the default, set `"default": "<name>"` in
`$GW_CONFIG_DIR/accounts.json` (see [`accounts.example.json`](accounts.example.json)).

## Step 6 — Smoke test

Confirm a real read works end to end:

```sh
gw calendar calendars --compact
# or: gw gmail labels --compact
```

Gate: exit 0 and a JSON payload (a list of the user's calendars or Gmail
labels). An empty-but-valid list is fine; a non-zero exit or an auth error is
not — go back to Step 4. If the user set up multiple accounts, run the read once
per account with `--account <name>` to confirm each token is live.

## Step 7 — Optional: knowledge-base connector

The CRM sync, meeting-prep (`/prep-meeting`), and outreach features are optional
and need a knowledge base. Suggest, do not force:

```
/plugin marketplace add Elnora-AI/knowledge-vault
/plugin install knowledge-base@knowledge-vault
```

then run its setup. Once `.claude/knowledge-base.local.md` exists, the connector
auto-enables. Without it, those commands no-op with a message and every core
Google command still works.

## Completion checklist

Before declaring setup complete, verify ALL of these. If any item fails, finish
it before reporting done.

1. `gw --version` exits 0 and prints `gw, version 1.0.0`.
2. `<config_dir>/client_secret.json` exists (or `GW_CLIENT_ID`/`GW_CLIENT_SECRET`
   are set).
3. `gw auth status` exits 0 with `token_present: true` and `valid: true`.
4. If the user added extra accounts: `gw auth list` shows each one.
5. `gw calendar calendars` (or `gw gmail labels`) exits 0 and returns JSON.
6. You have NOT printed, logged, or committed any token, `client_secret.json`,
   or `accounts.json` — those live under `$GW_CONFIG_DIR` (default
   `~/.config/gw`), never in the repo. Keep it that way.

When all applicable items pass, print `GW_WORKSPACE_READY` on its own line so the
user (and any wrapping harness) can grep for it.

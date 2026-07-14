# elnora-google-workspace

**Google Workspace for Claude Code — drive Gmail, Calendar, Drive, Docs, Sheets, Forms, and Tasks from one agent-friendly CLI, with a `gw api` escape hatch to any Google API. Multi-account OAuth, keyring-backed tokens, config-driven and universal.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## Install

Two slash commands, run them **one at a time** (paste the first, hit enter, wait, then the second):

```
/plugin marketplace add Elnora-AI/elnora-google-workspace
```

```
/plugin install google-workspace@elnora-google-workspace
```

Then run first-run setup:

```
/gw-setup
```

`/gw-setup` creates the plugin's Python venv, installs dependencies, walks you
through a Google Cloud project + Desktop OAuth client (driving a browser where it
can), authenticates, and verifies a real read. The agent does everything it can
for you and stops only where Google needs a human to click.

> You bring your own OAuth client — the plugin ships none. See
> [`settings.example.md`](settings.example.md) and
> [`accounts.example.json`](accounts.example.json) for the config shapes, and
> [SAFETY.md](SAFETY.md) for the security posture.

### Using Codex, Cursor, or another agent

The slash commands and skills are Claude-Code-shaped, but the `gw` CLI is a plain
Python program. For first-run setup under any agent, hand it
[`INSTALL_FOR_AGENTS.md`](INSTALL_FOR_AGENTS.md) — a gated, step-by-step runbook
that builds the venv, helps create the OAuth client (driving the browser where it
can), authenticates, and verifies a real read. For day-to-day use afterwards, drop
[`AGENTS.md`](AGENTS.md) at your project root and any agent can call the CLI
following the same rules (auth, JSON output, destructive-op confirmation).

---

## What you get

- **Gmail** — send, draft, list, read, reply / reply-all, scan for replies, manage
  drafts and attachments, labels, threads, trash.
- **Calendar** — create/update/get/delete events, list upcoming, list calendars,
  Google Meet links, attendees, timezones, reminders.
- **Drive** — list, get, upload, download, export, move, copy, trash, share.
- **Docs** — create, read, import Markdown as a native Doc, append, replace.
- **Sheets** — read, write, append, list.
- **Forms** — read metadata + responses, create and edit forms.
- **Tasks** — create, list, complete.
- **`gw api` — any Google API.** A generic Discovery invoker reaches services with
  no curated group (Slides, People, Chat, Admin SDK, Classroom, Apps Script, …) and
  any uncovered method, with schema introspection, dry-run validation, NDJSON
  pagination, and a destructive-method guard.
- **`gw schema`** — show parameters, scopes, and request/response shape for any command.
- **Multi-account OAuth** — name as many accounts as you like in `accounts.json`;
  tokens are stored in the OS keyring when available, else a 0600 file.

Everything is **config-driven** — accounts, the config dir, and every optional
feature come from your config or environment. Nothing personal or company-specific
is baked in.

### Slash commands

| Command | Does |
|---|---|
| `/gw-setup` | First-run: venv, deps, Google Cloud OAuth client, authenticate, verify |
| `/gw-inbox [timeframe]` | Quick inbox scan — recent emails with sender, subject, snippet |
| `/draft-email …` | Draft a Gmail email or reply (draft only — never sends) with CRM context |
| `/prep-meeting <event>` | Pre-meeting brief from CRM + transcripts (needs the optional knowledge base) |

### Skills & agents

- **Skills:** `gw-setup` (onboarding), a `google-workspace` router, and one per
  service — `gw-gmail`, `gw-calendar`, `gw-drive`, `gw-docs`, `gw-sheets`,
  `gw-forms`, `gw-tasks`, `gw-inbox` — plus `gw-api` for the generic invoker.
- **Agent:** `cold-outreach` — send outreach from a contact sheet or the CRM, scan
  for replies, and track stats (draft-first; the CRM path needs the optional
  knowledge base).

---

## Configuration

- **Accounts** live in `$GW_CONFIG_DIR/accounts.json` (default
  `~/.config/gw/accounts.json`), created by `gw auth login --account <name>`. See
  [`accounts.example.json`](accounts.example.json).
- **OAuth client** — provide your own Desktop client via
  `~/.config/gw/client_secret.json`, `GW_CLIENT_ID`/`GW_CLIENT_SECRET`, or
  `gw auth login --client-secret-file PATH`.
- **Tokens** are stored in the OS keyring (with the optional `keyring` package) or a
  0600 JSON file under the config dir. Nothing is ever written into the repo.
- Full option list: [`settings.example.md`](settings.example.md).

## The self-driving system (with knowledge-vault)

Install [`Elnora-AI/knowledge-vault`](https://github.com/Elnora-AI/knowledge-vault)
alongside this plugin and the two compose into one system that quietly keeps itself
up to date: Gmail and Calendar are the senses, the vault is the memory, and a
scheduled sync keeps a CRM fresh from your real activity. Each plugin still works
on its own — knowledge-vault is a full vault without Google, and every Google
command here works without a vault.

Batteries-included setup (once both plugins are installed):

```sh
gw auth login                 # one-time Google sign-in (browser OAuth; run /gw-setup first for the OAuth client)
gw crm init                   # scaffold contacts.csv + companies.csv in your vault
gw gmail sync-crm-install     # schedule email → CRM (auto-registers, see below)
gw calendar sync-crm-install  # schedule calendar → CRM
```

`gw crm init` needs only `vault_path` in `.claude/knowledge-base.local.md` — the file
knowledge-vault writes. The CRM lands at `<vault>/crm` by default (override with
`crm_dir`, or nest under `company_dir`). From then on the sync bumps
`last_contact_date`, promotes pipeline stages, and links meetings — no manual work.

Every connector feature degrades to a clean no-op when no knowledge base is
configured; the core Google commands never depend on it. Tune it with
`GW_INTERNAL_DOMAINS`, `GW_TRANSCRIPT_DIRS`, `GW_SLACK_USER_ID`, `GW_SLACK_CLI_BIN`,
and `GW_EXA_LIB` (see [`settings.example.md`](settings.example.md)).

## Scheduling

`gw gmail sync-crm-install` and `gw calendar sync-crm-install` register the CRM sync
on your OS's native scheduler automatically — launchd (macOS), Task Scheduler
(Windows), or the user crontab (Linux) — pinning the resolved knowledge-base config
so the detached job finds the same vault. If the scheduler can't be driven, the exact
command is printed instead (no elevated permissions are ever taken on your behalf).
`--interval-hours N` sets the cadence (default 2). Remove either with the matching
`sync-crm-uninstall`.

> Windows and Linux auto-registration is implemented but still pending a live
> verification pass on those platforms; macOS is fully verified.

## Safety

Read-only by default where it matters, explicit confirmation for destructive
operations, OS-keyring or 0600-file token storage, path-traversal validation,
credential scrubbing from all output, trash-not-delete for Drive, and draft-first
outreach. See [SAFETY.md](SAFETY.md).

## Part of the Elnora family

This plugin is one of a family of universal, config-driven Claude Code tools from [Elnora AI](https://github.com/Elnora-AI). Each tool is fully standalone — install just this one and everything above works. Install siblings and they start chaining automatically, no extra wiring:

- **[knowledge-vault](https://github.com/Elnora-AI/knowledge-vault)** — a plain-Markdown knowledge base with a CSV-backed CRM and task system. With both installed, Gmail and Calendar become the senses and the vault becomes the memory: `gw gmail sync-crm-install` and `gw calendar sync-crm-install` schedule syncs that bump `last_contact_date`, promote pipeline stages, and link meetings to contacts, while `/draft-email` and `/prep-meeting` pull CRM context into your writing, and actionable emails can be triaged straight into the vault's task system.
- **[elnora-slack](https://github.com/Elnora-AI/elnora-slack)** — the full Slack Web API as an agent-friendly CLI. The CRM connector here already knows how to read your Slack activity (`GW_SLACK_CLI_BIN`, `GW_SLACK_USER_ID`) so DMs count toward last-contact tracking, and meeting prep can pull Slack threads alongside email history.
- **[elnora-linear](https://github.com/Elnora-AI/elnora-linear)** — Linear issue tracking from Claude Code. Chain it with Gmail here to turn an email thread into a tracked issue, or reference Linear context when drafting replies about in-flight work.

Every chain degrades to a clean no-op when the sibling isn't installed — the core Google commands never depend on anything else. Browse the whole family at [github.com/Elnora-AI](https://github.com/Elnora-AI).

## Development

```
python -m pytest tests -q      # test suite
npm run check                  # secret + JSON guards (CI runs these)
```

## License

[Apache 2.0](LICENSE) © Elnora AI

# elnora-google-workspace

**Gmail, Calendar, Drive, Docs, Sheets, Forms, Tasks, Analytics and Search Console for Claude Code, from one agent-friendly CLI, with a `gw api` escape hatch to any Google API.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## Install

Run these one at a time: paste the first, wait for it to finish, then the second.

```
/plugin marketplace add Elnora-AI/elnora-google-workspace
```

```
/plugin install google-workspace@elnora-google-workspace
```

Then set it up:

```
/gw-setup
```

`/gw-setup` creates the plugin's Python venv, installs dependencies, walks you
through a Google Cloud project and Desktop OAuth client, authenticates, and
verifies a real read. It stops where Google needs a human to click.

> You bring your own OAuth client; the plugin ships none. See
> [`settings.example.md`](settings.example.md) and
> [`accounts.example.json`](accounts.example.json) for the config shapes, and
> [SAFETY.md](SAFETY.md) for the security posture.

### Using Codex, Cursor, or another agent

The slash commands and skills are Claude-Code-shaped, but `gw` is a plain Python
program. Hand any agent [`INSTALL_FOR_AGENTS.md`](INSTALL_FOR_AGENTS.md) for
first-run setup, then drop [`AGENTS.md`](AGENTS.md) at your project root so it
follows the same rules for auth, JSON output and destructive-op confirmation.

---

## What you get

| Service | Commands |
|---|---|
| **Gmail** | send, draft, list, read, reply and reply-all, scan for replies, drafts, attachments, labels, threads, trash |
| **Calendar** | create, update, get, delete and list events, list calendars, Meet links, attendees, timezones, reminders. Also edits booking pages by driving your own Chrome, since Google ships no API for them |
| **Drive** | list, get, upload, download, export, move, copy, trash, share |
| **Docs** | create, read, import Markdown as a native Doc, append, replace |
| **Sheets** | read, write, append, list |
| **Forms** | read metadata and responses, create and edit forms |
| **Tasks** | create, list, complete |
| **Analytics (GA4)** | properties, reports, realtime, metadata search, and a compatibility check that settles a field name before you spend a call. Read-only, opt-in scope |
| **Search Console** | sites, search analytics, sitemaps, URL inspection. Read-only by construction, opt-in scope |

`gw api` reaches any Google API through Discovery, covering services with no
curated group (Slides, People, Chat, Admin SDK, Classroom, Apps Script) and any
uncovered method, with schema introspection, dry-run validation, NDJSON
pagination and a destructive-method guard. `gw schema` shows parameters, scopes
and request and response shapes for any command.

Name as many accounts as you like in `accounts.json`. Tokens are stored in the OS
keyring when one is available, otherwise a 0600 file.

Everything is config-driven. Accounts, the config directory and every optional
feature come from your config or environment, so nothing personal or
company-specific is baked in.

`--output csv` and `--fields` trim a response to what an agent needs, and both go
before the subcommand: `gw --output csv analytics report ...`.

### Slash commands

| Command | Does |
|---|---|
| `/gw-setup` | First run: venv, deps, Google Cloud OAuth client, authenticate, verify |
| `/gw-inbox [timeframe]` | Inbox scan with sender, subject and snippet |
| `/draft-email` | Draft a Gmail email or reply, with CRM context. Drafts only, and never sends |
| `/prep-meeting <event>` | Pre-meeting brief from CRM and transcripts (needs the optional knowledge base) |

### Skills and agents

`gw-setup` for onboarding, a `google-workspace` router, one skill per service
(`gw-gmail`, `gw-calendar`, `gw-drive`, `gw-docs`, `gw-sheets`, `gw-forms`,
`gw-tasks`, `gw-inbox`, `gw-analytics`, `gw-searchconsole`) and `gw-api` for the
generic invoker.

The `cold-outreach` agent sends outreach from a contact sheet or the CRM, scans
for replies and tracks stats. It drafts first, and its CRM path needs the
optional knowledge base.

---

## Configuration

- **Accounts** live in `$GW_CONFIG_DIR/accounts.json` (default
  `~/.config/gw/accounts.json`), created by `gw auth login --account <name>`.
- **OAuth client**: provide your own Desktop client via
  `~/.config/gw/client_secret.json`, `GW_CLIENT_ID` and `GW_CLIENT_SECRET`, or
  `gw auth login --client-secret-file PATH`.
- **Tokens** go to the OS keyring (with the optional `keyring` package) or a 0600
  JSON file under the config directory. Nothing is written into the repo.
- **Opt-in scopes**: Analytics and Search Console are excluded from a default
  login. Add them with `gw auth login --add-scopes analytics,searchconsole`,
  which keeps the scopes an account already holds. A login replaces the token, so
  `--scopes` would drop everything it does not name.

Full option list: [`settings.example.md`](settings.example.md).

## With knowledge-vault

Install [`Elnora-AI/knowledge-vault`](https://github.com/Elnora-AI/knowledge-vault)
alongside this plugin and a scheduled sync keeps a CRM current from your real Gmail
and Calendar activity. Either works on its own.

```sh
gw auth login                 # one-time Google sign-in
gw crm init                   # scaffold contacts.csv and companies.csv in your vault
gw gmail sync-crm-install     # schedule email to CRM
gw calendar sync-crm-install  # schedule calendar to CRM
```

`gw crm init` needs only `vault_path` in `.claude/knowledge-base.local.md`, the
file knowledge-vault writes. The CRM lands at `<vault>/crm` by default. The sync
then bumps `last_contact_date`, promotes pipeline stages and links meetings.

Connector features are a clean no-op when no knowledge base is configured, and the
core Google commands do not depend on one.

## Scheduling

The `sync-crm-install` commands register the sync on your OS's native scheduler:
launchd on macOS, Task Scheduler on Windows, or the user crontab on Linux. They pin
the resolved knowledge-base config so the detached job finds the same vault. Where
the scheduler cannot be driven the exact command is printed instead, and no
elevated permissions are taken on your behalf. `--interval-hours N` sets the
cadence, defaulting to 2, and `sync-crm-uninstall` removes it.

> Windows and Linux auto-registration is implemented and still pending a live
> verification pass. macOS is verified.

## Safety

Read-only by default where it matters, explicit confirmation for destructive
operations, OS-keyring or 0600-file token storage, path-traversal validation,
credential scrubbing from output, trash rather than delete for Drive, and
draft-first outreach. See [SAFETY.md](SAFETY.md).

## Part of the Elnora family

Open-source agent tooling from [Elnora AI](https://github.com/Elnora-AI):
config-driven tools that wire Claude Code, or any AI coding agent, into the
systems you run your company on. Each works standalone, and installing several
chains them into end-to-end workflows.

<!-- ELNORA-FAMILY:START -->
- [elnora-linear](https://github.com/Elnora-AI/elnora-linear) — Linear issue management — search, bulk edit, agents, and a config-driven curator
- [elnora-slack](https://github.com/Elnora-AI/elnora-slack) — the entire Slack Web API as a CLI plus agent skills with a draft-and-approve send gate
- [elnora-whatsapp](https://github.com/Elnora-AI/elnora-whatsapp) — read, search, and send WhatsApp from your own paired account, 100% local
- [elnora-merit-aktiva](https://github.com/Elnora-AI/elnora-merit-aktiva) — Merit Aktiva accounting and Merit Palk payroll as a CLI and plugin
- [elnora-vanta](https://github.com/Elnora-AI/elnora-vanta) — read-only Vanta compliance — frameworks, tests, controls, and vulnerabilities as agent-friendly JSON
- [elnora-luma](https://github.com/Elnora-AI/elnora-luma) — Luma (lu.ma) events — all 61 public API endpoints as a spec-driven CLI with safety guardrails
- [elnora-travel](https://github.com/Elnora-AI/elnora-travel) — a real travel agent — live flights, hotels, Airbnb, Booking.com, and routes in one itinerary
- [elnora-websearch-tools](https://github.com/Elnora-AI/elnora-websearch-tools) — web search — Exa, Tavily, Perplexity, Firecrawl, and Valyu CLIs and skills in one plugin
- [knowledge-vault](https://github.com/Elnora-AI/knowledge-vault) — an Obsidian-compatible knowledge base for agent teams — search and save your work to any vault
<!-- ELNORA-FAMILY:END -->

## Development

```
python -m pytest tests -q      # test suite
npm run check                  # secret and JSON guards over tracked files
npm run check:commits          # the same guards over commit messages
```

CI runs all of these. The commit scan exists because a file guard cannot see a
commit message or a PR body, and on a public repo both are readable by anyone.
Use placeholders in examples and fixtures: `example.com`, `sc-domain:example.com`,
`properties/123456789`.

## License

[Apache 2.0](LICENSE) © Elnora AI

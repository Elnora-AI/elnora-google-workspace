# Example settings

Nothing here is required to use the core Google commands (Gmail, Calendar,
Drive, Docs, Sheets, Forms, Tasks, `gw api`). These are the optional knobs.

## Accounts (`accounts.json`)

Your real accounts config lives at `$GW_CONFIG_DIR/accounts.json`
(default `~/.config/gw/accounts.json`) and is **gitignored**. It is created and
populated for you by `gw auth login --account <name>`. See
[`accounts.example.json`](./accounts.example.json) for the shape.

## OAuth client

The plugin never ships an OAuth client. Provide your own Desktop OAuth client
via one of:

- `~/.config/gw/client_secret.json` (recommended — `/gw-setup` puts it here), or
- `GW_CLIENT_ID` / `GW_CLIENT_SECRET` environment variables, or
- `gw auth login --client-secret-file /path/to/client_secret.json`

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `GW_CONFIG_DIR` | Config + cache + token dir | `~/.config/gw` |
| `GW_KEYRING` | Set to `off` to disable OS-keyring token storage | keyring used if available |
| `GW_API_CONFIRM` | Set to `off` to disable the destructive-op `--confirm` guard on `gw api` | guard on |

### Optional knowledge-base connector

Install [`Elnora-AI/knowledge-vault`](https://github.com/Elnora-AI/knowledge-vault)
(or any plugin that writes `.claude/knowledge-base.local.md`) to enable CRM sync,
meeting prep, and outreach. These settings tune that connector; every one is
optional and each feature no-ops cleanly when unset.

The CRM location is read from the knowledge-base config file itself (frontmatter
keys, not env vars). Only `vault_path` is required — `gw crm init` scaffolds the
CRM at `<vault_path>/crm`:

| Config key | Purpose | Default |
|---|---|---|
| `vault_path` | Absolute path to your vault (written by knowledge-vault) | required |
| `crm_dir` | Subfolder under the vault that holds the CRM CSVs | `crm` |
| `company_dir` | Optional extra prefix nested between `vault_path` and `crm_dir` | empty |

| Variable | Purpose | Default |
|---|---|---|
| `GW_KB_CONFIG` | Explicit path to the knowledge-base config markdown | discovered from CWD |
| `GW_INTERNAL_DOMAINS` | Comma-separated email domains to treat as internal (skipped by CRM sync) | empty |
| `GW_TRANSCRIPT_DIRS` | Comma-separated meeting-transcript dirs meeting-prep scans | empty |
| `GW_SLACK_USER_ID` | Slack user id to DM meeting briefs to | unset (prints to stdout) |
| `GW_SLACK_CLI_BIN` | Path to a Slack CLI entry point for DM delivery | unset |
| `GW_EXA_LIB` | Path to an Exa CLI `lib` dir for optional contact enrichment | unset |
| `GW_OUTREACH_VALUE_PROP` / `GW_OUTREACH_CTA` | Default outreach copy when a template doesn't supply it | empty |

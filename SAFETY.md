# Safety & security posture

This plugin holds OAuth access to your Google account, so it is built to be
conservative by default. The guarantees below are enforced in code and covered by
the test suite.

## Credentials

- **You bring your own OAuth client.** The plugin ships no client id/secret. You
  create a Desktop OAuth client in your own Google Cloud project (`/gw-setup`
  walks you through it) and it stays yours.
- **Token storage.** Tokens are stored in the OS keyring (service `gw-cli`) when the
  optional `keyring` package and a real backend are available; otherwise a JSON file
  written atomically with `0600` permissions. File permissions are re-checked and
  re-tightened on every read. `GW_KEYRING=off` forces file storage. A keyring write is
  read back before it is trusted, so a no-op backend can never silently drop a token.
- **Nothing secret touches the repo.** Config, `accounts.json`, `client_secret.json`,
  and tokens all live under `$GW_CONFIG_DIR` (default `~/.config/gw`). The `.gitignore`
  also blocks `.google-token*.json`, `credentials*.json`, `client_secret*.json`, and
  `accounts.json` from ever being committed.
- **Output is scrubbed.** All error and crash output passes through a credential
  scrubber that redacts tokens, client secrets, and API keys before anything is
  printed. Tracebacks are scrubbed by a global crash handler.

## Scopes

- Scopes are the **narrowest per service** that cover the functionality, requested
  only for the services you authorize. `gw auth login --scopes gmail,calendar` grants
  a subset; `--readonly` requests read-only variants.
- Unverified Google Cloud apps cap requested scopes (~25) and require you to list your
  own account as a Test user — `/gw-setup` explains this and keeps the default set
  well under the cap.

## Destructive operations

- **`gw api`** flags any destructive method (delete / clear / remove / trash / stop /
  revoke, or HTTP `DELETE`) and refuses to run it without `--confirm`.
  `GW_API_CONFIRM=off` disables the guard for scripted use.
- **Drive** has **no permanent delete** — only `trash` / `untrash`, which are
  recoverable from the Drive UI.
- **Drive sharing** grants access to **specific people only** (`type=user`).
  Creating an "anyone with the link" permission is deliberately unsupported.
- **Calendar delete** does not notify attendees unless you pass `--notify`.
- **Outreach** is draft-first: the `cold-outreach` agent and `/draft-email` create
  Gmail drafts for your review and never auto-send.

## Input validation

- Account names are validated before they become file paths or keyring keys — no path
  separators, `..`, null bytes, leading dots, or Windows reserved device names.
- Template and campaign names are rejected if they contain path separators.
- CSV writes neutralize spreadsheet formula-injection prefixes (`=`, `@`, tab, CR).

## Reporting a vulnerability

Please email **security@elnora.ai**. See [`.github/SECURITY.md`](.github/SECURITY.md).

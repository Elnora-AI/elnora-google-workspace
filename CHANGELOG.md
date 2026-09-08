# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- **`gw analytics`** — read Google Analytics 4: `properties`, `report`, `realtime`,
  `metadata`, `check`. Responses are flattened out of the GA4 wire format into plain
  records, and metric values are converted to numbers using the type the API declares,
  so rows sort and sum without reparsing. `metadata --grep` and `check` exist to settle
  a field name before spending a call, which is the most common way a GA4 request fails.
- **`gw searchconsole`** — read Google Search Console: `sites`, `query`, `sitemaps`,
  `inspect`. Positional `keys` are named by their dimension. Read-only by construction:
  the write half of the API is not exposed, and a test asserts it stays unreachable.
- **Opt-in OAuth scopes** `analytics` (`analytics.readonly`) and `searchconsole`
  (`webmasters.readonly`). Excluded from a default login, so no existing consent screen
  widens and nobody is re-prompted for APIs they do not use.
- **`gw auth login --add-scopes`** — authorize one more API while keeping the scopes the
  account already has. A login replaces the token, so `--scopes` silently dropped
  everything it did not name; that path now warns (`SCOPES_NARROWED`) listing what it
  would lose, and `--add-scopes` refuses when there is no token to add to rather than
  quietly issuing a narrower one.
- Skills `gw-analytics` and `gw-searchconsole`, routed from `google-workspace`.

### Fixed
- The credential scrubber redacted documentation URLs. Its generic "40+ characters of
  base64" pattern also matches an ordinary URL path, so a Google API error naming the
  valid field names arrived as `see https://developers.google.[REDACTED]-schema`,
  destroying the most useful part of the message. URL spans are now scrubbed with the
  patterns that name a real credential shape and spared the generic run; a key inside a
  URL is still redacted, and so is a bare base64 blob. Affects every `gw` command.

- `gw crm init` / `gw crm status` / `gw crm path` — scaffold and inspect the CRM
  (`contacts.csv` + `companies.csv`) directly under your vault. Makes the CRM sync
  work out of the box for any knowledge-vault user, not just a pre-existing CRM.
- `gw gmail sync-crm-install` / `sync-crm-uninstall` — the email→CRM sync can now be
  scheduled with a command (previously only calendar could).
- Cross-platform auto-scheduling: `sync-crm-install` now registers the job on the
  host's native scheduler automatically — launchd (macOS), Task Scheduler (Windows),
  or the user crontab (Linux) — and pins the resolved knowledge-base config so the
  detached job finds the same vault. Falls back to printing the command if the
  scheduler can't be driven. (Windows/Linux auto-registration pending live verification.)

### Changed
- The knowledge-base connector now requires only `vault_path`; `crm_dir` defaults to
  `crm` and `company_dir` is optional. Existing configs that set these keys are
  unaffected. An empty (freshly-scaffolded) CRM now syncs as a clean no-op instead of
  reporting an error.

## [1.2.0](https://github.com/Elnora-AI/elnora-google-workspace/compare/v1.1.0...v1.2.0) (2026-08-10)


### Features

* **calendar:** booking page timezone, rename and delete ([#28](https://github.com/Elnora-AI/elnora-google-workspace/issues/28)) ([ddf1674](https://github.com/Elnora-AI/elnora-google-workspace/commit/ddf167454250dccab1383dd424eb7c4df107e2b4))
* **calendar:** read and edit booking pages by driving Chrome ([#26](https://github.com/Elnora-AI/elnora-google-workspace/issues/26)) ([97d91bb](https://github.com/Elnora-AI/elnora-google-workspace/commit/97d91bbb9776796598b9b68bdc46b6427b00a3db))

## [1.1.0](https://github.com/Elnora-AI/elnora-google-workspace/compare/v1.0.0...v1.1.0) (2026-07-24)


### Features

* **gmail:** add modify-labels command and gmail.modify scope ([#21](https://github.com/Elnora-AI/elnora-google-workspace/issues/21)) ([8209f7c](https://github.com/Elnora-AI/elnora-google-workspace/commit/8209f7c99627864c5d1fa37e23c8b91286dde4d2))

## 1.0.0 — 2026-07-13

Initial public release of the Google Workspace plugin (Gmail, Calendar, Drive, Docs, Sheets, Forms, Tasks, plus `gw api`/`gw schema` and multi-account OAuth).

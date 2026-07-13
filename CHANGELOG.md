# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added
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

## 1.0.0 — 2026-07-13

Initial public release of the Google Workspace plugin (Gmail, Calendar, Drive, Docs, Sheets, Forms, Tasks, plus `gw api`/`gw schema` and multi-account OAuth).

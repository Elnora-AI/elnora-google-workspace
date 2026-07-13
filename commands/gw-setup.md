---
name: gw-setup
description: First-run setup for the Google Workspace plugin — creates the venv, walks you through a Google Cloud OAuth client, and authenticates. Gets you from install to first result.
allowed-tools: [Bash]
user_invocable: true
---

# gw-setup

Run the **gw-setup** skill to bootstrap this plugin end to end. Do everything you
can for the user and only stop where a human must click something in Google Cloud.

Load `skills/gw-setup/SKILL.md` and follow it in order:

1. **Environment** — detect `python3`/`uv`, create the plugin-local `.venv`,
   install deps, generate the `gw` launcher, and verify `gw --version`.
2. **Google Cloud OAuth client** — the hard part. Offer to drive a browser (via
   the Chrome DevTools MCP) through creating a project, enabling the 7 APIs,
   configuring the consent screen, and creating a **Desktop** OAuth client; save
   the client JSON to `~/.config/gw/client_secret.json`. If the user declines
   automation, print the exact URLs and click-by-click steps from `gw auth setup`.
3. **Authenticate** — `gw auth login` (localhost callback).
4. **Verify** — `gw auth status`, then a real read (`gw calendar calendars` or
   `gw gmail labels`).
5. **Optional** — suggest installing `Elnora-AI/knowledge-vault` to enable the
   CRM / meeting-prep / outreach connector.

Report what succeeded and the one or two manual clicks the user still owes you.

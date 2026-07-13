# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x.x   | Yes       |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately via one of:

- **Email:** [security@elnora.ai](mailto:security@elnora.ai)
- **GitHub Security Advisories:** [Report a vulnerability](https://github.com/Elnora-AI/elnora-google-workspace/security/advisories/new)

Include as much as you can: a description, steps to reproduce, potential impact, and any suggested fix.

## Response Timeline

- **Acknowledgement:** within 48 hours
- **Initial assessment:** within 5 business days
- **Fix and disclosure:** within 90 days

## Scope

**In scope:**

- The plugin content in this repository (skills, commands, agents, and CLIs) and how it authenticates to and calls Google APIs
- OAuth and token handling (`gw` authentication, keyring-backed token storage)
- The CI guards (`scripts/check-no-secrets.mjs`, `scripts/check-json.mjs`)

**Out of scope:**

- Third-party dependencies (report to their maintainers)
- Google Workspace itself and the Google APIs this plugin calls
- A user's own Google account contents, OAuth client, or granted scopes

## Best Practices for Users

- Keep your OAuth token and credential files gitignored — `.google-token*.json`, `credentials*.json`, `client_secret*.json`, and `accounts.json` are ignored by default. Never commit them.
- Provide any API keys or client secrets via environment variables or the system keyring; never hardcode them.
- Grant the plugin only the Google API scopes you actually need.

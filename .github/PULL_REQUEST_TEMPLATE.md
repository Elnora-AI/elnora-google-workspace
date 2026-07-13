<!-- PR title must be a Conventional Commit, e.g. "feat: add gw forms list command" -->

## What & why

<!-- What does this change and why? -->

## Checklist

- [ ] `npm run check` passes (no company/person/customer/path-specific strings; manifests valid)
- [ ] `python -m pytest tests -q` passes (if Python changed)
- [ ] Docs/examples use only the generic placeholder entities (Acme / Globex / Jane Doe / example.com)
- [ ] No committed OAuth tokens, credentials, client secrets, or account files
- [ ] Cross-platform (macOS, Linux, Windows); no hardcoded paths or timezones

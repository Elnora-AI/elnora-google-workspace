# Contributing

Thanks for helping improve elnora-google-workspace. This is a universal, config-driven Claude Code plugin — contributions must keep it that way.

## Ground rules

1. **Stay universal.** No company-, person-, customer-, or path-specific content anywhere. Examples use placeholder entities: Acme Corp / Globex, Jane Doe / Sam Rivera, `example.com`. The CI guard `scripts/check-no-secrets.mjs` enforces this and will fail your PR otherwise.
2. **Never commit credentials.** OAuth tokens and client secrets stay out of the repo. `.google-token*.json`, `credentials*.json`, `client_secret*.json`, and `accounts.json` are gitignored — keep it that way. Provide secrets via environment variables or the system keyring.
3. **Cross-platform.** Everything must work on macOS, Linux, and Windows. Use `python3 ... || python ...` in command invocations, `pathlib` for paths, UTF-8 everywhere. Never hardcode a timezone.
4. **Keep dependencies lean.** Only add a dependency when there's no reasonable standard-library alternative.

## Development

```sh
# JS guards
npm run check           # runs check-no-secrets and check-json

# Python tests
pip install google-api-python-client google-auth-oauthlib click keyring pytest
python -m pytest tests -q
```

## Pull requests

- Use a [Conventional Commit](https://www.conventionalcommits.org/) PR title (`feat:`, `fix:`, `docs:`, `chore:`, …). CI lints this.
- Run `npm run check` and `python -m pytest tests -q` before opening a PR.
- Keep changes surgical and focused. Update docs when behavior changes.
- Fill in the PR checklist.

## Reporting security issues

See [SECURITY.md](SECURITY.md) — do not open a public issue for vulnerabilities.

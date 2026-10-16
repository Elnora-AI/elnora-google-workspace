---
name: gw-postmaster
description: >
  Read Gmail Postmaster Tools via CLI — the domains you registered, and per day the spam rate,
  DKIM / SPF / DMARC pass rates, delivery errors and Google's domain reputation.
  TRIGGERS: "postmaster", "postmaster tools", "spam rate", "domain reputation", "sender
  reputation", "deliverability", "are we going to spam", "DKIM pass rate", "DMARC pass rate",
  "delivery errors", "Gmail bulk sender", "email reputation"
---

# Gmail Postmaster Tools

Read-only by construction: nothing here creates, verifies or deletes a domain or touches its
users, and a test asserts it stays that way.

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Scope prerequisite

Postmaster is an opt-in scope, so a default `gw` token does not carry it. Add it once:

```bash
$CLI auth login --add-scopes postmaster
```

It grants two read-only scopes, one per API version: `postmaster.traffic.readonly` (v2: the
daily stats) and `postmaster.readonly` (v1: the domain list and the domain reputation). An
account authorized before both were in the opt-in gets the stats with a note that the
reputation needs the login again. The domain is registered and verified in the Postmaster
Tools UI; the API reads only domains the account was given there.

## Commands

```bash
$CLI postmaster domains --compact

$CLI postmaster stats --domain example.com \
  [--since 30daysAgo] [--until yesterday] \
  [--metrics spam_rate,dkim,spf,dmarc,delivery_error_rate]
```

`--metrics` also takes `tls_outbound` and `delivery_errors` (a count). Rates are fractions:
`0.003` is 0.3%. `--until` is inclusive.

## Read the empty days right

Google reports a day only when enough mail reached Gmail users that day. A day missing from
`rows` is a day under that threshold, never a day without problems, and `days_reported` says
how many came back. Mail to company mailboxes (Microsoft 365, Google Workspace, security
gateways) is not counted at all, so a B2B sender can send thousands of emails and see no row.
Postmaster measures the Gmail-consumer side; placement at company mailboxes needs seed-inbox
tests.

## Response shape (validated)

```json
{"domain":"example.com","window":{"since":"2026-09-01","until":"2026-09-30"},
 "metrics":["spam_rate","dkim","spf","dmarc","delivery_error_rate"],
 "rows":[{"date":"2026-09-16","spam_rate":0.0,"dkim":1.0,"spf":0.0,"dmarc":1.0,
          "delivery_error_rate":0.0,"reputation":"HIGH"}],
 "days_reported":1,"notes":["Postmaster reports a day only when ..."]}
```

`domains`: `{"domains":[{"domain","permission","created"}],"count"}`

## Anything not covered here

The v2 compliance status (bulk-sender requirements) is one `gw-api` call with the same scope:

```bash
$CLI api call gmailpostmastertools:v2 domains.getComplianceStatus \
  --params '{"name":"domains/example.com/complianceStatus"}' --compact
```

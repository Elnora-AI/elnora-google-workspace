---
name: draft-email
description: Draft a Gmail email or reply in your voice with full CRM/knowledge-base context loaded and every claim verified. Always creates a draft — never sends.
argument-hint: <thread-id|recipient-email> [topic]
allowed-tools: Read, Glob, Grep, Bash, WebFetch, Task
---

# Draft Email: $ARGUMENTS

## Task

Draft a Gmail email — either a reply to an existing thread (`<thread-id>`) or a new email to someone (`<recipient-email> [topic]`) — in your voice, with full context loaded and every claim verified.

**This skill creates a Gmail DRAFT only. Never sends.** The user reviews and approves before any send — never send without explicit approval.

## Hard preconditions

You MUST complete every step in order. Skipping is not allowed.

### Step 1 — Load your writing-voice guide (optional)

If your knowledge base has a writing-voice guide, load it and apply it. If there's none, skip this step and draft in a clear, professional default voice — the command must still work with no voice doc.

If you have a voice guide, pay particular attention to:

- Any **at-a-glance** / quick-reference section — banned phrases, banned punctuation, hard safety rules. This is the 90% of the catch.
- The relevant **by channel** subsection (cold outreach / inbound consulting / professional contacts / etc.) for the email type you're drafting.
- The **core voice principles** if drafting more than a one-liner.

If your knowledge base is configured (`.claude/knowledge-base.local.md`), the guide typically lives under `{vault_path}/{company_dir}/` — resolve the path from that config.

### Step 2 — Identify the recipient(s) and load CRM context

If a thread-id was passed:
```bash
python3 plugins/google-workspace/cli/gw.py gmail get-thread <thread-id> --compact
```

Extract the external participant(s) (sender + non-internal Cc). For each one:

1. Look them up in `vault: 10-crm/contacts.csv` by email. If found, capture: slug, full name, role, company, stage, last_contact_date, last_contact_channel, last_meeting_date, next_action, priority, notes.
2. Look up their company in `vault: 10-crm/companies.csv` by name. Capture: slug, stage, NTE/billed/remaining (KPI columns), notes.
3. **If their company stage is `active-customer` or `customer`**, also read the CRM's company/account detail file (its per-company Markdown file) for the rich relationship context (current state, open contracts, recent invoices, timeline, open items).

If no thread-id (new email), the recipient email is in $ARGUMENTS. Same lookups.

### Step 3 — Load relationship context (optional)

If transcript scanning is configured, search for any past meeting transcripts that include this person. The directories to scan come from the `GW_TRANSCRIPT_DIRS` environment variable (comma-separated, relative to the company dir). Skip this step if it's unset.

```bash
# GW_TRANSCRIPT_DIRS is a comma-separated list of dirs relative to {company_dir}.
for d in ${GW_TRANSCRIPT_DIRS//,/ }; do
  grep -lri "<their email>" "{vault_path}/{company_dir}/$d" 2>/dev/null
done | head -5
```

Read the **2 most recent** transcripts. Pull out:
- What was discussed
- Action items (who owes what)
- Open questions / unresolved threads
- Any commitments the user made

If the recipient is at a paying customer, also read the CRM's company/account detail file for any open contract, SOW, or negotiation log. Don't draft anything that contradicts what's already in flight.

### Step 4 — Pull the email thread context (if reply)

For a reply, read the full thread to know what was actually said most recently:

```bash
python3 plugins/google-workspace/cli/gw.py gmail get-thread <thread-id> --compact
```

Then read the latest 1-2 messages in full:

```bash
python3 plugins/google-workspace/cli/gw.py gmail get <message-id> --compact
```

Extract: what they asked, what tone they used, any specific claims/numbers/dates they made that you'll need to respond to or verify.

### Step 5 — External research (only when needed)

If the draft will reference something **outside** the vault — a recipient's recent paper, their company's press release, a market stat, an industry trend — search before claiming it. Order of preference:

1. **Exa** — `mcp__exa-search` or `/exa-cli` skill — best for finding people's recent work, papers, blog posts, LinkedIn profiles.
2. **Tavily** — `/tavily-cli` skill — best for press releases, company news, recent web events.
3. **Perplexity** — `/perplexity-cli` skill — best for synthesized answers about industry topics.
4. **Valyu** — `/valyu-cli` skill — best for finance, private-equity, scientific source-grounded answers.

> If a claim cannot be verified by external research, do not include it. Replace with something verifiable or omit.

### Step 6 — Verify every claim BEFORE drafting

Make a list of every factual assertion in the draft you're about to write. For each, verify against the right source:

| Claim type | Verification source |
|---|---|
| Date/meeting reference | `gw calendar list` (run forward + back enough days to cover the date) |
| Dollar amount / contract value / billed amount | The CRM's company/account detail file; Stripe CLI (or your billing tool) for invoices |
| URL — internal/company page | Verify it exists by `curl -sI` or `WebFetch`. **Zero tolerance** for unverified links. |
| URL — external | Same — `curl -sI` or `WebFetch`. Must return 200 (or appropriate redirect). |
| Their recent paper/work | Exa search result with source URL |
| Industry stat / market data | Tavily/Perplexity search with citation |
| Compliance status (SOC 2, ISO 27001, etc.) | Your compliance policies in the knowledge base. Be precise — "observation window closes [date]" not "we are certified" |
| CRM contact details (role, company, last touch) | `contacts.csv` directly |
| Past meeting content | The transcript file itself, not memory |

If you list a verification source for a claim, **actually run the check**. Don't trust memory.

### Step 7 — Draft the email

Compose the body in your voice. Apply the channel-specific overlay from your voice guide, if you have one:

- Cold outreach → under 150 words, lead with their problem, one CTA, sign off with your name, no emojis.
- Inbound consulting → warm + specific deliverables, longer is OK if there's a real relationship, social proof stays.
- Professional contacts (accountants, lawyers) → warm, context-before-ask, friendly close.
- Reply to existing thread → respond to what they SAID, not what you think they're DOING. Keep tone consistent with the thread.

**Self-check before submitting (silently, every time):**

1. Em-dash count — max 2.
2. Banned phrases scan — search the draft for every entry in your voice guide's banned-phrases list, if you have one. If any hit, rewrite.
3. AI-tell scan — does any sentence sound like Claude wrapping up a thought? Cut it.
4. URL verification — every URL has been checked.
5. Verbalized motivations — does any sentence start with "I understand you want to…" or "I know you're trying to…"? Rewrite.
6. Confidential clients — any company named that isn't already public? Cut or ask the user.
7. Emojis — zero in email body.
8. Pending vs done — anything stated as complete that's actually pending someone else's approval?
9. Commercial-relationship language (only for customer-facing material) — any reference to billable hours / consulting blocks / "the rest is in the next package"? Cut.

### Step 8 — Create the Gmail draft (never send)

Use `gmail draft` for new emails or `gmail draft_reply` for replies:

```bash
# New email
python3 plugins/google-workspace/cli/gw.py gmail draft \
  --to "<recipient>" \
  --subject "<subject>" \
  --body "<body>" \
  [--cc "<cc>"] \
  --compact

# Reply
python3 plugins/google-workspace/cli/gw.py gmail draft-reply \
  <message-id-of-the-message-you're-replying-to> \
  --body "<body>" \
  --compact
```

The CLI returns a draft id. **Do NOT call `gmail send` or `gmail send-draft`.** Approval has to come from the user explicitly.

### Step 9 — Show the user the draft + verification report

Print, in this format:

```
DRAFT CREATED

Recipient: <name> <email>
Subject: <subject>
Channel: <cold-outreach | inbound-consulting | reply | professional-contact | …>
Tone overlay applied: <which voice guide section, if any>
Em-dash count: <n>
Banned-phrase check: clean / 1 hit ([phrase])
URLs in draft (all verified):
  - <url> ✓ HTTP 200
  - <url> ✓ HTTP 200
Claims verified:
  - "<claim>" ← <source>
  - "<claim>" ← <source>
External research used (if any):
  - Exa query: "<query>" → <result>
CRM context loaded:
  - Contact: <slug> (<stage>) — last_contact_date <date>
  - Company: <slug> (<stage>)
  - Customer file: <yes/no>
  - Past transcripts read: <2 paths>

Gmail draft id: <draft-id>
Open in Gmail to review: https://mail.google.com/mail/u/0/#drafts/<draft-id>

──── DRAFT BODY ────
<full body>
──────────────────────

Reply "send" to send via `gw gmail send-draft <draft-id>`, or paste edits.
```

## Examples

**Reply to an existing thread:**
```
/draft-email 19d88e7b7f93caee
```

**New cold outreach email to a prospect:**
```
/draft-email prospect@example.com partnership conversation
```

**Follow-up to an accountant about a tax credit:**
```
/draft-email accountant@example.com R&D credit Q3 status
```

## Notes

- This skill is the ONLY supported path for drafting emails on the user's behalf. Do not use raw `gmail.send`/`gmail.reply` without going through these steps — those calls bypass voice loading and verification.
- Sending is always a separate, explicit step the user authorizes. Never auto-send.
- After the user says "send" / "approved", run `gw gmail send-draft <draft-id>` (if CRM auto-tracking is wired up, this bumps `last_contact_date` for known recipients with channel=`email-out`).
- If the user edits the draft inline, regenerate via `gw gmail update-draft <draft-id> ...` and re-show.
- Log notable corrections back into your voice guide, if you keep one.

---
name: cold-outreach-v1
goal: reply
---

# Cold Outreach Email Template v1

A neutral starter template. Replace the copy with your own before sending real
outreach. Placeholders are filled from your contact data; `{value_prop}` and
`{cta}` also read the `GW_OUTREACH_VALUE_PROP` / `GW_OUTREACH_CTA` environment
variables when a contact or template does not supply them.

## Template

```
Subject: {subject_line}

Hi {first_name},

{opening_line}

{value_prop}

{cta}

Best,
Your Name
```

### Placeholder Definitions

| Placeholder | Description | Rules |
|---|---|---|
| `{subject_line}` | Email subject | Under 50 chars, specific to the recipient, no clickbait |
| `{first_name}` | Recipient's first name | Always use first name only |
| `{opening_line}` | Why you are emailing this person specifically | 1-2 sentences. Reference their work or shared context. Lead with THEM. |
| `{value_prop}` | What you offer and why it matters to them | 2-3 sentences. Tie it to their situation. Plain language, no jargon. |
| `{cta}` | Single call to action | 1 sentence. One low-pressure ask. Make it easy to say yes. |

---

## Example Emails (fictional)

These are illustrative only. People, companies, and products below are invented.

### 1. Outreach to a team lead

```
Subject: Quick note for Acme Robotics

Hi Dana,

I saw Acme Robotics recently opened several engineering roles — scaling that fast
usually makes onboarding and process consistency a real pain point.

We build tooling that helps growing teams standardize repetitive workflows so new
hires ramp up faster. A couple of teams your size have found it useful.

Would a short call next week be worth it to see if it fits how you work?

Best,
Your Name
```

### 2. Follow-up after meeting at an event

```
Subject: Following up from the meetup

Hi Sam,

Great meeting you at the meetup last week. Your point about keeping documentation in
sync across tools stuck with me.

That is exactly the kind of problem our product helps with — one source of truth that
updates everywhere. Happy to send a short demo if it is useful.

No pressure either way — let me know if you'd like to continue the conversation.

Best,
Your Name
```

---

## Template Usage Notes

- These fictional examples serve as few-shot guidance for the outreach agent.
- Personalize each email with real context about the recipient; generic fills are a fallback, not the goal.
- If there is not enough context to write a specific opening line, flag the contact for manual review rather than sending a generic email.

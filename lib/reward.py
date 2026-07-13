"""Reply classification and sentiment mapping for outreach campaigns.

Classifies email replies by sentiment (positive, negative, bounce, rsvp,
neutral) and maps sentiments to CRM stages. Used by cold_outreach_agent.py.
"""

from __future__ import annotations

import re

# Keywords for simple sentiment classification
_POSITIVE_KEYWORDS = [
    "interested", "love to", "would like", "let's", "lets", "set up",
    "schedule", "demo", "meeting", "call", "sounds good", "tell me more",
    "learn more", "happy to", "great", "yes", "sure", "absolutely",
    "available", "free to", "book", "calendar",
]

_NEGATIVE_KEYWORDS = [
    "not interested", "unsubscribe", "remove me", "no thanks",
    "no thank", "not for us", "not a fit", "opt out",
    "don't contact", "do not contact", "wrong person",
]

# Explicit opt-out keywords — subset of _NEGATIVE_KEYWORDS used to distinguish
# "unsubscribed" (CRM status) from general "declined". Import in agents.
UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe", "remove me", "opt out", "don't contact", "do not contact",
]

# Single-word keywords that need word-boundary matching to avoid false positives
# (e.g., "pass" shouldn't match "password", "stop" shouldn't match "nonstop")
_NEGATIVE_WORD_BOUNDARY = [r"\bpass\b", r"\bstop\b"]

_RSVP_KEYWORDS = [
    "rsvp", "i'll be there", "count me in", "see you there",
    "attending", "registered", "signed up", "i'll attend",
    "see you on", "will be there", "plan to attend",
]

_BOUNCE_KEYWORDS = [
    "undeliverable", "delivery failed", "address not found",
    "mailbox not found", "user unknown", "rejected", "permanent failure",
]

# SMTP error codes need word-boundary matching to avoid false positives
# (e.g., "5:50 PM" or "5503 Main St" should not trigger bounce detection)
_BOUNCE_CODE_PATTERNS = [r"\b550\b", r"\b551\b", r"\b552\b", r"\b553\b", r"\b554\b"]

# Consolidated sentiment → CRM stage map. Import this instead of redefining.
SENTIMENT_TO_CRM_STAGE: dict[str, str] = {
    "rsvp": "replied",
    "positive": "replied",
    "negative": "lost",
    "bounce": "lost",
    "neutral": "replied",
}


def classify_reply(reply_text: str | None) -> str:
    """Classify a reply as rsvp, positive, negative, bounce, or neutral.

    Simple keyword matching for v1. Can be upgraded to LLM-based
    classification later.

    Args:
        reply_text: The reply email body text.

    Returns:
        One of: "rsvp", "positive", "negative", "bounce", "neutral"
    """
    if not reply_text:
        return "neutral"
    text = reply_text.lower()

    # Check bounce first (auto-replies from mail servers)
    for kw in _BOUNCE_KEYWORDS:
        if kw in text:
            return "bounce"
    for pattern in _BOUNCE_CODE_PATTERNS:
        if re.search(pattern, text):
            return "bounce"

    # Check negative (explicit rejection/unsubscribe)
    for kw in _NEGATIVE_KEYWORDS:
        if kw in text:
            return "negative"
    for pattern in _NEGATIVE_WORD_BOUNDARY:
        if re.search(pattern, text):
            return "negative"

    # Check RSVP (event attendance confirmation — before general positive)
    for kw in _RSVP_KEYWORDS:
        if kw in text:
            return "rsvp"

    # Check positive (interest signals)
    for kw in _POSITIVE_KEYWORDS:
        if kw in text:
            return "positive"

    return "neutral"

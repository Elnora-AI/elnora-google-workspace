"""Tests for reward module — reply classification and sentiment mapping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from reward import (
    SENTIMENT_TO_CRM_STAGE,
    UNSUBSCRIBE_KEYWORDS,
    classify_reply,
)


def test_classify_reply_positive():
    assert classify_reply("Sounds good, let's set up a call!") == "positive"
    assert classify_reply("I'd love to learn more about this") == "positive"
    assert classify_reply("Yes, I'm interested") == "positive"


def test_classify_reply_negative():
    assert classify_reply("Not interested, please remove me") == "negative"
    assert classify_reply("No thanks, not a fit for us") == "negative"


def test_classify_reply_bounce():
    assert classify_reply("Delivery failed: address not found") == "bounce"
    assert classify_reply("550 User unknown") == "bounce"


def test_classify_reply_bounce_smtp_word_boundary():
    """SMTP codes use word-boundary matching — no false positives on addresses/times."""
    assert classify_reply("Our lab is at 5503 Main St") != "bounce"
    assert classify_reply("Available from 5:50 PM") != "bounce"
    assert classify_reply("We have 5540 samples ready") != "bounce"
    # But real SMTP error codes still match
    assert classify_reply("Error 550: mailbox unavailable") == "bounce"
    assert classify_reply("554 delivery error") == "bounce"


def test_classify_reply_rsvp():
    """RSVP keywords should be classified before general positive."""
    assert classify_reply("I'll be there! Count me in.") == "rsvp"
    assert classify_reply("Registered for the event, see you there") == "rsvp"
    assert classify_reply("I'll attend the workshop") == "rsvp"


def test_classify_reply_negative_word_boundary():
    """Single-word negatives use word-boundary matching — no false positives."""
    assert classify_reply("pass") == "negative"
    assert classify_reply("I'll pass on this") == "negative"
    # "password" should NOT trigger negative
    assert classify_reply("Please reset my password") != "negative"
    # "nonstop" should NOT trigger negative
    assert classify_reply("We run nonstop experiments") != "negative"


def test_classify_reply_neutral():
    assert classify_reply("Thanks for reaching out, I'll think about it") == "neutral"
    assert classify_reply("Can you send me more details?") == "neutral"


def test_classify_reply_empty():
    """Empty reply should be neutral."""
    assert classify_reply("") == "neutral"
    assert classify_reply(None) == "neutral"


def test_sentiment_to_crm_stage_completeness():
    """All sentiment values from classify_reply should have CRM stage mappings."""
    for sentiment in ("rsvp", "positive", "negative", "bounce", "neutral"):
        assert sentiment in SENTIMENT_TO_CRM_STAGE


def test_unsubscribe_keywords_are_subset_of_negatives():
    """UNSUBSCRIBE_KEYWORDS should all appear in the negative keywords list."""
    from reward import _NEGATIVE_KEYWORDS
    for kw in UNSUBSCRIBE_KEYWORDS:
        assert kw in _NEGATIVE_KEYWORDS, f"'{kw}' not in _NEGATIVE_KEYWORDS"

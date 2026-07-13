"""Rate limiting — token bucket + retry with exponential backoff.

Token bucket: 10 tokens, 2/sec refill.
Retry: exponential backoff for 429/503 responses.

Google Workspace API quotas vary by service (~250 units/sec for Gmail,
~300 for Sheets), so this provides a conservative shared bucket.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, TypeVar

from output import RateLimitError

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------

_BUCKET_CAPACITY = 10
_REFILL_RATE = 2.0  # tokens per second

_tokens: float = float(_BUCKET_CAPACITY)
_last_refill: float = time.monotonic()


def _refill() -> None:
    """Refill the token bucket based on elapsed time."""
    global _tokens, _last_refill
    now = time.monotonic()
    elapsed = now - _last_refill
    _tokens = min(_BUCKET_CAPACITY, _tokens + elapsed * _REFILL_RATE)
    _last_refill = now


def acquire() -> None:
    """Acquire a token, blocking until one is available."""
    global _tokens
    while True:
        _refill()
        if _tokens >= 1.0:
            _tokens -= 1.0
            return
        # Wait for enough time to get one token
        wait = (1.0 - _tokens) / _REFILL_RATE
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds
_MAX_DELAY = 16.0  # seconds


def _is_retryable(exc: BaseException) -> bool:
    """Check if an exception indicates a retryable error (429 or 503)."""
    if isinstance(exc, RateLimitError):
        return True
    msg = str(exc).lower()
    return "429" in msg or "503" in msg or "rate limit" in msg


def retry_with_backoff(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call fn with exponential backoff on 429/503 errors.

    Acquires a rate-limit token before each attempt.
    """
    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        acquire()
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == _MAX_RETRIES:
                raise
            delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
            jitter = random.uniform(0, delay * 0.25)
            time.sleep(delay + jitter)
    # Should not reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]

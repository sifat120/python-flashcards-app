"""Cache, leaderboard, and rate limiting.

Two interchangeable backends, picked at import time:
  - Redis backend → used when `REDIS_URL` (or `CACHE_URL`) is set
                    (managed Valkey on Embr).
  - Memory backend → used when no Redis URL is present (local dev / tests).

Implements the same module-level functions in both modes so callers in
`backend/app.py` don't care which backend is active:

  - Leaderboard (sorted set)      — `leaderboard:streaks`
  - Per-user totals               — `total:{username}`
  - Last review day per user      — `last_review:{username}`
  - TTL cache                     — `cache:{key}`
  - Rate limit counters           — caller-supplied key (e.g. `ratelimit:ai:{ip}`)

Embr injects `REDIS_URL` and `CACHE_URL` automatically when
`cache.enabled: true` is set in `embr.yaml`.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import date
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


# ── Backend selection ────────────────────────────────────────────────────────

def _connect_redis():
    """Connect to Redis/Valkey if a URL is configured, else return None.

    A failed connection is non-fatal: we log a warning and fall back to the
    in-memory backend so the app keeps serving traffic.
    """
    url = os.getenv("REDIS_URL") or os.getenv("CACHE_URL")
    if not url:
        logger.info("No REDIS_URL/CACHE_URL set — using in-memory cache")
        return None
    safe_url = url.split("@", 1)[-1] if "@" in url else url
    try:
        import redis  # type: ignore
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        logger.info("Connected to Redis/Valkey at %s", safe_url)
        return client
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "Redis URL %s set but connection failed (%s); falling back to in-memory cache",
            safe_url, e,
        )
        return None


_redis = _connect_redis()


def is_redis_enabled() -> bool:
    """True when the cache is backed by a real Redis/Valkey instance."""
    return _redis is not None

_LEADERBOARD_KEY = "leaderboard:streaks"


# ── In-memory state (used when _redis is None) ───────────────────────────────

_lock = Lock()
_streaks: dict[str, int] = {}
_last_review_day: dict[str, str] = {}
_total_reviews: dict[str, int] = defaultdict(int)
_ttl_store: dict[str, tuple[float, object]] = {}
_rate_counters: dict[str, tuple[float, int]] = {}


# ── Leaderboard ──────────────────────────────────────────────────────────────

def record_review(username: str, review_day_iso: str) -> tuple[int, int]:
    """Update streak + total review count for a user.

    Returns ``(new_streak, total_reviews)`` so callers don't need a follow-up
    round-trip to fetch the total.

    Streak rules:
      - Same day as last review            → unchanged
      - Day after last review              → +1
      - Any longer gap (or no prior)       → reset to 1
    """
    if _redis is not None:
        prev_day_key = f"last_review:{username}"
        total_key = f"total:{username}"
        # Single round-trip: fetch prev_day + current zscore in one pipeline.
        pipe = _redis.pipeline()
        pipe.get(prev_day_key)
        pipe.zscore(_LEADERBOARD_KEY, username)
        prev_day, current_score = pipe.execute()

        if prev_day == review_day_iso:
            new_streak = int(current_score or 1)
        elif prev_day and _day_delta(prev_day, review_day_iso) == 1:
            new_streak = int(current_score or 0) + 1
        else:
            new_streak = 1

        # Single round-trip for the writes + the new total.
        pipe = _redis.pipeline()
        pipe.zadd(_LEADERBOARD_KEY, {username: new_streak})
        pipe.set(prev_day_key, review_day_iso)
        pipe.incr(total_key)
        _, _, new_total = pipe.execute()
        return new_streak, int(new_total)

    with _lock:
        _total_reviews[username] += 1
        last = _last_review_day.get(username)
        if last == review_day_iso:
            new_streak = _streaks.get(username, 1)
        elif last and _day_delta(last, review_day_iso) == 1:
            new_streak = _streaks.get(username, 0) + 1
        else:
            new_streak = 1
        _streaks[username] = new_streak
        _last_review_day[username] = review_day_iso
        return new_streak, _total_reviews[username]


def leaderboard(top_n: int = 10) -> list[tuple[str, int]]:
    """Return top-N (username, streak) pairs, descending by streak."""
    if _redis is not None:
        rows = _redis.zrevrange(_LEADERBOARD_KEY, 0, top_n - 1, withscores=True)
        return [(name, int(score)) for name, score in rows]

    with _lock:
        pairs = sorted(_streaks.items(), key=lambda kv: kv[1], reverse=True)
        return pairs[:top_n]


def get_streak(username: str) -> int:
    if _redis is not None:
        score = _redis.zscore(_LEADERBOARD_KEY, username)
        return int(score) if score is not None else 0
    return _streaks.get(username, 0)


def get_total_reviews(username: str) -> int:
    if _redis is not None:
        v = _redis.get(f"total:{username}")
        return int(v) if v is not None else 0
    return _total_reviews.get(username, 0)


def _day_delta(earlier_iso: str, later_iso: str) -> int:
    a = date.fromisoformat(earlier_iso)
    b = date.fromisoformat(later_iso)
    return (b - a).days


# ── TTL cache (string values) ────────────────────────────────────────────────

def cache_get(key: str) -> Optional[str]:
    if _redis is not None:
        return _redis.get(f"cache:{key}")
    entry = _ttl_store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _ttl_store.pop(key, None)
        return None
    return value  # type: ignore[return-value]


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    if _redis is not None:
        _redis.setex(f"cache:{key}", ttl_seconds, value)
        return
    _ttl_store[key] = (time.time() + ttl_seconds, value)


def cache_delete(key: str) -> None:
    if _redis is not None:
        _redis.delete(f"cache:{key}")
        return
    _ttl_store.pop(key, None)


def cache_delete_many(keys: list[str]) -> None:
    """Delete multiple cache keys in a single Redis round-trip.

    Used by mutation endpoints that need to invalidate several related
    cached responses (e.g. deck list + deck detail + deck cards) without
    paying N round-trip penalties on managed Redis.
    """
    if not keys:
        return
    if _redis is not None:
        _redis.delete(*[f"cache:{k}" for k in keys])
        return
    for k in keys:
        _ttl_store.pop(k, None)


# ── Rate limiting ────────────────────────────────────────────────────────────

def rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    if _redis is not None:
        # Atomic: INCR then EXPIRE on first hit. Pipeline isn't strictly atomic
        # but the EXPIRE is idempotent so the worst case is the window resets
        # one extra second after a race — acceptable for rate limiting.
        pipe = _redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count) <= max_requests

    now = time.time()
    expires_at, count = _rate_counters.get(key, (0, 0))
    if now > expires_at:
        _rate_counters[key] = (now + window_seconds, 1)
        return True
    if count >= max_requests:
        return False
    _rate_counters[key] = (expires_at, count + 1)
    return True

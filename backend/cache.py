"""In-memory cache & leaderboard — swap for managed Redis/Valkey on Embr.

Implements the patterns from 05-flashcards-ai.md using plain Python dicts:

  - Leaderboard (sorted set)      — `leaderboard:streaks`
  - Due-today cache (TTL key)     — `due:{deck_id}:{date}`
  - Rate limit counter            — `ratelimit:ai:{token}`
  - Study session state           — `session:{token}`

To enable the real thing:
  1. Add `cache.enabled: true` to embr.yaml (or just import `redis` — Embr
     auto-detects the dependency and provisions a sidecar Valkey).
  2. Uncomment `redis>=5.0` in requirements.txt.
  3. Replace the dict-based implementations below with the Redis calls
     shown in the comments (ZADD, ZREVRANGE, INCR, EXPIRE, etc.).

Embr injects REDIS_URL and CACHE_URL into the environment automatically.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock
from typing import Optional

# Uncomment when enabling real Redis:
# import redis
# _redis = redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379"), decode_responses=True)


# ── Leaderboard (sorted set equivalent) ──────────────────────────────────────

_lock = Lock()
_streaks: dict[str, int] = {}           # username -> streak
_last_review_day: dict[str, str] = {}   # username -> ISO date of most recent review
_total_reviews: dict[str, int] = defaultdict(int)


def record_review(username: str, review_day_iso: str) -> int:
    """Update streak + total count for a user and return new streak.

    Streak rules:
      - Reviewing today when last review was today → streak unchanged
      - Reviewing today when last review was yesterday → streak + 1
      - Any longer gap → streak resets to 1
    """
    # Redis: ZADD leaderboard:streaks {new_streak} {username}
    #        INCR total:{username}
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
        return new_streak


def leaderboard(top_n: int = 10) -> list[tuple[str, int]]:
    """Return top-N (username, streak) pairs, descending."""
    # Redis: ZREVRANGE leaderboard:streaks 0 {top_n-1} WITHSCORES
    with _lock:
        pairs = sorted(_streaks.items(), key=lambda kv: kv[1], reverse=True)
        return pairs[:top_n]


def get_streak(username: str) -> int:
    return _streaks.get(username, 0)


def get_total_reviews(username: str) -> int:
    return _total_reviews.get(username, 0)


def _day_delta(earlier_iso: str, later_iso: str) -> int:
    from datetime import date
    a = date.fromisoformat(earlier_iso)
    b = date.fromisoformat(later_iso)
    return (b - a).days


# ── TTL cache (for due-today lists, event detail, etc.) ──────────────────────

_ttl_store: dict[str, tuple[float, object]] = {}


def cache_get(key: str) -> Optional[object]:
    # Redis: GET {key}
    entry = _ttl_store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _ttl_store.pop(key, None)
        return None
    return value


def cache_set(key: str, value: object, ttl_seconds: int) -> None:
    # Redis: SETEX {key} {ttl_seconds} {value}
    _ttl_store[key] = (time.time() + ttl_seconds, value)


def cache_delete(key: str) -> None:
    _ttl_store.pop(key, None)


# ── Rate limiting ────────────────────────────────────────────────────────────

_rate_counters: dict[str, tuple[float, int]] = {}  # key -> (window_expires_at, count)


def rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Return True if the request is allowed, False if rate-limited.

    Redis equivalent (atomic):
      local c = redis.call('INCR', KEYS[1])
      if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
      return c <= tonumber(ARGV[2])
    """
    now = time.time()
    expires_at, count = _rate_counters.get(key, (0, 0))
    if now > expires_at:
        _rate_counters[key] = (now + window_seconds, 1)
        return True
    if count >= max_requests:
        return False
    _rate_counters[key] = (expires_at, count + 1)
    return True

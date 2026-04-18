"""SM-2-style spaced repetition scheduler.

Given a card and a rating (very_hard/hard/good/easy), update its ease factor,
interval, and next-review date. A simplified variant of SuperMemo-2 suitable
for the app; swap for Anki's full algorithm if needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from backend.models import CardRecord


VALID_RATINGS = {"very_hard", "hard", "good", "easy"}


def apply_review(card: CardRecord, rating: str) -> CardRecord:
    if rating not in VALID_RATINGS:
        raise ValueError(f"invalid rating: {rating}")

    if rating == "very_hard":
        # Treat as a near-failure — short interval, lower ease factor.
        card.interval_days = 1
        card.ease = max(1.3, card.ease - 0.2)
    elif rating == "hard":
        card.interval_days = max(1, round(card.interval_days * 1.2))
        card.ease = max(1.3, card.ease - 0.15)
    elif rating == "good":
        card.interval_days = 1 if card.interval_days == 0 else max(1, round(card.interval_days * card.ease))
    elif rating == "easy":
        card.interval_days = 3 if card.interval_days == 0 else max(1, round(card.interval_days * card.ease * 1.3))
        card.ease = card.ease + 0.15

    card.next_review = date.today() + timedelta(days=card.interval_days)
    card.last_reviewed = datetime.now(timezone.utc)
    return card

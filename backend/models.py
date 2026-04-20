"""Pydantic models and record classes for the Flashcards app."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel


# ── Request / Response schemas ───────────────────────────────────────────────

class DeckCreate(BaseModel):
    title: str
    description: str = ""
    subject: str = ""


class DeckUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    subject: Optional[str] = None


class DeckOut(BaseModel):
    id: str
    title: str
    description: str
    subject: str
    card_count: int
    due_count: int
    created_at: str


class CardCreate(BaseModel):
    front_text: str
    back_text: str


class CardUpdate(BaseModel):
    front_text: Optional[str] = None
    back_text: Optional[str] = None


class CardOut(BaseModel):
    id: str
    deck_id: str
    front_text: str
    back_text: str
    ease: float
    interval_days: int
    next_review: str
    last_reviewed: Optional[str] = None
    is_due: bool


class ReviewInput(BaseModel):
    rating: str  # "very_hard" | "hard" | "good" | "easy"
    username: str = "anonymous"


class ReviewResult(BaseModel):
    """Bundled response for POST /cards/{id}/review — the updated card,
    the next card to study, refreshed streak/total, and the deck's remaining
    due count, all in a single round-trip."""
    card: CardOut
    next_card: Optional[CardOut] = None
    streak: int
    total_reviews: int
    deck_due_count: int


class LeaderboardEntry(BaseModel):
    username: str
    streak: int
    rank: int


class StatsOut(BaseModel):
    username: str
    streak: int
    total_reviews: int
    cards_due_today: int


# ── Record classes used by both in-memory and DB-backed stores ──────────────

class DeckRecord:
    def __init__(self, title: str, description: str = "", subject: str = ""):
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.description = description
        self.subject = subject
        self.created_at = datetime.now(timezone.utc).isoformat()


class CardRecord:
    def __init__(self, deck_id: str, front_text: str, back_text: str):
        self.id = uuid.uuid4().hex[:10]
        self.deck_id = deck_id
        self.front_text = front_text
        self.back_text = back_text
        self.ease: float = 2.5
        self.interval_days: int = 0
        self.next_review: date = date.today()
        self.last_reviewed: Optional[datetime] = None

    def is_due(self) -> bool:
        return self.next_review <= date.today()

    def to_out(self) -> CardOut:
        return CardOut(
            id=self.id,
            deck_id=self.deck_id,
            front_text=self.front_text,
            back_text=self.back_text,
            ease=self.ease,
            interval_days=self.interval_days,
            next_review=self.next_review.isoformat(),
            last_reviewed=self.last_reviewed.isoformat() if self.last_reviewed else None,
            is_due=self.is_due(),
        )

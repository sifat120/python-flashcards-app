"""Pydantic models and record classes for the Flashcards app."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


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
    image_url: Optional[str] = None


class CardUpdate(BaseModel):
    front_text: Optional[str] = None
    back_text: Optional[str] = None
    image_url: Optional[str] = None


class CardOut(BaseModel):
    id: str
    deck_id: str
    front_text: str
    back_text: str
    image_url: Optional[str] = None
    ease: float
    interval_days: int
    next_review: str
    last_reviewed: Optional[str] = None
    is_due: bool


class ReviewInput(BaseModel):
    rating: str  # "again" | "hard" | "good" | "easy"
    username: str = "anonymous"


class AIGenerateInput(BaseModel):
    passage: str
    count: int = 5


class AIGenerateResult(BaseModel):
    cards: list[dict]
    source: str  # "mock" or "azure_openai"


class ImageUploadResult(BaseModel):
    key: str
    url: str


class LeaderboardEntry(BaseModel):
    username: str
    streak: int
    rank: int


class StatsOut(BaseModel):
    username: str
    streak: int
    total_reviews: int
    cards_due_today: int


# ── In-memory records (swap for ORM models when using Postgres) ──────────────

class DeckRecord:
    def __init__(self, title: str, description: str = "", subject: str = ""):
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.description = description
        self.subject = subject
        self.created_at = datetime.now(timezone.utc).isoformat()


class CardRecord:
    def __init__(
        self,
        deck_id: str,
        front_text: str,
        back_text: str,
        image_url: Optional[str] = None,
    ):
        self.id = uuid.uuid4().hex[:10]
        self.deck_id = deck_id
        self.front_text = front_text
        self.back_text = back_text
        self.image_url = image_url
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
            image_url=self.image_url,
            ease=self.ease,
            interval_days=self.interval_days,
            next_review=self.next_review.isoformat(),
            last_reviewed=self.last_reviewed.isoformat() if self.last_reviewed else None,
            is_due=self.is_due(),
        )

"""Deck / card storage.

Two interchangeable backends, picked at import time:
  - DB backend  → used when `DATABASE_URL` is set (managed Postgres on Embr).
  - Memory backend → used when no `DATABASE_URL` is present (local dev / tests).

Both expose the same `store` singleton with identical methods so the API layer
in `backend/app.py` is backend-agnostic.

The schema in `db/schema.sql` is what Embr runs on deploy
(`psql $DATABASE_URL -f db/schema.sql`). SQLAlchemy's `create_all()` is also
called at startup as a safety net so local Postgres / sqlite work too.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from threading import Lock
from typing import Optional

from backend.models import (
    CardCreate,
    CardRecord,
    CardUpdate,
    DeckCreate,
    DeckRecord,
    DeckUpdate,
)

logger = logging.getLogger(__name__)


# ── Seed data — delete or edit once you have real decks ──────────────────────

_SEED_DECKS = [
    {
        "title": "Biology Basics",
        "description": "Intro-level cell biology and ecology.",
        "subject": "Biology",
        "cards": [
            {
                "front": "What is the basic structural and functional unit of life?",
                "back": "The cell — the smallest unit of life capable of carrying out all life processes.",
                "image": "/seed-diagrams/cell-structure.svg",
            },
            {
                "front": "What is the equation for photosynthesis?",
                "back": "6 CO₂ + 6 H₂O + light energy → C₆H₁₂O₆ + 6 O₂\n\n(Carbon dioxide + water → glucose + oxygen)",
                "image": None,
            },
            {
                "front": "What drives the water cycle?",
                "back": "Solar energy. The sun evaporates water from oceans and lakes; it condenses into clouds and returns as precipitation.",
                "image": "/seed-diagrams/water-cycle.svg",
            },
            {
                "front": "Which organelle is the 'powerhouse of the cell'?",
                "back": "The mitochondrion — it produces ATP (cellular energy) via oxidative phosphorylation.",
                "image": None,
            },
        ],
    },
    {
        "title": "Data Structures",
        "description": "Common data structures every programmer should know.",
        "subject": "Computer Science",
        "cards": [
            {
                "front": "What is a Binary Search Tree (BST)?",
                "back": "A binary tree where each node's left subtree contains only smaller values and the right subtree only larger values.",
                "image": "/seed-diagrams/binary-tree.svg",
            },
            {
                "front": "What is the average-case time complexity of search, insert, and delete in a balanced BST?",
                "back": "O(log n) — because the tree's height grows logarithmically with the number of nodes.\n\nWorst case (unbalanced): O(n).",
                "image": None,
            },
            {
                "front": "What is a Linked List?",
                "back": "A linear data structure where each element (node) stores data plus a pointer to the next node. O(1) insert/delete at the head, O(n) random access.",
                "image": "/seed-diagrams/linked-list.svg",
            },
        ],
    },
    {
        "title": "Chemistry Starter",
        "description": "Foundational chemistry concepts.",
        "subject": "Chemistry",
        "cards": [
            {
                "front": "Describe the Bohr model of the atom.",
                "back": "Electrons orbit a central nucleus (containing protons and neutrons) in fixed energy levels or shells.",
                "image": "/seed-diagrams/atom.svg",
            },
            {
                "front": "What is the atomic number of an element?",
                "back": "The number of protons in an atom's nucleus. It uniquely identifies the element on the periodic table.",
                "image": None,
            },
            {
                "front": "What is the molecular formula for water, and what bonds hold it together?",
                "back": "H₂O — two hydrogen atoms covalently bonded to one oxygen atom. Water molecules also form hydrogen bonds with each other.",
                "image": None,
            },
        ],
    },
]


def _seed_records() -> tuple[list[DeckRecord], list[CardRecord]]:
    """Build DeckRecord / CardRecord instances from the seed data."""
    decks: list[DeckRecord] = []
    cards: list[CardRecord] = []
    for deck_data in _SEED_DECKS:
        deck = DeckRecord(
            title=deck_data["title"],
            description=deck_data["description"],
            subject=deck_data["subject"],
        )
        decks.append(deck)
        for c in deck_data["cards"]:
            cards.append(
                CardRecord(
                    deck_id=deck.id,
                    front_text=c["front"],
                    back_text=c["back"],
                    image_url=c.get("image"),
                )
            )
    return decks, cards


# ── In-memory backend ────────────────────────────────────────────────────────

class _MemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._decks: dict[str, DeckRecord] = {}
        self._cards: dict[str, CardRecord] = {}
        self._cards_by_deck: dict[str, list[str]] = {}
        self._seed()

    def _seed(self) -> None:
        decks, cards = _seed_records()
        for deck in decks:
            self._decks[deck.id] = deck
            self._cards_by_deck[deck.id] = []
        for card in cards:
            self._cards[card.id] = card
            self._cards_by_deck[card.deck_id].append(card.id)
        logger.info("In-memory store seeded with %d decks, %d cards", len(decks), len(cards))

    # Decks
    def create_deck(self, data: DeckCreate) -> DeckRecord:
        deck = DeckRecord(data.title, data.description, data.subject)
        with self._lock:
            self._decks[deck.id] = deck
            self._cards_by_deck[deck.id] = []
        return deck

    def list_decks(self) -> list[DeckRecord]:
        with self._lock:
            return sorted(self._decks.values(), key=lambda d: d.created_at)

    def get_deck(self, deck_id: str) -> Optional[DeckRecord]:
        return self._decks.get(deck_id)

    def update_deck(self, deck_id: str, data: DeckUpdate) -> Optional[DeckRecord]:
        with self._lock:
            deck = self._decks.get(deck_id)
            if deck is None:
                return None
            if data.title is not None:
                deck.title = data.title
            if data.description is not None:
                deck.description = data.description
            if data.subject is not None:
                deck.subject = data.subject
            return deck

    def delete_deck(self, deck_id: str) -> bool:
        with self._lock:
            if deck_id not in self._decks:
                return False
            for cid in self._cards_by_deck.pop(deck_id, []):
                self._cards.pop(cid, None)
            self._decks.pop(deck_id)
            return True

    # Cards
    def create_card(self, deck_id: str, data: CardCreate) -> Optional[CardRecord]:
        with self._lock:
            if deck_id not in self._decks:
                return None
            card = CardRecord(deck_id, data.front_text, data.back_text, data.image_url)
            self._cards[card.id] = card
            self._cards_by_deck[deck_id].append(card.id)
            return card

    def list_cards(self, deck_id: str) -> list[CardRecord]:
        with self._lock:
            return [self._cards[cid] for cid in self._cards_by_deck.get(deck_id, [])]

    def get_card(self, card_id: str) -> Optional[CardRecord]:
        return self._cards.get(card_id)

    def update_card(self, card_id: str, data: CardUpdate) -> Optional[CardRecord]:
        with self._lock:
            card = self._cards.get(card_id)
            if card is None:
                return None
            if data.front_text is not None:
                card.front_text = data.front_text
            if data.back_text is not None:
                card.back_text = data.back_text
            if data.image_url is not None:
                card.image_url = data.image_url or None
            return card

    def delete_card(self, card_id: str) -> bool:
        with self._lock:
            card = self._cards.pop(card_id, None)
            if card is None:
                return False
            self._cards_by_deck[card.deck_id].remove(card_id)
            return True

    def save_card(self, card: CardRecord) -> None:
        # Mutations on the record are already by reference — nothing to flush.
        pass

    def next_due_card(self, deck_id: str) -> Optional[CardRecord]:
        due = [c for c in self.list_cards(deck_id) if c.is_due()]
        if not due:
            return None
        due.sort(key=lambda c: c.next_review)
        return due[0]

    def due_count(self, deck_id: str) -> int:
        return sum(1 for c in self.list_cards(deck_id) if c.is_due())

    def deck_counts(self) -> dict[str, tuple[int, int]]:
        """Return ``{deck_id: (card_count, due_count)}`` for every deck in a
        single pass. Used by the deck-list endpoint to avoid N+1 queries."""
        with self._lock:
            out: dict[str, tuple[int, int]] = {}
            for deck_id, card_ids in self._cards_by_deck.items():
                cards = [self._cards[c] for c in card_ids]
                out[deck_id] = (len(cards), sum(1 for c in cards if c.is_due()))
            return out


# ── Postgres backend (SQLAlchemy) ────────────────────────────────────────────

def _build_db_store():
    """Construct a DB-backed store. Imported lazily to avoid SQLAlchemy
    being a hard dependency for in-memory mode."""
    from sqlalchemy import (
        Column,
        Date,
        DateTime,
        Float,
        ForeignKey,
        Integer,
        String,
        case,
        create_engine,
        func,
        select,
        update as sql_update,
    )
    from sqlalchemy.orm import declarative_base, sessionmaker

    Base = declarative_base()

    class _DeckRow(Base):
        __tablename__ = "decks"
        id = Column(String, primary_key=True)
        title = Column(String, nullable=False)
        description = Column(String, nullable=False, default="")
        subject = Column(String, nullable=False, default="")
        created_at = Column(String, nullable=False)

    class _CardRow(Base):
        __tablename__ = "cards"
        id = Column(String, primary_key=True)
        deck_id = Column(
            String,
            ForeignKey("decks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
        front_text = Column(String, nullable=False)
        back_text = Column(String, nullable=False)
        image_url = Column(String, nullable=True)
        ease = Column(Float, nullable=False, default=2.5)
        interval_days = Column(Integer, nullable=False, default=0)
        next_review = Column(Date, nullable=False)
        last_reviewed = Column(DateTime(timezone=True), nullable=True)

    db_url = os.environ["DATABASE_URL"]
    # SQLAlchemy expects "postgresql://"; Heroku/some platforms emit "postgres://".
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://") :]
    # SQLAlchemy's default driver for `postgresql://` is psycopg2, but we ship
    # psycopg3 in requirements.txt. Force the psycopg3 dialect explicitly.
    if db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg://" + db_url[len("postgresql://") :]

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        future=True,
    )
    # SQLite ignores foreign keys unless you ask it to. Turn them on so local
    # dev catches the same FK violations that Postgres would on Embr.
    if engine.dialect.name == "sqlite":
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def _to_deck(row: _DeckRow) -> DeckRecord:
        d = DeckRecord.__new__(DeckRecord)
        d.id = row.id
        d.title = row.title
        d.description = row.description or ""
        d.subject = row.subject or ""
        d.created_at = row.created_at
        return d

    def _to_card(row: _CardRow) -> CardRecord:
        c = CardRecord.__new__(CardRecord)
        c.id = row.id
        c.deck_id = row.deck_id
        c.front_text = row.front_text
        c.back_text = row.back_text
        c.image_url = row.image_url
        c.ease = float(row.ease)
        c.interval_days = int(row.interval_days)
        c.next_review = row.next_review
        c.last_reviewed = row.last_reviewed
        return c

    class _DBStore:
        def __init__(self) -> None:
            self._maybe_seed()

        def _maybe_seed(self) -> None:
            with Session.begin() as s:
                if s.execute(select(_DeckRow.id).limit(1)).first() is not None:
                    logger.info("DB store: existing data found, skipping seed")
                    return
                decks, cards = _seed_records()
                for d in decks:
                    s.add(_DeckRow(
                        id=d.id, title=d.title,
                        description=d.description, subject=d.subject,
                        created_at=d.created_at,
                    ))
                # Flush decks first so the FK target rows exist before the
                # cards INSERT — Postgres enforces FKs row-by-row, so a single
                # combined flush would fail with cards_deck_id_fkey violations.
                s.flush()
                for c in cards:
                    s.add(_CardRow(
                        id=c.id, deck_id=c.deck_id,
                        front_text=c.front_text, back_text=c.back_text,
                        image_url=c.image_url,
                        ease=c.ease, interval_days=c.interval_days,
                        next_review=c.next_review,
                        last_reviewed=c.last_reviewed,
                    ))
                logger.info("DB store seeded with %d decks, %d cards", len(decks), len(cards))

        # Decks
        def create_deck(self, data: DeckCreate) -> DeckRecord:
            deck = DeckRecord(data.title, data.description, data.subject)
            with Session.begin() as s:
                s.add(_DeckRow(
                    id=deck.id, title=deck.title,
                    description=deck.description, subject=deck.subject,
                    created_at=deck.created_at,
                ))
            return deck

        def list_decks(self) -> list[DeckRecord]:
            with Session() as s:
                rows = s.execute(
                    select(_DeckRow).order_by(_DeckRow.created_at)
                ).scalars().all()
                return [_to_deck(r) for r in rows]

        def get_deck(self, deck_id: str) -> Optional[DeckRecord]:
            with Session() as s:
                row = s.get(_DeckRow, deck_id)
                return _to_deck(row) if row else None

        def update_deck(self, deck_id: str, data: DeckUpdate) -> Optional[DeckRecord]:
            with Session.begin() as s:
                row = s.get(_DeckRow, deck_id)
                if row is None:
                    return None
                if data.title is not None:
                    row.title = data.title
                if data.description is not None:
                    row.description = data.description
                if data.subject is not None:
                    row.subject = data.subject
                s.flush()
                return _to_deck(row)

        def delete_deck(self, deck_id: str) -> bool:
            with Session.begin() as s:
                row = s.get(_DeckRow, deck_id)
                if row is None:
                    return False
                s.delete(row)
                return True

        # Cards
        def create_card(self, deck_id: str, data: CardCreate) -> Optional[CardRecord]:
            with Session.begin() as s:
                if s.get(_DeckRow, deck_id) is None:
                    return None
                card = CardRecord(deck_id, data.front_text, data.back_text, data.image_url)
                s.add(_CardRow(
                    id=card.id, deck_id=card.deck_id,
                    front_text=card.front_text, back_text=card.back_text,
                    image_url=card.image_url,
                    ease=card.ease, interval_days=card.interval_days,
                    next_review=card.next_review,
                    last_reviewed=card.last_reviewed,
                ))
                return card

        def list_cards(self, deck_id: str) -> list[CardRecord]:
            with Session() as s:
                rows = s.execute(
                    select(_CardRow).where(_CardRow.deck_id == deck_id)
                ).scalars().all()
                return [_to_card(r) for r in rows]

        def get_card(self, card_id: str) -> Optional[CardRecord]:
            with Session() as s:
                row = s.get(_CardRow, card_id)
                return _to_card(row) if row else None

        def update_card(self, card_id: str, data: CardUpdate) -> Optional[CardRecord]:
            with Session.begin() as s:
                row = s.get(_CardRow, card_id)
                if row is None:
                    return None
                if data.front_text is not None:
                    row.front_text = data.front_text
                if data.back_text is not None:
                    row.back_text = data.back_text
                if data.image_url is not None:
                    row.image_url = data.image_url or None
                s.flush()
                return _to_card(row)

        def delete_card(self, card_id: str) -> bool:
            with Session.begin() as s:
                row = s.get(_CardRow, card_id)
                if row is None:
                    return False
                s.delete(row)
                return True

        def save_card(self, card: CardRecord) -> None:
            """Persist mutations made to a CardRecord (e.g. by the SM-2 scheduler).

            Single UPDATE round-trip — we already have the full record in memory
            from the calling handler, so there's no need to re-SELECT it first.
            """
            with Session.begin() as s:
                s.execute(
                    sql_update(_CardRow)
                    .where(_CardRow.id == card.id)
                    .values(
                        ease=card.ease,
                        interval_days=card.interval_days,
                        next_review=card.next_review,
                        last_reviewed=card.last_reviewed,
                    )
                )

        def next_due_card(self, deck_id: str) -> Optional[CardRecord]:
            today = date.today()
            with Session() as s:
                row = s.execute(
                    select(_CardRow)
                    .where(_CardRow.deck_id == deck_id, _CardRow.next_review <= today)
                    .order_by(_CardRow.next_review)
                    .limit(1)
                ).scalar_one_or_none()
                return _to_card(row) if row else None

        def due_count(self, deck_id: str) -> int:
            today = date.today()
            with Session() as s:
                return int(s.execute(
                    select(func.count(_CardRow.id)).where(
                        _CardRow.deck_id == deck_id,
                        _CardRow.next_review <= today,
                    )
                ).scalar_one() or 0)

        def deck_counts(self) -> dict[str, tuple[int, int]]:
            """Single-query batch fetch of (card_count, due_count) per deck.
            Avoids the N+1 round-trips you'd get from calling list_cards() and
            due_count() once per deck in /api/decks."""
            today = date.today()
            with Session() as s:
                rows = s.execute(
                    select(
                        _CardRow.deck_id,
                        func.count(_CardRow.id),
                        func.sum(
                            case((_CardRow.next_review <= today, 1), else_=0)
                        ),
                    ).group_by(_CardRow.deck_id)
                ).all()
            return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}

    return _DBStore()


# ── Backend selection ────────────────────────────────────────────────────────

def _make_store():
    """Pick the storage backend based on the environment.

    On Embr `DATABASE_URL` is auto-injected when `database.enabled: true` in
    `embr.yaml`, so the DB backend is the default in production. Locally, with
    no `DATABASE_URL`, the in-memory backend kicks in so the app runs without
    any setup. If DB init fails for any reason we log and fall back to memory
    rather than crashing the app — this preserves uptime at the cost of
    losing persistence for that boot.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.info("No DATABASE_URL set — using in-memory store")
        return _MemoryStore()

    # Mask credentials before logging the URL.
    safe_url = db_url.split("@", 1)[-1] if "@" in db_url else db_url
    try:
        store = _build_db_store()
        logger.info("Using SQL store at %s", safe_url)
        return store
    except Exception as e:  # pragma: no cover — defensive
        logger.exception(
            "DB init failed for %s — falling back to in-memory store: %s", safe_url, e
        )
        return _MemoryStore()


store = _make_store()

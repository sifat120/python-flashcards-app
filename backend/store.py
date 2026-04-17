"""In-memory deck / card storage with pre-seeded example content.

The seed content gives new users three working example decks so they
can see the study flow, AI generation, and diagram support in action.

Drop-in replacement target: swap this module for a DB-backed version
(e.g. SQLAlchemy + managed Postgres on Embr) without changing the API layer.
When `database.enabled: true` is set in embr.yaml, `DATABASE_URL` will be
injected as an environment variable — detect it here and switch backends.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.models import (
    CardCreate,
    CardRecord,
    CardUpdate,
    DeckCreate,
    DeckRecord,
    DeckUpdate,
)


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


# ── Store ────────────────────────────────────────────────────────────────────

class Store:
    def __init__(self) -> None:
        self._decks: dict[str, DeckRecord] = {}
        self._cards: dict[str, CardRecord] = {}
        self._cards_by_deck: dict[str, list[str]] = {}
        self._seed()

    def _seed(self) -> None:
        for deck_data in _SEED_DECKS:
            deck = DeckRecord(
                title=deck_data["title"],
                description=deck_data["description"],
                subject=deck_data["subject"],
            )
            self._decks[deck.id] = deck
            self._cards_by_deck[deck.id] = []
            for c in deck_data["cards"]:
                card = CardRecord(
                    deck_id=deck.id,
                    front_text=c["front"],
                    back_text=c["back"],
                    image_url=c.get("image"),
                )
                self._cards[card.id] = card
                self._cards_by_deck[deck.id].append(card.id)

    # ── Decks ────────────────────────────────────────────────────────────

    def create_deck(self, data: DeckCreate) -> DeckRecord:
        deck = DeckRecord(data.title, data.description, data.subject)
        self._decks[deck.id] = deck
        self._cards_by_deck[deck.id] = []
        return deck

    def list_decks(self) -> list[DeckRecord]:
        return sorted(self._decks.values(), key=lambda d: d.created_at)

    def get_deck(self, deck_id: str) -> Optional[DeckRecord]:
        return self._decks.get(deck_id)

    def update_deck(self, deck_id: str, data: DeckUpdate) -> Optional[DeckRecord]:
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
        if deck_id not in self._decks:
            return False
        for cid in self._cards_by_deck.pop(deck_id, []):
            self._cards.pop(cid, None)
        self._decks.pop(deck_id)
        return True

    # ── Cards ────────────────────────────────────────────────────────────

    def create_card(self, deck_id: str, data: CardCreate) -> Optional[CardRecord]:
        if deck_id not in self._decks:
            return None
        card = CardRecord(deck_id, data.front_text, data.back_text, data.image_url)
        self._cards[card.id] = card
        self._cards_by_deck[deck_id].append(card.id)
        return card

    def list_cards(self, deck_id: str) -> list[CardRecord]:
        return [self._cards[cid] for cid in self._cards_by_deck.get(deck_id, [])]

    def get_card(self, card_id: str) -> Optional[CardRecord]:
        return self._cards.get(card_id)

    def update_card(self, card_id: str, data: CardUpdate) -> Optional[CardRecord]:
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
        card = self._cards.pop(card_id, None)
        if card is None:
            return False
        self._cards_by_deck[card.deck_id].remove(card_id)
        return True

    def next_due_card(self, deck_id: str) -> Optional[CardRecord]:
        due = [c for c in self.list_cards(deck_id) if c.is_due()]
        if not due:
            return None
        due.sort(key=lambda c: c.next_review)
        return due[0]

    def due_count(self, deck_id: str) -> int:
        return sum(1 for c in self.list_cards(deck_id) if c.is_due())


# Singleton used by the API
store = Store()

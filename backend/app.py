"""FastAPI application — REST API + static frontend for Flashcards.

Minimal surface area: deck/card CRUD, a study flow, a streak leaderboard
and per-user stats. Postgres + Valkey when their URLs are present, in-memory
fallbacks otherwise — same code path either way.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

from backend import cache  # noqa: E402
from backend.models import (  # noqa: E402
    CardCreate,
    CardOut,
    CardUpdate,
    DeckCreate,
    DeckOut,
    DeckUpdate,
    LeaderboardEntry,
    ReviewInput,
    ReviewResult,
    StatsOut,
)
from backend.scheduler import VALID_RATINGS, apply_review  # noqa: E402
from backend.store import store  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Flashcards", version="0.3.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Browser-cache hint for read endpoints. Short TTL keeps things fresh, but
# stale-while-revalidate lets repeat clicks paint instantly while the
# browser revalidates in the background.
_READ_CACHE_HEADER = "public, max-age=10, stale-while-revalidate=60"

logger.info(
    "Flashcards starting — store=%s cache=%s",
    type(store).__name__,
    "redis" if cache.is_redis_enabled() else "memory",
)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "cache_enabled": cache.is_redis_enabled()}


# ── Cache helpers ────────────────────────────────────────────────────────────

_CACHE_TTL_SECONDS = 60
_DECK_LIST_KEY = "decks:list"
def _deck_detail_key(deck_id: str) -> str: return f"decks:detail:{deck_id}"
def _deck_cards_key(deck_id: str) -> str: return f"decks:cards:{deck_id}"


def _invalidate_deck(deck_id: str | None = None) -> None:
    keys = [_DECK_LIST_KEY]
    if deck_id:
        keys.extend([_deck_detail_key(deck_id), _deck_cards_key(deck_id)])
    cache.cache_delete_many(keys)


def _json_with_cache_headers(content, x_cache: str) -> JSONResponse:
    return JSONResponse(
        content=content,
        headers={"X-Cache": x_cache, "Cache-Control": _READ_CACHE_HEADER},
    )


def _cached_json(key: str, builder) -> JSONResponse:
    hit = cache.cache_get(key)
    if hit is not None:
        return _json_with_cache_headers(json.loads(hit), "HIT")
    payload = builder()
    cache.cache_set(key, json.dumps(payload, default=str), _CACHE_TTL_SECONDS)
    return _json_with_cache_headers(payload, "MISS")


def _deck_to_out(deck, card_count: int, due_count: int) -> DeckOut:
    return DeckOut(
        id=deck.id,
        title=deck.title,
        description=deck.description,
        subject=deck.subject,
        card_count=card_count,
        due_count=due_count,
        created_at=deck.created_at,
    )


def _build_deck_list_payload() -> list[dict]:
    decks = store.list_decks()
    counts = store.deck_counts()
    return [
        _deck_to_out(d, *counts.get(d.id, (0, 0))).model_dump(mode="json")
        for d in decks
    ]


# ── Startup pre-warm ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def _prewarm() -> None:
    """Warm the Postgres connection pool and the deck-list cache so the
    first user click is a cache hit, not a cold TLS handshake + GROUP BY."""
    try:
        payload = _build_deck_list_payload()
        cache.cache_set(_DECK_LIST_KEY, json.dumps(payload, default=str), _CACHE_TTL_SECONDS)
        logger.info("Pre-warmed deck-list cache (%d decks)", len(payload))
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("Pre-warm skipped: %s", e)


# ── Decks ────────────────────────────────────────────────────────────────────

@app.get("/api/decks")
async def list_decks():
    return _cached_json(_DECK_LIST_KEY, _build_deck_list_payload)


@app.post("/api/decks", response_model=DeckOut, status_code=201)
async def create_deck(data: DeckCreate):
    deck = store.create_deck(data)
    _invalidate_deck(deck.id)
    logger.info("Created deck id=%s title=%r", deck.id, deck.title)
    return _deck_to_out(deck, 0, 0)


@app.get("/api/decks/{deck_id}")
async def get_deck(deck_id: str):
    def build():
        deck = store.get_deck(deck_id)
        if deck is None:
            raise HTTPException(404, "Deck not found")
        # Single-deck targeted aggregate — avoids the full deck_counts()
        # GROUP BY just to extract one row.
        card_count, due_count = store.deck_counts_for(deck_id)
        return _deck_to_out(deck, card_count, due_count).model_dump(mode="json")
    return _cached_json(_deck_detail_key(deck_id), build)


@app.put("/api/decks/{deck_id}", response_model=DeckOut)
async def update_deck(deck_id: str, data: DeckUpdate):
    deck = store.update_deck(deck_id, data)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)
    return _deck_to_out(deck, *store.deck_counts_for(deck_id))


@app.delete("/api/decks/{deck_id}", status_code=204)
async def delete_deck(deck_id: str):
    if not store.delete_deck(deck_id):
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)


# ── Cards ────────────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/cards")
async def list_cards(deck_id: str):
    def build():
        return [c.to_out().model_dump(mode="json") for c in store.list_cards(deck_id)]
    return _cached_json(_deck_cards_key(deck_id), build)


@app.post("/api/decks/{deck_id}/cards", response_model=CardOut, status_code=201)
async def create_card(deck_id: str, data: CardCreate):
    card = store.create_card(deck_id, data)
    if card is None:
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)
    return card.to_out()


@app.put("/api/cards/{card_id}", response_model=CardOut)
async def update_card(card_id: str, data: CardUpdate):
    card = store.update_card(card_id, data)
    if card is None:
        raise HTTPException(404, "Card not found")
    _invalidate_deck(card.deck_id)
    return card.to_out()


@app.delete("/api/cards/{card_id}", status_code=204)
async def delete_card(card_id: str):
    card = store.get_card(card_id)
    if not store.delete_card(card_id):
        raise HTTPException(404, "Card not found")
    if card is not None:
        _invalidate_deck(card.deck_id)


# ── Study flow ───────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/study/next")
async def next_due(deck_id: str):
    """Return the next due card AND a look-ahead so the client can render
    the second card instantly when the user rates the first one."""
    cards = [c for c in store.list_cards(deck_id) if c.is_due()]
    cards.sort(key=lambda c: c.next_review)
    if not cards:
        return {"card": None, "next_card": None, "message": "No cards due — come back later!"}
    return {
        "card": cards[0].to_out(),
        "next_card": cards[1].to_out() if len(cards) > 1 else None,
    }


@app.post("/api/cards/{card_id}/review", response_model=ReviewResult)
async def review_card(card_id: str, data: ReviewInput):
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(404, "Card not found")
    if data.rating not in VALID_RATINGS:
        raise HTTPException(400, f"rating must be one of {sorted(VALID_RATINGS)}")

    apply_review(card, data.rating)
    store.save_card(card)
    _invalidate_deck(card.deck_id)

    username = data.username.strip() or "anonymous"
    new_streak, total_reviews = cache.record_review(username, date.today().isoformat())

    # Single round-trip for both pieces of post-review state.
    next_card, deck_due = store.next_and_due_count(card.deck_id)
    return ReviewResult(
        card=card.to_out(),
        next_card=next_card.to_out() if next_card else None,
        streak=new_streak,
        total_reviews=total_reviews,
        deck_due_count=deck_due,
    )


# ── Leaderboard & stats ──────────────────────────────────────────────────────

@app.get("/api/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(top: int = Query(10, ge=1, le=100)):
    return [
        LeaderboardEntry(username=name, streak=streak, rank=i + 1)
        for i, (name, streak) in enumerate(cache.leaderboard(top))
    ]


@app.get("/api/stats", response_model=StatsOut)
async def get_stats(username: str = "anonymous"):
    safe_user = username.replace(":", "_")[:64] or "anonymous"
    key = f"stats:{safe_user}:{date.today().isoformat()}"
    hit = cache.cache_get(key)
    if hit is not None:
        return _json_with_cache_headers(json.loads(hit), "HIT")
    payload = StatsOut(
        username=username,
        streak=cache.get_streak(username),
        total_reviews=cache.get_total_reviews(username),
        cards_due_today=sum(d for _, d in store.deck_counts().values()),
    ).model_dump(mode="json")
    cache.cache_set(key, json.dumps(payload, default=str), 15)
    return _json_with_cache_headers(payload, "MISS")


# ── Static frontend ──────────────────────────────────────────────────────────

@app.get("/favicon.svg")
async def favicon():
    return FileResponse(
        STATIC_DIR / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")

"""FastAPI application — REST API + static frontend for Flashcards AI.

Routes are grouped by concern (decks, cards, study, AI, images, leaderboard,
static). Every handler is a small async function that delegates the real work
to the storage / cache / blob / AI modules so this file stays scannable.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

# Configure logging before importing any module that might log at import time
# (store.py, cache.py, blob.py all do backend selection on import).
from backend.logging_config import setup_logging

setup_logging()

from backend import cache  # noqa: E402
from backend.ai_service import generate_cards, is_ai_enabled  # noqa: E402
from backend.blob import UploadError, is_embr_blob_enabled, store as blob_store  # noqa: E402
from backend.models import (  # noqa: E402
    AIGenerateInput,
    AIGenerateResult,
    CardCreate,
    CardOut,
    CardUpdate,
    DeckCreate,
    DeckOut,
    DeckUpdate,
    ImageUploadResult,
    LeaderboardEntry,
    ReviewInput,
    ReviewResult,
    StatsOut,
)
from backend.scheduler import VALID_RATINGS, apply_review  # noqa: E402
from backend.store import store  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Flashcards AI", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SEED_DIR = STATIC_DIR / "seed-diagrams"

logger.info(
    "Flashcards AI starting — store=%s cache=%s blob=%s ai=%s",
    type(store).__name__,
    "redis" if cache.is_redis_enabled() else "memory",
    type(blob_store).__name__,
    "azure_openai" if is_ai_enabled() else "mock",
)


@app.on_event("startup")
async def _prewarm() -> None:
    """Pre-warm the connection pool and the deck-list cache.

    On a cold container start, the very first request would otherwise pay for
    the Postgres TLS handshake plus the cold deck-list query. Doing this once
    at boot means user-facing requests start with a hot pool and a cached
    deck list, so the first click feels instant instead of taking ~500ms.
    """
    try:
        decks = store.list_decks()
        counts = store.deck_counts()
        payload = [
            _deck_to_out(d, *counts.get(d.id, (0, 0))).model_dump(mode="json")
            for d in decks
        ]
        cache.cache_set(_DECK_LIST_KEY, json.dumps(payload, default=str), _CACHE_TTL_SECONDS)
        logger.info("Pre-warmed deck-list cache (%d decks)", len(decks))
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("Pre-warm skipped: %s", e)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ai_enabled": is_ai_enabled(),
        "embr_blob_enabled": is_embr_blob_enabled(),
        "cache_enabled": cache.is_redis_enabled(),
    }


# ── Decks ────────────────────────────────────────────────────────────────────

# JSON-cache keys. We cache the rendered response payloads (not ORM objects)
# so reads avoid Postgres entirely on cache hits. TTL is short to keep bugs
# from latent staleness contained — invalidation is best-effort but explicit
# below.
_CACHE_TTL_SECONDS = 60
_DECK_LIST_KEY = "decks:list"
def _deck_detail_key(deck_id: str) -> str: return f"decks:detail:{deck_id}"
def _deck_cards_key(deck_id: str) -> str: return f"decks:cards:{deck_id}"


def _invalidate_deck(deck_id: str | None = None) -> None:
    """Bust the deck-list cache plus (optionally) per-deck caches.
    Pipelined into a single Redis round-trip so writes stay snappy."""
    keys = [_DECK_LIST_KEY]
    if deck_id:
        keys.extend([_deck_detail_key(deck_id), _deck_cards_key(deck_id)])
    cache.cache_delete_many(keys)


def _cached_json(key: str, builder) -> JSONResponse:
    """Return a JSONResponse from cache if present, otherwise build, store,
    and return. ``builder`` is a zero-arg callable returning JSON-serialisable
    data."""
    hit = cache.cache_get(key)
    if hit is not None:
        return JSONResponse(content=json.loads(hit), headers={"X-Cache": "HIT"})
    payload = builder()
    cache.cache_set(key, json.dumps(payload, default=str), _CACHE_TTL_SECONDS)
    return JSONResponse(content=payload, headers={"X-Cache": "MISS"})


def _deck_to_out(deck, card_count: int | None = None, due_count: int | None = None) -> DeckOut:
    # When counts aren't provided, fall back to per-deck queries (used by
    # single-deck endpoints). The list endpoint passes pre-computed counts
    # from store.deck_counts() to avoid N+1 round-trips on Postgres.
    if card_count is None:
        card_count = len(store.list_cards(deck.id))
    if due_count is None:
        due_count = store.due_count(deck.id)
    return DeckOut(
        id=deck.id,
        title=deck.title,
        description=deck.description,
        subject=deck.subject,
        card_count=card_count,
        due_count=due_count,
        created_at=deck.created_at,
    )


@app.get("/api/decks")
async def list_decks():
    def build():
        decks = store.list_decks()
        counts = store.deck_counts()
        return [
            _deck_to_out(d, *counts.get(d.id, (0, 0))).model_dump(mode="json")
            for d in decks
        ]
    return _cached_json(_DECK_LIST_KEY, build)


@app.post("/api/decks", response_model=DeckOut, status_code=201)
async def create_deck(data: DeckCreate):
    deck = store.create_deck(data)
    _invalidate_deck(deck.id)
    logger.info("Created deck id=%s title=%r", deck.id, deck.title)
    return _deck_to_out(deck)


@app.get("/api/decks/{deck_id}")
async def get_deck(deck_id: str):
    def build():
        deck = store.get_deck(deck_id)
        if deck is None:
            raise HTTPException(404, "Deck not found")
        counts = store.deck_counts().get(deck_id, (0, 0))
        return _deck_to_out(deck, *counts).model_dump(mode="json")
    return _cached_json(_deck_detail_key(deck_id), build)


@app.put("/api/decks/{deck_id}", response_model=DeckOut)
async def update_deck(deck_id: str, data: DeckUpdate):
    deck = store.update_deck(deck_id, data)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)
    logger.info("Updated deck id=%s", deck_id)
    return _deck_to_out(deck)


@app.delete("/api/decks/{deck_id}", status_code=204)
async def delete_deck(deck_id: str):
    if not store.delete_deck(deck_id):
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)
    logger.info("Deleted deck id=%s", deck_id)


# ── Cards ────────────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/cards")
async def list_cards(deck_id: str):
    def build():
        # No separate get_deck() round-trip — list_cards on a missing deck
        # just returns []. Distinguishing "missing deck" from "empty deck"
        # isn't worth a full DB query for every page-load.
        return [c.to_out().model_dump(mode="json") for c in store.list_cards(deck_id)]
    return _cached_json(_deck_cards_key(deck_id), build)


@app.post("/api/decks/{deck_id}/cards", response_model=CardOut, status_code=201)
async def create_card(deck_id: str, data: CardCreate):
    card = store.create_card(deck_id, data)
    if card is None:
        raise HTTPException(404, "Deck not found")
    _invalidate_deck(deck_id)
    logger.info("Created card id=%s deck=%s", card.id, deck_id)
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
    logger.info("Deleted card id=%s", card_id)


# ── Study flow ───────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/study/next")
async def next_due(deck_id: str):
    # No separate get_deck() round-trip — next_due_card returning None already
    # covers both "deck missing" and "no cards due" with the same UX.
    card = store.next_due_card(deck_id)
    if card is None:
        return {"card": None, "message": "No cards due — come back later!"}
    return {"card": card.to_out()}


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
    today_iso = date.today().isoformat()
    new_streak, total_reviews = cache.record_review(username, today_iso)

    # Bundle everything the client needs (next card to study, refreshed
    # streak/total, due-count for this deck) so the UI doesn't fire 3
    # follow-up requests after every rating click. We use a single-deck
    # due_count() (one COUNT query) instead of deck_counts() (GROUP BY across
    # every deck) because we only need this one deck's number.
    next_card = store.next_due_card(card.deck_id)
    deck_due = store.due_count(card.deck_id)

    logger.debug(
        "Review applied card=%s rating=%s user=%s streak=%d next=%s",
        card.id, data.rating, username, new_streak, card.next_review,
    )
    return ReviewResult(
        card=card.to_out(),
        next_card=next_card.to_out() if next_card else None,
        streak=new_streak,
        total_reviews=total_reviews,
        deck_due_count=deck_due,
    )


# ── AI generation ────────────────────────────────────────────────────────────

@app.post("/api/decks/{deck_id}/ai-generate", response_model=AIGenerateResult)
async def ai_generate(deck_id: str, data: AIGenerateInput, request: Request):
    if store.get_deck(deck_id) is None:
        raise HTTPException(404, "Deck not found")

    # Rate limit: 20 AI generations per hour per client IP.
    ip = request.client.host if request.client else "unknown"
    if not cache.rate_limit(f"ratelimit:ai:{ip}", max_requests=20, window_seconds=3600):
        logger.warning("AI rate limit hit for ip=%s deck=%s", ip, deck_id)
        raise HTTPException(429, "AI generation rate limit exceeded (20/hour).")

    if not data.passage.strip():
        raise HTTPException(400, "passage is required")

    cards, source = await generate_cards(data.passage, data.count)
    for c in cards:
        store.create_card(
            deck_id,
            CardCreate(front_text=c["front_text"], back_text=c["back_text"]),
        )
    _invalidate_deck(deck_id)
    logger.info(
        "AI generation deck=%s source=%s count=%d passage_chars=%d",
        deck_id, source, len(cards), len(data.passage),
    )
    return AIGenerateResult(cards=cards, source=source)


# ── Image uploads ────────────────────────────────────────────────────────────

@app.post("/api/images", response_model=ImageUploadResult, status_code=201)
async def upload_image(file: UploadFile = File(...), request: Request = None):
    ip = request.client.host if request and request.client else "unknown"
    if not cache.rate_limit(f"ratelimit:upload:{ip}", max_requests=30, window_seconds=3600):
        logger.warning("Upload rate limit hit for ip=%s", ip)
        raise HTTPException(429, "Upload rate limit exceeded (30/hour).")

    data = await file.read()
    try:
        key, url = blob_store.save(file.filename or "upload.bin", data)
    except UploadError as e:
        logger.warning("Image upload rejected: %s (filename=%s, bytes=%d)", e, file.filename, len(data))
        raise HTTPException(400, str(e))

    logger.info("Image uploaded key=%s bytes=%d backend=%s", key, len(data), type(blob_store).__name__)
    return ImageUploadResult(key=key, url=url)


@app.get("/uploads/{key}")
async def serve_upload(key: str):
    # Only the local backend serves uploads through the app. On Embr, blob
    # URLs are /_embr/blob/{key} and served by the platform proxy directly.
    path_for = getattr(blob_store, "path_for", None)
    if path_for is None:
        raise HTTPException(404, "Not found")
    path = path_for(key)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@app.get("/seed-diagrams/{name}")
async def serve_seed_diagram(name: str):
    # Seed diagrams shipped with the app — show customers what's possible.
    # Replace with your own content or let users upload via /api/images.
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(400, "invalid name")
    path = SEED_DIR / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/svg+xml" if name.endswith(".svg") else None)


# ── Leaderboard & stats ──────────────────────────────────────────────────────

@app.get("/api/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(top: int = Query(10, ge=1, le=100)):
    return [
        LeaderboardEntry(username=name, streak=streak, rank=i + 1)
        for i, (name, streak) in enumerate(cache.leaderboard(top))
    ]


@app.get("/api/stats", response_model=StatsOut)
async def get_stats(username: str = "anonymous"):
    # Short-lived per-user cache. Stats fire on every page load and after
    # every review, so even a 15s TTL collapses bursts of requests into
    # one DB query without making the leaderboard feel laggy.
    safe_user = username.replace(":", "_")[:64] or "anonymous"
    key = f"stats:{safe_user}:{date.today().isoformat()}"
    def build():
        total_due = sum(d for _, d in store.deck_counts().values())
        return StatsOut(
            username=username,
            streak=cache.get_streak(username),
            total_reviews=cache.get_total_reviews(username),
            cards_due_today=total_due,
        ).model_dump(mode="json")
    hit = cache.cache_get(key)
    if hit is not None:
        return JSONResponse(content=json.loads(hit), headers={"X-Cache": "HIT"})
    payload = build()
    cache.cache_set(key, json.dumps(payload, default=str), 15)
    return JSONResponse(content=payload, headers={"X-Cache": "MISS"})


# ── Static frontend ──────────────────────────────────────────────────────────

@app.get("/favicon.svg")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")

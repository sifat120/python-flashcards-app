"""FastAPI application — REST API + static frontend for Flashcards AI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend import cache
from backend.ai_service import generate_cards, is_ai_enabled
from backend.blob import UploadError, is_embr_blob_enabled, store as blob_store
from backend.models import (
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
    StatsOut,
)
from backend.scheduler import VALID_RATINGS, apply_review
from backend.store import store

app = FastAPI(title="Flashcards AI", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SEED_DIR = STATIC_DIR / "seed-diagrams"


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ai_enabled": is_ai_enabled(),
        "embr_blob_enabled": is_embr_blob_enabled(),
    }


# ── Decks ────────────────────────────────────────────────────────────────────

def _deck_to_out(deck) -> DeckOut:
    return DeckOut(
        id=deck.id,
        title=deck.title,
        description=deck.description,
        subject=deck.subject,
        card_count=len(store.list_cards(deck.id)),
        due_count=store.due_count(deck.id),
        created_at=deck.created_at,
    )


@app.get("/api/decks", response_model=list[DeckOut])
async def list_decks():
    return [_deck_to_out(d) for d in store.list_decks()]


@app.post("/api/decks", response_model=DeckOut, status_code=201)
async def create_deck(data: DeckCreate):
    return _deck_to_out(store.create_deck(data))


@app.get("/api/decks/{deck_id}", response_model=DeckOut)
async def get_deck(deck_id: str):
    deck = store.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    return _deck_to_out(deck)


@app.put("/api/decks/{deck_id}", response_model=DeckOut)
async def update_deck(deck_id: str, data: DeckUpdate):
    deck = store.update_deck(deck_id, data)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    return _deck_to_out(deck)


@app.delete("/api/decks/{deck_id}", status_code=204)
async def delete_deck(deck_id: str):
    if not store.delete_deck(deck_id):
        raise HTTPException(404, "Deck not found")


# ── Cards ────────────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/cards", response_model=list[CardOut])
async def list_cards(deck_id: str):
    if store.get_deck(deck_id) is None:
        raise HTTPException(404, "Deck not found")
    return [c.to_out() for c in store.list_cards(deck_id)]


@app.post("/api/decks/{deck_id}/cards", response_model=CardOut, status_code=201)
async def create_card(deck_id: str, data: CardCreate):
    card = store.create_card(deck_id, data)
    if card is None:
        raise HTTPException(404, "Deck not found")
    cache.cache_delete(f"due:{deck_id}:{date.today().isoformat()}")
    return card.to_out()


@app.put("/api/cards/{card_id}", response_model=CardOut)
async def update_card(card_id: str, data: CardUpdate):
    card = store.update_card(card_id, data)
    if card is None:
        raise HTTPException(404, "Card not found")
    return card.to_out()


@app.delete("/api/cards/{card_id}", status_code=204)
async def delete_card(card_id: str):
    if not store.delete_card(card_id):
        raise HTTPException(404, "Card not found")


# ── Study flow ───────────────────────────────────────────────────────────────

@app.get("/api/decks/{deck_id}/study/next")
async def next_due(deck_id: str):
    if store.get_deck(deck_id) is None:
        raise HTTPException(404, "Deck not found")
    card = store.next_due_card(deck_id)
    if card is None:
        return {"card": None, "message": "No cards due — come back later!"}
    return {"card": card.to_out()}


@app.post("/api/cards/{card_id}/review", response_model=CardOut)
async def review_card(card_id: str, data: ReviewInput):
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(404, "Card not found")
    if data.rating not in VALID_RATINGS:
        raise HTTPException(400, f"rating must be one of {sorted(VALID_RATINGS)}")
    apply_review(card, data.rating)
    store.save_card(card)
    cache.record_review(data.username.strip() or "anonymous", date.today().isoformat())
    cache.cache_delete(f"due:{card.deck_id}:{date.today().isoformat()}")
    return card.to_out()


# ── AI generation ────────────────────────────────────────────────────────────

@app.post("/api/decks/{deck_id}/ai-generate", response_model=AIGenerateResult)
async def ai_generate(deck_id: str, data: AIGenerateInput, request: Request):
    if store.get_deck(deck_id) is None:
        raise HTTPException(404, "Deck not found")

    # Rate limit: 20 AI generations per hour per client IP.
    ip = request.client.host if request.client else "unknown"
    if not cache.rate_limit(f"ratelimit:ai:{ip}", max_requests=20, window_seconds=3600):
        raise HTTPException(429, "AI generation rate limit exceeded (20/hour).")

    if not data.passage.strip():
        raise HTTPException(400, "passage is required")

    cards, source = await generate_cards(data.passage, data.count)
    for c in cards:
        store.create_card(
            deck_id,
            CardCreate(front_text=c["front_text"], back_text=c["back_text"]),
        )
    return AIGenerateResult(cards=cards, source=source)


# ── Image uploads ────────────────────────────────────────────────────────────

@app.post("/api/images", response_model=ImageUploadResult, status_code=201)
async def upload_image(file: UploadFile = File(...), request: Request = None):
    ip = request.client.host if request and request.client else "unknown"
    if not cache.rate_limit(f"ratelimit:upload:{ip}", max_requests=30, window_seconds=3600):
        raise HTTPException(429, "Upload rate limit exceeded (30/hour).")

    data = await file.read()
    try:
        key, url = blob_store.save(file.filename or "upload.bin", data)
    except UploadError as e:
        raise HTTPException(400, str(e))
    return ImageUploadResult(key=key, url=url)


@app.get("/uploads/{key}")
async def serve_upload(key: str):
    path = blob_store.path_for(key)
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
    total_due = sum(store.due_count(d.id) for d in store.list_decks())
    return StatsOut(
        username=username,
        streak=cache.get_streak(username),
        total_reviews=cache.get_total_reviews(username),
        cards_due_today=total_due,
    )


# ── Static frontend ──────────────────────────────────────────────────────────

@app.get("/favicon.svg")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")

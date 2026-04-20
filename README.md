# Flashcards — Spaced Repetition

A minimal flashcards app ready to deploy on [Embr](https://portal.embr.azure). FastAPI backend serves a vanilla HTML/JS frontend on a single port — no frontend build step, no AI calls, no file uploads, just decks, cards, and a fast study flow.

Comes pre-loaded with three example decks (Biology, Data Structures, Chemistry) so the UI looks alive on first load.

## Deploy to Embr

The included [`embr.yaml`](embr.yaml) provisions managed Postgres and a Valkey (Redis-compatible) cache, runs [`db/schema.sql`](db/schema.sql) against the database on every deploy, and configures health checks:

```yaml
# embr.yaml
version: 1
platform: python
platformVersion: "3.14"
run:
  port: 8080
  startCommand: "python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080"
database:
  enabled: true
cache:
  enabled: true
healthCheck:
  path: /api/health
  expectedStatusCode: 200
```

Connect this repo in the [Embr Portal](https://portal.embr.azure) and Embr will:

1. Install dependencies from `requirements.txt` (FastAPI, uvicorn, SQLAlchemy, psycopg, redis).
2. Provision Postgres, run `db/schema.sql`, and inject `DATABASE_URL`.
3. Provision Valkey and inject `REDIS_URL` / `CACHE_URL`.
4. Start the server and health-check `GET /api/health`.

## Run locally

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8008
```

Open http://localhost:8008

With no env vars set, the app uses an in-memory store and an in-memory cache so it just works. Set `DATABASE_URL` and/or `REDIS_URL` to connect to real services:

```bash
# bash / zsh
export DATABASE_URL="postgresql://user:pass@localhost:5432/flashcards"
export REDIS_URL="redis://127.0.0.1:6379"
```

```powershell
# PowerShell
$env:DATABASE_URL = "postgresql://user:pass@localhost:5432/flashcards"
$env:REDIS_URL = "redis://127.0.0.1:6379"
```

## Features

- **Decks & cards** — create, edit, delete
- **Study mode** — SM-2-style spaced repetition (`very_hard` / `hard` / `good` / `easy`)
- **Streak leaderboard** — daily-streak tracking via the cache layer

## How the app uses managed services

Each backing service has two interchangeable backends. The right one is selected at startup based on whether the corresponding environment variable is present, so the same code runs locally (in-memory) and on Embr (managed).

| Component | Local (no env var) | Embr (env var injected by `embr.yaml`)             | Code |
|-----------|--------------------|-----------------------------------------------------|------|
| Database  | In-memory dict     | Managed Postgres → `DATABASE_URL`                   | [`backend/store.py`](backend/store.py) |
| Cache     | In-memory dict     | Managed Valkey/Redis → `REDIS_URL` / `CACHE_URL`    | [`backend/cache.py`](backend/cache.py) |

### Database

`db/schema.sql` defines the `decks` and `cards` tables and is run by Embr on every deploy. `backend/store.py` uses SQLAlchemy when `DATABASE_URL` is set; otherwise it falls back to an in-memory store with the same interface. Seed data is inserted on first deploy when the tables are empty.

### Cache

`backend/cache.py` connects to `REDIS_URL` (or `CACHE_URL`) at import time. If the connection succeeds, the streak leaderboard and response cache use Redis primitives (`ZADD`, `ZREVRANGE`, `INCR`, `SETEX`). If no URL is set or the connection fails, the same operations run against in-memory dicts.

The cache layer powers:

- **Streak leaderboard** — `ZADD`/`ZREVRANGE` on `leaderboard:streaks`.
- **Per-user totals + last-review-day** — used by the streak rule.
- **Response caching** — `GET /api/decks`, `GET /api/decks/{id}`, `GET /api/decks/{id}/cards`, and `GET /api/stats` are cached as JSON (60s TTL for deck endpoints, 15s for stats). Every mutation busts related keys via a single pipelined Redis `DEL`. Responses include an `X-Cache: HIT|MISS` header.

## Why it feels snappy

- **Homepage load** — 3 parallel requests, all served from the response cache after the first hit.
- **Deck detail** — 2 parallel requests, both cached.
- **Study rating** — a **single** `POST /api/cards/{id}/review` returns the updated card, the next card to study, the new streak, the user's total reviews, and the deck's remaining due count. No follow-up requests needed.
- **Deck listing avoids N+1** — card counts and due counts are fetched in one `GROUP BY` query.
- **Cache invalidation is pipelined** — writes bust deck list + per-deck detail + per-deck cards in a single Redis `DEL`.

No AI calls, no file uploads — every button click is either an in-memory operation, a cache hit, or a single short Postgres query.

## Environment variables

| Variable | When you get it |
|---|---|
| `PORT` | Always (matches `run.port`) |
| `DATABASE_URL` | When `database.enabled: true` (managed Postgres) |
| `REDIS_URL` / `CACHE_URL` | When `cache.enabled: true` (managed Valkey) |
| `EMBR_ENVIRONMENT`, `EMBR_PROJECT_ID` | Always |

## Project layout

```
flashcard-app/
├── embr.yaml               # Embr deployment config
├── requirements.txt
├── db/
│   └── schema.sql          # Postgres schema — run by Embr on every deploy
├── backend/
│   ├── app.py              # FastAPI routes
│   ├── models.py           # Pydantic schemas + record classes
│   ├── store.py            # Decks/cards store (SQLAlchemy + in-memory fallback) + seed data
│   ├── scheduler.py        # SM-2 review algorithm
│   └── cache.py            # Leaderboard + TTL cache (Redis + in-memory fallback)
└── static/
    ├── index.html          # Single-page frontend (vanilla JS)
    └── favicon.svg
```

## Customizing

- **Replace the seed decks** by editing `_SEED_DECKS` in `backend/store.py`.
- **Tune cache TTLs** in `backend/app.py` (`_CACHE_TTL_SECONDS` for deck endpoints, the `15` in `get_stats` for stats).

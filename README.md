# Flashcards AI — Spaced Repetition with Diagrams

A flashcards app ready to deploy on [Embr](https://portal.embr.azure). FastAPI backend serves a vanilla HTML/JS frontend on a single port — no frontend build step.

Comes pre-loaded with three example decks (Biology, Data Structures, Chemistry) so you can see the study flow, AI-generation, and diagram support working out of the box.

## Deploy to Embr

The included [`embr.yaml`](embr.yaml) provisions managed Postgres and a Valkey (Redis-compatible) cache, runs [`db/schema.sql`](db/schema.sql) against the database on every deploy, and configures health checks:

```yaml
# embr.yaml
version: 1
platform: python
platformVersion: "3.14"
run:
  port: 8080
database:
  enabled: true
cache:
  enabled: true
healthCheck:
  path: /api/health
  expectedStatusCode: 200
```

Connect this repo in the [Embr Portal](https://portal.embr.azure) and Embr will:

1. Install dependencies from `requirements.txt` (FastAPI, SQLAlchemy, psycopg, redis).
2. Provision Postgres, run `db/schema.sql`, and inject `DATABASE_URL`.
3. Provision Valkey and inject `REDIS_URL` / `CACHE_URL`.
4. Auto-provision blob storage and inject `EMBR_BLOB_KEY` (no config needed).
5. Start the server and begin health-checking `GET /api/health` every minute.

The app picks up each managed resource automatically — see [How the app uses managed services](#how-the-app-uses-managed-services) below.

## Run Locally

```bash
pip install -r requirements.txt
gunicorn --bind 0.0.0.0:8008 --reload application:app
```

Open http://localhost:8008

## Features

- **Decks & cards** — create, edit, delete; each card has front/back text plus an optional diagram
- **Study mode** — SM-2-style spaced repetition (`very_hard` / `hard` / `good` / `easy`)
- **AI card generation** — paste a passage of study notes; mock by default, real Azure OpenAI when configured
- **Diagrams** — pre-bundled SVG diagrams (cell, water cycle, atom, BST, linked list) so customers see real content instantly
- **Custom image uploads** — drop in your own PNG/JPG/SVG per card
- **Streak leaderboard** — daily-streak tracking via the cache layer

## How the app uses managed services

Each backing service has two interchangeable backends. The right one is selected at startup based on whether the corresponding environment variable is present, so the same code runs locally (in-memory) and on Embr (managed).

| Component    | Local (no env var)    | Embr (env var injected by `embr.yaml`)                      | Code |
|--------------|-----------------------|--------------------------------------------------------------|------|
| Database     | In-memory dict        | Managed Postgres → `DATABASE_URL`                            | [`backend/store.py`](backend/store.py) |
| Cache        | In-memory dict        | Managed Valkey/Redis → `REDIS_URL` / `CACHE_URL`             | [`backend/cache.py`](backend/cache.py) |
| Blob storage | Local `uploads/` dir  | Auto-provisioned blob → `EMBR_BLOB_KEY` + `EMBR_DOMAIN`, served at `/_embr/blob/{key}` | [`backend/blob.py`](backend/blob.py) |
| AI           | Mock card generator   | Azure OpenAI → `AZURE_OPENAI_*` set via CLI                  | [`backend/ai_service.py`](backend/ai_service.py) |

### Database

`db/schema.sql` defines the `decks` and `cards` tables and is run by Embr on every deploy (`psql $DATABASE_URL -f db/schema.sql`). `backend/store.py` uses SQLAlchemy when `DATABASE_URL` is set; otherwise it falls back to an in-memory store with the same interface. Seed data is inserted on first deploy when the tables are empty.

To run with Postgres locally:

```bash
$env:DATABASE_URL = "postgresql://user:pass@localhost:5432/flashcards"   # PowerShell
# or
export DATABASE_URL="postgresql://user:pass@localhost:5432/flashcards"   # bash
```

`SQLAlchemy.create_all()` runs at startup as a safety net so SQLite (`sqlite:///flashcards.db`) also works for quick local experiments.

### Cache

`backend/cache.py` connects to `REDIS_URL` (or `CACHE_URL`) at import time. If the connection succeeds, leaderboards / rate limits / TTL caches use Redis primitives (`ZADD`, `ZREVRANGE`, `INCR`, `EXPIRE`, `SETEX`). If no URL is set or the connection fails, the same operations run against in-memory dicts.

The cache layer is responsible for:

- **Streak leaderboard** — `ZADD`/`ZREVRANGE` on `leaderboard:streaks`.
- **Per-user totals + last-review-day** — used by the streak rule.
- **Rate limiting** — `INCR` + `EXPIRE` buckets for AI generation (20/hr) and image uploads (30/hr) per client IP.
- **Response caching** — `GET /api/decks`, `GET /api/decks/{id}`, `GET /api/decks/{id}/cards`, and `GET /api/stats` are cached as JSON (60s TTL for deck endpoints, 15s for stats). Every mutation (deck/card create/update/delete, review, AI-generate) busts related keys via a single pipelined Redis `DEL`. Responses include an `X-Cache: HIT|MISS` header so you can verify cache behaviour in your browser's network tab.

### Blob storage

Embr auto-provisions blob storage for every environment — no `embr.yaml` toggle needed. `backend/blob.py` selects its backend at import time:

- **On Embr** (`EMBR_BLOB_KEY` set): uploads `PUT https://<env-domain>/_embr/blob/images/{uuid}.{ext}` with `Authorization: Bearer ${EMBR_BLOB_KEY}` and returns the same-domain path `/_embr/blob/{key}`. Reads are public and served directly by the Embr platform proxy (no app round-trip).
- **Locally** (no `EMBR_BLOB_KEY`): uploads land in `backend/uploads/` and the app serves them at `/uploads/{key}`.

Both paths enforce a 5 MB cap and an allow-list of `png/jpg/jpeg/webp/svg/gif`.

### AI

The mock generator in `backend/ai_service.py` runs by default. To switch to Azure OpenAI, set `AZURE_OPENAI_*` variables (see [Setting your own](#setting-your-own-openai-key-third-party-apis)) and uncomment the `openai` line in `requirements.txt` and the real-implementation block in `ai_service.py`.

## Performance

The app is tuned to feel snappy on managed Postgres + Valkey (where each network round-trip is typically 50–200ms):

- **Homepage load** — 3 parallel requests, all served from the response cache after the first hit.
- **Click into a deck** — 2 parallel requests, both cached.
- **Click a study rating** — a **single** `POST /api/cards/{id}/review` returns the updated card, the next card to study, the new streak, the user's total reviews, and the deck's remaining due count. No follow-up `/study/next`, `/api/stats`, or `/api/leaderboard` calls needed.
- **Deck listing avoids N+1** — card counts and due counts are fetched in one `GROUP BY` query rather than one query per deck.
- **Cache invalidation is pipelined** — writes bust the deck list + per-deck detail + per-deck cards keys in a single Redis `DEL`.

## Environment variables

### Auto-injected by Embr (no setup needed)

| Variable | When you get it |
|---|---|
| `PORT` | Always (matches `run.port`) |
| `DATABASE_URL` | When `database.enabled: true` (managed Postgres) |
| `REDIS_URL` / `CACHE_URL` | When `cache.enabled: true` (managed Valkey) |
| `EMBR_BLOB_KEY` | Always — blob storage is auto-provisioned per environment |
| `EMBR_DOMAIN` | Always — used by `backend/blob.py` to form blob upload URLs |
| `EMBR_ENVIRONMENT`, `EMBR_PROJECT_ID` | Always |

### Setting your own (OpenAI key, third-party APIs)

Use the Embr CLI:

```bash
# Plain variable
embr variables set --key AZURE_OPENAI_ENDPOINT --value https://<your-resource>.openai.azure.com/

# Secret (encrypted at rest, masked in logs)
embr variables set --key AZURE_OPENAI_API_KEY --value <key> --secret
embr variables set --key AZURE_OPENAI_DEPLOYMENT --value <deployment-name>
```

Or via the **Portal**: open your project → environment → **Variables** tab → **Add variable** → toggle **Secret** for sensitive values → **Save**.

> Variables are injected on the next deploy/restart. Push a new commit or run `embr deployments create` after setting them.

### To enable real AI in this app

```bash
embr variables set --key AZURE_OPENAI_ENDPOINT --value https://<your-resource>.openai.azure.com/
embr variables set --key AZURE_OPENAI_API_KEY --value <key> --secret
embr variables set --key AZURE_OPENAI_DEPLOYMENT --value <deployment-name>
```

Then uncomment `openai>=1.12` in `requirements.txt` and the real-implementation block in `backend/ai_service.py`, and redeploy. The "AI Mock" badge in the UI will switch to "AI ON".

### Local development

Set the same variables in your shell or a `.env` file (gitignored):

```powershell
# PowerShell
$env:AZURE_OPENAI_ENDPOINT = "https://..."
$env:AZURE_OPENAI_API_KEY = "..."
$env:AZURE_OPENAI_DEPLOYMENT = "..."
$env:REDIS_URL = "redis://127.0.0.1:6379"   # if running redis locally
```

```bash
# bash / zsh
export AZURE_OPENAI_ENDPOINT="https://..."
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_DEPLOYMENT="..."
export REDIS_URL="redis://127.0.0.1:6379"
```

## Logging

The app sets up structured logging on import via [`backend/logging_config.py`](backend/logging_config.py) — every module just does `logger = logging.getLogger(__name__)` and inherits the same format and level. Logs go to **stdout**, which Embr captures and exposes in the portal under each environment's **Logs** tab.

**Format**: `<iso-timestamp> <LEVEL> <module> — <message>`

```
2026-04-17T15:27:42 INFO    backend.store — Using SQL store at sqlite:///deploy_test.db
2026-04-17T15:27:42 INFO    backend.app — Flashcards AI starting — store=_DBStore cache=memory blob=_EmbrBlobStore ai=mock
2026-04-17T15:27:42 INFO    backend.app — Created deck id=b5e4be19e0 title='New Deck'
```

**Levels**: set `LOG_LEVEL` to `DEBUG`, `INFO` (default), `WARNING`, or `ERROR`:

```bash
embr variables set --key LOG_LEVEL --value DEBUG
```

**What gets logged**:

- **Startup summary** — which backend was picked for store / cache / blob / AI, so you can verify managed services connected.
- **Backend selection** — each component logs the backend it chose at import (with credentials masked in URLs).
- **Lifecycle events** — deck and card create/update/delete (`INFO`); reviews (`DEBUG` to avoid noise); AI generations and image uploads (`INFO`).
- **Warnings** — rate-limit hits, rejected uploads, Redis connection failures, DB init failures (with full traceback).

The `/api/health` endpoint also reports backend status (`ai_enabled`, `cache_enabled`, `embr_blob_enabled`) for quick at-a-glance checks via curl or uptime monitors.

## How to use this app

1. **Browse the seeded decks** to learn the UI without typing a word.
2. **Create a new deck**, then add cards one of three ways:
   - Type the front/back manually and pick a **seed diagram** from the gallery.
   - Type the front/back manually and **upload your own image**.
   - Paste a block of study notes and click **AI Generate** to get a batch of Q/A pairs.
3. **Click Study** on any deck to enter the spaced-repetition flow. Rate each card; the SM-2 scheduler picks when you'll see it next.
4. **Compete on the streak leaderboard** by reviewing at least one card every day — your name on the leaderboard is set in the top-right input.

## Project layout

```
flashcard-app/
├── application.py          # WSGI entrypoint (a2wsgi → FastAPI)
├── embr.yaml               # Embr deployment config
├── requirements.txt
├── db/
│   └── schema.sql          # Postgres schema — run by Embr on every deploy
├── backend/
│   ├── app.py              # FastAPI routes
│   ├── logging_config.py   # Structured logging setup (LOG_LEVEL env var)
│   ├── models.py           # Pydantic schemas + in-memory record classes
│   ├── store.py            # Decks/cards store (SQLAlchemy + in-memory fallback) + seed data
│   ├── scheduler.py        # SM-2 review algorithm
│   ├── cache.py            # Leaderboard + TTL cache + rate limiting (Redis + in-memory fallback)
│   ├── blob.py             # Image upload handling (Embr blob + local fallback)
│   └── ai_service.py       # AI card generation
└── static/
    ├── index.html          # Single-page frontend (vanilla JS)
    ├── favicon.svg
    └── seed-diagrams/      # Pre-bundled placebo diagrams
        ├── cell-structure.svg
        ├── water-cycle.svg
        ├── atom.svg
        ├── binary-tree.svg
        └── linked-list.svg
```

## Notes for editing the app

- **Replace the seed decks** with your own subject content by editing `_SEED_DECKS` in `backend/store.py`.
- **Replace the seed diagrams** by dropping new SVG/PNG files into `static/seed-diagrams/` and updating the `SEED_DIAGRAMS` list in `static/index.html`.
- **Tighten the AI rate limit** in `backend/app.py` (`rate_limit("ratelimit:ai:...", max_requests=20, ...)`) once you have real users.

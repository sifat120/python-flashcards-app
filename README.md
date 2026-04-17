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
- **Study mode** — SM-2-style spaced repetition (`again` / `hard` / `good` / `easy`)
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
| Blob storage | Local `uploads/` dir  | Auto-provisioned blob → `EMBR_BLOB_KEY` + `/_embr/blob/{key}` | [`backend/blob.py`](backend/blob.py) |
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

### Blob storage

Embr auto-provisions blob storage for every environment — no `embr.yaml` toggle needed. `EMBR_BLOB_KEY` is injected at runtime. Until you wire `backend/blob.py` to write to `https://<env>/_embr/blob/{key}`, image uploads land in the local `backend/uploads/` directory and are served by the app at `/uploads/{key}` (works on Embr too, but isn't durable across redeploys).

### AI

The mock generator in `backend/ai_service.py` runs by default. To switch to Azure OpenAI, set `AZURE_OPENAI_*` variables (see [Setting your own](#setting-your-own-openai-key-third-party-apis)) and uncomment the `openai` line in `requirements.txt` and the real-implementation block in `ai_service.py`.

## Environment variables

### Auto-injected by Embr (no setup needed)

| Variable | When you get it |
|---|---|
| `PORT` | Always (matches `run.port`) |
| `DATABASE_URL` | When `database.enabled: true` (managed Postgres) |
| `REDIS_URL` / `CACHE_URL` | When `cache.enabled: true` (managed Valkey) |
| `EMBR_BLOB_KEY` | Always — blob storage is auto-provisioned per environment |
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
│   ├── models.py           # Pydantic schemas + in-memory record classes
│   ├── store.py            # Decks/cards store (SQLAlchemy + in-memory fallback) + seed data
│   ├── scheduler.py        # SM-2 review algorithm
│   ├── cache.py            # Leaderboard + TTL cache + rate limiting (Redis + in-memory fallback)
│   ├── blob.py             # Image upload handling
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

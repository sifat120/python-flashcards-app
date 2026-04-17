# Flashcards AI — Spaced Repetition with Diagrams

A template app ready to deploy on [Embr](https://portal.embr.azure). FastAPI backend serves a vanilla HTML/JS frontend on a single port — no frontend build step.

Comes pre-loaded with three example decks (Biology, Data Structures, Chemistry) so you can see the study flow, AI-generation, and diagram support working out of the box.

## Deploy to Embr

```yaml
# build.yaml
version: 1
platform: python
platformVersion: "3.14"
run:
  port: 8080
```

Connect this repo in the [Embr Portal](https://portal.embr.azure), and Embr will install dependencies from `requirements.txt`, then start the server automatically.

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

## How the template maps to Embr's managed components

The template runs entirely in-memory by default so it's frictionless to try. Each backing service has a clear swap path:

| Component | Default (template) | Production (Embr) | File |
|-----------|--------------------|--------------------|------|
| Database  | In-memory dict     | Managed Postgres (`database.enabled: true`) | [`backend/store.py`](backend/store.py) |
| Cache     | In-memory dict     | Managed Valkey/Redis (`cache.enabled: true`) | [`backend/cache.py`](backend/cache.py) |
| Blob storage | Local `uploads/` dir | Azure Blob via `EMBR_BLOB_KEY` | [`backend/blob.py`](backend/blob.py) |
| AI        | Mock card generator | Azure OpenAI (`AZURE_OPENAI_*` env vars) | [`backend/ai_service.py`](backend/ai_service.py) |

Each module's docstring shows exactly what to uncomment / change. The API in `backend/app.py` does **not** need to change when you swap any of these out.

### Enabling Postgres + Redis on Embr

Edit `build.yaml` (or rename to `embr.yaml`):

```yaml
version: 1
platform: python
platformVersion: "3.14"
run:
  port: 8080
database:
  enabled: true     # Postgres provisioned, DATABASE_URL injected
cache:
  enabled: true     # Valkey provisioned, REDIS_URL injected
healthCheck:
  path: /api/health
```

Then uncomment the `redis`, `sqlalchemy`, and `psycopg` lines in `requirements.txt` and switch the in-memory implementations in `store.py` / `cache.py` to use the injected URLs.

## How customers use this template

1. **Browse the seeded decks** to learn the UI without typing a word.
2. **Create a new deck**, then add cards one of three ways:
   - Type the front/back manually and pick a **seed diagram** from the gallery.
   - Type the front/back manually and **upload your own image**.
   - Paste a block of study notes and click **AI Generate** to get a batch of Q/A pairs.
3. **Click Study** on any deck to enter the spaced-repetition flow. Rate each card; the SM-2 scheduler picks when you'll see it next.
4. **Compete on the streak leaderboard** by reviewing at least one card every day — your name on the leaderboard is set in the top-right input.

## Project layout

```
template-flashcards-app/
├── application.py          # WSGI entrypoint (a2wsgi → FastAPI)
├── build.yaml              # Embr deployment config
├── requirements.txt
├── backend/
│   ├── app.py              # FastAPI routes
│   ├── models.py           # Pydantic schemas + in-memory record classes
│   ├── store.py            # Decks/cards store + seed data
│   ├── scheduler.py        # SM-2 review algorithm
│   ├── cache.py            # Leaderboard + TTL cache + rate limiting
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

## Notes for editing the template

- **Replace the seed decks** with your own subject content by editing `_SEED_DECKS` in `backend/store.py`.
- **Replace the seed diagrams** by dropping new SVG/PNG files into `static/seed-diagrams/` and updating the `SEED_DIAGRAMS` list in `static/index.html`.
- **Tighten the AI rate limit** in `backend/app.py` (`rate_limit("ratelimit:ai:...", max_requests=20, ...)`) once you have real users.

-- Flashcards AI — database schema.
-- Embr auto-detects this file (`db/schema.sql`) when `database.enabled: true`
-- and runs `psql $DATABASE_URL -f db/schema.sql` on every deploy.
-- The CREATE TABLE IF NOT EXISTS guards keep it idempotent.

CREATE TABLE IF NOT EXISTS decks (
    id          TEXT        PRIMARY KEY,
    title       TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    subject     TEXT        NOT NULL DEFAULT '',
    created_at  TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id            TEXT        PRIMARY KEY,
    deck_id       TEXT        NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    front_text    TEXT        NOT NULL,
    back_text     TEXT        NOT NULL,
    image_url     TEXT,
    ease          DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    interval_days INTEGER     NOT NULL DEFAULT 0,
    next_review   DATE        NOT NULL,
    last_reviewed TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS cards_deck_id_idx     ON cards (deck_id);
CREATE INDEX IF NOT EXISTS cards_next_review_idx ON cards (deck_id, next_review);

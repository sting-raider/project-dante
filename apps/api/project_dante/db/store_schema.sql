-- Dante persistence layer — Postgres schema (plan §11.2).
--
-- Applied by PostgresStore.ensure_schema() on first connection and safe to
-- run manually via psql:
--
--     psql "$DATABASE_URL" -f project_dante/db/store_schema.sql
--
-- Design notes:
-- - The store contract (db/store.py) treats every record as an opaque dict
--   carrying its type under `_type` and a globally-unique string `id`. The
--   `records` table mirrors that exactly: the full record lives in `payload`
--   JSONB; `_type` and `id` are promoted into columns for indexing.
-- - Money stays integer paise inside payloads — JSONB preserves ints, so no
--   NUMERIC columns are needed and round-trips are exact.
-- - `created_at` / `updated_at` are store-level metadata (NOT injected into
--   payloads) so get()/list()/find() return byte-shape-identical dicts to
--   the JSON backend.

BEGIN;

CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS records_type_idx ON records (record_type);

-- GIN over payloads: accelerates JSONB containment queries for find().
CREATE INDEX IF NOT EXISTS records_payload_gin_idx ON records USING GIN (payload);

-- Append-only domain event stream (plan §21). One row per DomainEvent;
-- `idempotency_key` is unique per aggregate so webhook replays collapse at
-- the storage layer, mirroring EventLog's in-memory dedup semantics.
CREATE TABLE IF NOT EXISTS domain_events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    aggregate_type  TEXT NOT NULL,
    aggregate_id    TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'System',
    event_version   INTEGER NOT NULL DEFAULT 1,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id  TEXT,
    causation_id    TEXT,
    idempotency_key TEXT,
    trace_id        TEXT,
    synthetic       BOOLEAN NOT NULL DEFAULT FALSE,
    scenario_id     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS domain_events_aggregate_idx
    ON domain_events (aggregate_id);

CREATE INDEX IF NOT EXISTS domain_events_type_idx
    ON domain_events (event_type);

-- Replay dedup: same idempotency key on the same aggregate is a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS domain_events_idem_idx
    ON domain_events (COALESCE(aggregate_id, ''), COALESCE(idempotency_key, ''))
    WHERE idempotency_key IS NOT NULL;

COMMIT;

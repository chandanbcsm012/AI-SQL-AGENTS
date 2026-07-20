-- New durable-memory tables, on Postgres (infra/docker-compose.yml).
--
-- Deliberately NOT a migration of app_user/table_ownership/review_queue --
-- those stay on SQLite (db/schema.sql) for now; they're small, single-writer
-- at this scale, and already have a well-tested auth/HITL flow built on
-- them. This is net-new capability with no legacy behavior to preserve, so
-- it goes straight to Postgres, matching docs/IMPLEMENTATION_PLAN.md
-- Phase 4's durable-store direction without destabilizing Phase 1-3's
-- already-working, already-tested SQLite-backed modules.
--
-- activity_log doubles as both "query history" (personalization / few-shot
-- retrieval of past NL->SQL pairs) and "audit log" (who ran what, when) --
-- one row per completed run, rather than two overlapping tables.

CREATE TABLE IF NOT EXISTS activity_log (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    username        TEXT,
    question        TEXT NOT NULL,
    sql             TEXT,
    status          TEXT NOT NULL,
    tables_touched  TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_username ON activity_log (username, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_trace_id ON activity_log (trace_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    username    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, key)
);

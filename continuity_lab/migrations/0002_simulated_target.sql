-- Simulated local publishing target. Independent of orchestration state:
-- the lab only reaches it through continuity_lab.services.target.
CREATE SCHEMA IF NOT EXISTS target;

CREATE TABLE target.releases (
    release_id       UUID PRIMARY KEY,
    idempotency_key  TEXT NOT NULL UNIQUE,
    workspace_id     TEXT NOT NULL,
    version          TEXT NOT NULL,
    title            TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    draft_revision   INT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX releases_ws_idx ON target.releases (workspace_id);

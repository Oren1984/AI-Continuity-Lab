-- Semantic retrieval over Help Center entries and eligible saved context.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE lab.embeddings (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  TEXT REFERENCES lab.workspaces(id) ON DELETE CASCADE,  -- NULL = global Help Center
    source_type   TEXT NOT NULL CHECK (source_type IN ('help', 'decision', 'run_note')),
    source_id     TEXT NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(384) NOT NULL,
    model         TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, source_type, source_id)
);
CREATE UNIQUE INDEX embeddings_help_unique ON lab.embeddings (source_type, source_id) WHERE workspace_id IS NULL;

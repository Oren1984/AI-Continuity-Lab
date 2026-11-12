-- Orchestration state for AI Continuity Lab.
CREATE SCHEMA IF NOT EXISTS lab;

CREATE TABLE lab.workspaces (
    id          TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,39}$'),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.decisions (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES lab.workspaces(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    decision      TEXT NOT NULL,
    source        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, key)
);

CREATE TABLE lab.runs (
    id              UUID PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES lab.workspaces(id) ON DELETE CASCADE,
    scenario        TEXT NOT NULL,
    task_variant    TEXT NOT NULL,
    tool_profile    TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    recovery_mode   TEXT NOT NULL DEFAULT 'continuity' CHECK (recovery_mode IN ('continuity', 'baseline')),
    origin          TEXT NOT NULL DEFAULT 'ui' CHECK (origin IN ('ui', 'eval', 'test')),
    goal            TEXT NOT NULL,
    stage           TEXT NOT NULL,
    pending_input   TEXT,
    pending_question JSONB,
    signin_choice   TEXT,
    context_from_checkpoint JSONB,
    group_id        UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX runs_workspace_idx ON lab.runs (workspace_id, created_at DESC);

CREATE TABLE lab.draft_revisions (
    id          UUID PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    revision    INT NOT NULL,
    content     JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    author      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, revision)
);

CREATE TABLE lab.checkpoints (
    id          UUID PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    seq         INT NOT NULL,
    step        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE TABLE lab.step_log (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    step        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('started', 'completed', 'abandoned')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.verification_evidence (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    draft_revision  INT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('signin', 'receipt')),
    claim           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('verified', 'failed', 'not_verified')),
    source          TEXT NOT NULL,
    detail          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.approvals (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    draft_revision  INT NOT NULL,
    content_hash    TEXT NOT NULL,
    verification_id UUID NOT NULL REFERENCES lab.verification_evidence(id),
    target          TEXT NOT NULL,
    approved_by     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active', 'invalidated', 'used')),
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one active approval per run.
CREATE UNIQUE INDEX approvals_one_active ON lab.approvals (run_id) WHERE status = 'active';

CREATE TABLE lab.operations (
    id               UUID PRIMARY KEY,
    run_id           UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    approval_id      UUID NOT NULL REFERENCES lab.approvals(id),
    draft_revision   INT NOT NULL,
    target           TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'unknown', 'not_written')),
    attempts         INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one live publication operation per run: a retry reuses the original operation ID.
-- A new operation is possible only after the previous one was reconciled as not written
-- and the draft changed.
CREATE UNIQUE INDEX operations_one_live_per_run ON lab.operations (run_id)
    WHERE status IN ('pending', 'unknown', 'confirmed');

CREATE TABLE lab.publication_attempts (
    id            BIGSERIAL PRIMARY KEY,
    operation_id  UUID NOT NULL REFERENCES lab.operations(id) ON DELETE CASCADE,
    attempt_no    INT NOT NULL,
    outcome       TEXT NOT NULL,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.target_receipts (
    id            UUID PRIMARY KEY,
    operation_id  UUID NOT NULL UNIQUE REFERENCES lab.operations(id) ON DELETE CASCADE,  -- one receipt per operation
    run_id        UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    release_id    UUID NOT NULL,
    receipt       JSONB NOT NULL,
    obtained_via  TEXT NOT NULL CHECK (obtained_via IN ('response', 'reconciliation')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.messages (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID NOT NULL REFERENCES lab.runs(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content       TEXT NOT NULL,
    tool_profile  TEXT,
    model_id      TEXT,
    latency_ms    INT,
    tokens_in     INT,
    tokens_out    INT,
    simulated     BOOLEAN NOT NULL DEFAULT TRUE,
    claims        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lab.events (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    TEXT,
    run_id          UUID,
    correlation_id  TEXT,
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX events_run_idx ON lab.events (run_id, id);
CREATE INDEX events_ws_idx ON lab.events (workspace_id, id);

CREATE TABLE lab.eval_results (
    id                         BIGSERIAL PRIMARY KEY,
    batch_id                   UUID NOT NULL,
    comparison                 TEXT NOT NULL CHECK (comparison IN ('model', 'recovery')),
    scenario                   TEXT NOT NULL,
    task_variant               TEXT NOT NULL,
    condition                  TEXT NOT NULL,
    model_id                   TEXT NOT NULL,
    model_tag                  TEXT NOT NULL,
    repetition                 INT NOT NULL,
    completed                  BOOLEAN NOT NULL,
    repeated_steps             INT NOT NULL,
    recovery_time_ms           INT,
    necessary_clarifications   INT NOT NULL,
    unnecessary_clarifications INT NOT NULL,
    missed_clarifications      INT NOT NULL,
    unsupported_claims         INT NOT NULL,
    claims_accepted_without_evidence INT NOT NULL,
    duplicate_publications     INT NOT NULL,
    latency_ms                 INT,
    tokens_in                  INT,
    tokens_out                 INT,
    simulated                  BOOLEAN NOT NULL,
    settings                   JSONB NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

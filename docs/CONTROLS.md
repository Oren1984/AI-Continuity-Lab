# Controls (shift-left)

What is built and how to see it. This is a proof of concept with synthetic data; it makes no compliance certification claims.

## Security

- **Untrusted input.** Uploaded checkpoints: 256 KB limit (also `server.maxUploadSize=1` MB), UTF-8 JSON object, strict schema with no unknown fields, length limits, workspace ID pattern, source references limited to an allowlist of fixture files, idempotency keys pattern-checked (`services/checkpoint.py`, tests in `tests/unit/test_checkpoint_events_verifier.py`). Tool inputs: instructions are trimmed to 2 000 characters; model output is stored as data, never executed, and only maps to a fixed set of sign-in modes. The Ollama system prompt tells the model to treat user text as data.
- **Server-side scope and policy.** Every repository query is scoped by workspace; the target lookup is scoped by workspace and idempotency key; policy runs inside the workflow service, not the UI.
- **Local-only defaults.** The database has no published port; the app binds to `127.0.0.1:8501`. No credentials or API keys are needed. The container runs as a non-root user.
- **Secrets.** `.env` and `.env.*` (except `.env.example`) are ignored by Git and Docker builds. The default database password is a local-only value overridable through `.env`. Exports omit the (synthetic) demo password.
- **Fixture check isolation.** The sign-in check runs as a subprocess with `python -I`, a timeout, and its request on stdin.

## Observability

Structured events in `lab.events` correlate run ID (correlation ID), adapter calls (model, latency, tokens, simulated flag, capability changes), policy checks (rule, allowed, reason), approvals, failures (stall, unavailable, interruption, unknown outcome), reconciliation and target receipts (operation ID, release ID). `EventLog` redacts sensitive keys (`password`, `prompt`, `token`, `api_key`, …), key-like strings and non-demo e-mail addresses. Raw prompts are excluded unless `LOG_RAW_PROMPTS=true`. The run's **Event timeline** shows them.

## Monitoring

**Operations** page: app and database readiness (migration version), embedding model, simulated provider and dependency state per scenario, error and denial counts, adapter response times, modeled recovery time, unresolved operations with an **alert** when reconciliation is needed (also an `alert_reconciliation_needed` event), and the simulated target's releases. Container healthchecks: `pg_isready` and Streamlit `/_stcore/health`.

## Governance

A model can only propose a draft mode or ask a question. The workflow service authorizes every transition; restricted actions (approve, publish, retry, edit after an unknown outcome) are checked by `PolicyService` and every decision is recorded. Approval is bound to revision, content hash, evidence and target. Each status shows its evidence source beside the claim. Imported approvals and receipts are informational until independently resolved.

## Data minimization, retention, export, deletion

| Record | Covered by retention (`RETENTION_DAYS`, default 30) | Deleted by “Delete demo records” | Notes |
| --- | --- | --- | --- |
| Runs and everything under them: drafts, checkpoints, steps, evidence, approvals, operations, attempts, receipts, messages | yes (cascade) | yes | |
| Events (audit log) | yes, after retention + 7 days | yes | A content-free `records_deleted` tombstone (counts only) is kept. |
| Embeddings: Help Center (global) | no — product content | no | Not personal data. |
| Embeddings: workspace decisions | with the workspace | yes | Drafts, prompts and credentials are **never** embedded. |
| Evaluation results | yes | no (eval workspace) | Aggregated metrics only. |
| Simulated target releases | no (they model an external system) | yes | Version, title, hashes only. |

Purge runs at every startup and from Operations. Export is scoped to one run in the current workspace and omits the demo password. Boundary of the POC: no real users, no real providers, no real publishing destination; the simulated target is isolated in the `target` schema of the local database.

## Costs

Token usage and latency are captured per adapter call and shown in the conversation, Operations and Results. Fixture values are labeled **simulated**. Local compute shows the app process CPU seconds and peak memory. A money estimate is shown as **unavailable** because fixture tokens are not real and no price per token is configured; a real estimate needs live token counts and prices in currency per 1M tokens, stated with those assumptions.

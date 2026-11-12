# Architecture

## Components

| Layer | Where | Responsibility |
| --- | --- | --- |
| UI | `app/` (Streamlit) | Rendering and interaction only. Session state holds transient selections; the active run lives in the URL (`?run=`), so a reload, a second browser, or an app restart resumes from PostgreSQL. |
| Workflow service | `continuity_lab/services/workflow.py` | The only place state changes. Persists each transition and a checkpoint before returning. Calls policy at the service boundary. |
| Policy | `services/policy.py` | Pure decisions over a run snapshot: edit, verify, approve, publish, reconcile. Every decision is written to the event log (denials in their own transaction, so they survive the rollback). |
| Model adapters | `services/adapters.py` | `FixtureAdapter` (default, deterministic, offline) and optional `OllamaAdapter`. A model *proposes* a sign-in mode or asks a question; it never creates records. |
| Scenario runner | `services/scenarios.py`, `services/providers.py` | Deterministic fault plans (stall, outage, capability removal, fallback model, interruption, lost response) applied to simulated tool profiles with fictional dependencies. Fault consumption is counted from persisted events, so it behaves the same across reruns and sessions. |
| Verifier | `services/verifier.py` | Runs `fixtures/demo_repo/checks/check_signin.py` in a subprocess (`python -I`) against the synthetic repository, and compares target receipts with the approved draft. Its results are the only source of VERIFIED. |
| Simulated target | `services/target.py` | Local release registry in its own `target` schema behind four calls (`publish`, `lookup`, `list_releases`, `delete_workspace`). Writes in its own transaction; unique idempotency key. `FaultyTargetClient` loses one response before or after the write. |
| Repositories | `continuity_lab/repositories.py` | SQL, always scoped by workspace. |
| Retrieval | `services/retrieval.py` | pgvector search over Help Center articles and workspace decisions using `BAAI/bge-small-en-v1.5` (384-d), downloaded at image build time by `scripts/preload_embeddings.py`. A labeled hashing fallback exists only for running outside the image. |
| Evaluation | `services/evaluation.py` | Deterministic simulated person drives full episodes through the same services and scores them from state and receipts. |
| Retention | `services/retention.py` | Expiry purge and scoped deletion with content-free tombstones. |

Containers: `db` (pgvector/pgvector:pg16, durable volume, healthcheck, no published port) and `app` (Python 3.12 slim, non-root user, healthcheck on `/_stcore/health`). Optional `ollama` under a Compose profile. No other services were needed: the target's independence is a separate schema and transaction, which is enough to lose a response after a successful write.

Startup (`continuity_lab/entrypoint.py`): wait for the database → apply versioned migrations under an advisory lock → seed the demo workspace and decisions (idempotent) → index Help Center and decisions → purge expired records → start Streamlit.

## State (PostgreSQL)

`lab` schema: `workspaces`, `decisions`, `runs`, `draft_revisions`, `checkpoints`, `step_log`, `verification_evidence`, `approvals`, `operations`, `publication_attempts`, `target_receipts`, `messages`, `events`, `eval_results`, `embeddings`. `target` schema: `releases`. Migrations: `continuity_lab/migrations/0001…0003`.

Key constraints:

- `approvals_one_active` — at most one active approval per run.
- `operations_one_live_per_run` — at most one pending/unknown/confirmed operation per run.
- `operations.idempotency_key UNIQUE` and `target.releases.idempotency_key UNIQUE`.
- `target_receipts.operation_id UNIQUE` — one receipt per operation.

## Publication protocol

1. **Authorize and persist** (one transaction, run row locked `FOR UPDATE`): policy check; create the operation (or reuse the original one after a *not written* reconciliation); mark it `pending`; record the attempt. Concurrent clicks wait on the lock and then see `pending` → "in progress", so only one request reaches the target.
2. **Write to the target** in its own transaction with `ON CONFLICT (idempotency_key) DO NOTHING`.
3. **Record the receipt**, mark the operation `confirmed`, the approval `used`, write a checkpoint, then have the verifier check the receipt against the approved content hash.

If step 2 raises (lost response before or after the write), the operation becomes `unknown` and an alert event is written. Retry is refused until **reconcile** looks the key up at the target: found → receipt via reconciliation; absent → `not_written`, and a retry reuses the same operation ID.

Approval binds draft revision, content hash, verification evidence ID, and target. Editing creates a new revision and invalidates the active approval; the old evidence no longer matches the latest revision, so publication is blocked until a fresh check and a fresh approval.

## Checkpoints

Written after every committed transition and exportable as JSON (`schema: ai-continuity-lab/checkpoint`, `version: 1.0`): task goal, variant, draft (password omitted on export), decisions, completed and pending steps, fixture source hashes, verification references, approval, operation status and receipt, preserved input, and a manual continuation section. Import (`services/checkpoint.py`) enforces size (256 KB), UTF-8 JSON, a strict Pydantic schema with no extra fields and length limits, workspace ID format, and source references restricted to the fixture allowlist. Same workspace and run → **resume**. Otherwise → a **new run using the checkpoint as context**: the draft is imported as untrusted (verification required), and imported approvals, receipts and evidence are informational only. An imported operation can be looked up at the target only within the current workspace.

## Request path example

`Publish` button → `WorkflowService.publish` → `PolicyService.can_publish` → `Repo.create_operation` → `FaultyTargetClient.publish` → `SimulatedTarget.publish` → `Repo.add_receipt` → `Verifier.check_receipt` → events correlated by run ID and operation ID.

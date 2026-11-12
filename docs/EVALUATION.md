# Evaluation

Both comparisons run full episodes through the real workflow service, PostgreSQL and the simulated target (`continuity_lab/services/evaluation.py`), in an isolated `eval` workspace. A deterministic simulated person plays each episode. Completion is scored from saved state and target receipts: the receipt must verify against the approved draft **and** the published sign-in mode must match what the person intended (visible credentials). Run them from **Results** in the app.

## 1. Model comparison

Changes only the model. Held constant: task text per variant, scenario, starting state, tool profile (ChatGPT), recovery features (full continuity), rubric v1, temperature 0.

Cells: explicit requirement, requirement inferable from an existing decision (DEC-004), material ambiguity (no decision), and capability loss (to observe unsupported verification claims).

## 2. Recovery comparison

Holds the model (`fixture-advanced`) and each scenario constant; compares:

- **baseline** — no checkpoint, no transfer between tools, no evidence beside claims. After a failure the person starts a fresh session and redoes the work; a lost publication result looks like a failure.
- **continuity** — checkpoints, input preservation, tool transfer, unknown-outcome reconciliation and evidence beside each claim.

Approval and idempotent publication are enforced in **every** run. The baseline duplicates in *lost response after write* do not come from a missing safeguard inside one run: the person started a new session, which created a new operation the target cannot recognise as the same intent.

## Metrics

| Metric | Definition |
| --- | --- |
| Completed | Receipt verified and published sign-in mode = intended. |
| Repeated steps | Completed workflow steps done again in the same task (across all sessions the person used), plus re-typing lost input. Rework after a failed check counts. |
| Recovery time (modeled) | Measured system time from the first failure to completion **plus** modeled human time: repeat step 45 s, retry/switch 10 s, clarification 20 s, reconcile 15 s, re-enter input 30 s, restart session 20 s. Blank when no failure occurred. |
| Necessary / unnecessary / missed questions | Against predefined expectations: ask only for the ambiguous variant. |
| Unsupported claims made | Model claims “sign-in verified” without verifier evidence. |
| Claims accepted without evidence | Unsupported claims the person relied on (baseline shows no evidence beside claims). |
| Duplicate releases | Releases in the target beyond one, across the person's sessions for the task. |
| Latency / tokens | From adapter calls. **Simulated** for fixtures. |

## Results (fixture models, 3 repetitions per cell, run 2026-11-12)

These are **simulation results**. Fixture models are deterministic, so the three repetitions are identical; *n* is shown so the limits are visible.

### Model comparison

| Scenario / variant | Model | n | Completed | Repeated | Necessary Q | Unnecessary Q | Missed Q | Unsupported claims | Latency ms (sim.) | Tokens (sim.) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal / explicit | fixture-lite | 3 | 100% | 0 | 0 | 0 | 0 | 0 | 420 | 140 |
| normal / explicit | fixture-advanced | 3 | 100% | 0 | 0 | 0 | 0 | 0 | 950 | 140 |
| normal / inferable | fixture-lite | 3 | 100% | 0 | 0 | **3** | 0 | 0 | 420 | 109 |
| normal / inferable | fixture-advanced | 3 | 100% | 0 | 0 | 0 | 0 | 0 | 950 | 127 |
| ambiguous | fixture-lite | 3 | 100% | **1.0** | 0 | 0 | **3** | 0 | 420 | 79 |
| ambiguous | fixture-advanced | 3 | 100% | 0 | **3** | 0 | 0 | 0 | 950 | 67 |
| capability loss / explicit | fixture-lite | 3 | 100% | 0 | 0 | 0 | 0 | **3** | 420 | 133 |
| capability loss / explicit | fixture-advanced | 3 | 100% | 0 | 0 | 0 | 0 | 0 | 950 | 141 |

**Attributable to the model:** the lighter model asked an unnecessary question when a prior decision answered it, guessed one-click entry instead of asking (verification then failed on evidence and the draft had to be reworked), and claimed verification it could not have done. Completion did not differ, because the verifier and policy caught every mistake — the cost shows up as questions, rework and unsupported claims.

### Recovery comparison (fixture-advanced)

| Scenario | Condition | n | Completed | Repeated steps | Recovery s (modeled) | Duplicate releases |
| --- | --- | --- | --- | --- | --- | --- |
| provider overload | baseline | 3 | 100% | 1.0 | 40.2 | 0 |
| provider overload | continuity | 3 | 100% | 0 | 10.2 | 0 |
| interruption | baseline | 3 | 100% | 1.0 | 20.1 | 0 |
| interruption | continuity | 3 | 100% | 0 | 10.1 | 0 |
| capability loss | baseline | 3 | 100% | 1.0 | 30.1 | 0 |
| capability loss | continuity | 3 | 100% | 0 | 10.1 | 0 |
| lost response after write | baseline | 3 | 100% | 3.0 | 20.1 | **3** (1 per run) |
| lost response after write | continuity | 3 | 100% | 0 | 15.0 | 0 |
| lost response before write | baseline | 3 | 100% | 3.0 | 20.2 | 0 |
| lost response before write | continuity | 3 | 100% | 0 | 15.0 | 0 |
| shared dependency outage | baseline | 3 | 100% | 2.0 | 120.2 | 0 |
| shared dependency outage | continuity | 3 | 100% | 0 | 10.1 | 0 |

**Attributable to preserving and verifying the work:** repeated steps 33 → 0 over 18 episodes each, duplicate releases 3 → 0, and lower modeled recovery time in every scenario. The advanced model made no unsupported claims here, so *claims accepted without evidence* is 0 in both conditions; with `fixture-lite` the baseline would accept them (see the model comparison).

**Still the person's choice in every run:** which sign-in behavior the release should have when nothing settles it, approving publication of a specific revision, and whether to switch tools or continue manually.

## Limits

- One task, one fixture repository, deterministic models, scripted human behavior. Results describe these conditions only.
- Human time is modeled, not observed.
- Latency and token counts for fixtures are simulated values.

## Optional live comparison (Ollama)

`OllamaAdapter` sends the same structured request to two configurable local models (`OLLAMA_MODEL_LIGHT`, `OLLAMA_MODEL_HEAVY`) with temperature 0 and seed 7, and reports real latency and token counts. Start it with `docker compose --profile ollama up --build`, pull the models into the `ollama` container, set `LAB_MODEL_ADAPTER=ollama` and the two tags in `.env`. The Results page shows each model as *not configured*, *configured, unavailable*, or *available (untested in this session)*; only available models join the model comparison.

**Status: pending.** No live comparison was run for this release (no Ollama server was running on the build machine), so exact tags, hardware and results are not recorded. The Ollama adapter code path is untested against a live model. The fixture workflow does not depend on it. When run, record here: model tags and digests, CPU/GPU and RAM, repetitions, and the per-cell table.

## What was run for V1.0

- `docker compose run --rm app python -m pytest -q` → **46 passed** (unit, integration on PostgreSQL 16 + pgvector, scenario e2e, concurrency, headless Streamlit UI).
- Both comparisons above, 3 repetitions each, from a clean volume.
- Manual UI checks in a browser on Windows 11 + Docker Desktop: happy path published through the UI; lost response → app restart → resume in a new tab → reconcile → one release.

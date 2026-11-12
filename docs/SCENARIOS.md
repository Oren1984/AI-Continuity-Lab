# Scenarios

One seeded repository fixture (`fixtures/demo_repo`) and one release draft (`fixtures/release_draft.json`) are used across all scenarios. Faults are deterministic and defined in `continuity_lab/services/scenarios.py`. Each scenario is covered by an automated end-to-end test in `tests/e2e/test_scenarios.py`.

| Scenario | Layer | What the person sees | What the lab does | Test |
| --- | --- | --- | --- | --- |
| Normal run | — | Draft ready, sign-in verified beside the claim, approval, publication. | One receipt, verified against the approved draft. | `test_normal_run_one_verified_receipt` |
| Provider overload | service | ChatGPT accepts the request and stalls (simulated 30 s). | The input was saved before the call and is in the checkpoint. Retry, switch to an independent route, download the checkpoint, or continue manually. | `test_provider_overload_preserves_input_then_retry` |
| Connection or power interruption | device/connection | The interface disappears during verification; the browser session is cleared. | Reload or another browser restores the last committed checkpoint from PostgreSQL. The verification that started but never finished is shown as **UNCONFIRMED** and in the manual checklist. Also demonstrated with `docker compose restart app`. | `test_interruption_restores_last_checkpoint_and_marks_unconfirmed` |
| Capability loss, chat responsive | tool | The chat answers but ChatGPT has no repository access. | Evidence: **NOT VERIFIED in this run**. Approval and publication are blocked until fresh verification exists for the current revision (switch tool or manual check). | `test_capability_loss_blocks_publication_until_fresh_verification` |
| Lost response after approval — target written | service | Publish → no result. | **UNKNOWN**. Retry refused. Reconcile by operation ID finds the release; receipt recorded via reconciliation. Exactly one release. | `test_lost_response_after_write_reconciles_without_duplicate` |
| Lost response after approval — target not written | service | Publish → no result. | **UNKNOWN** → reconcile finds nothing → **NOT WRITTEN** → retry with the same operation ID → one release. | `test_lost_response_before_write_retries_with_same_operation` |
| Shared dependency outage | shared infrastructure | ChatGPT and GitHub Copilot fail together. | Names *North Shared Gateway (fictional)* as the shared dependency, offers Claude or Gemini, or a manual path (template draft + manual fixture check). | `test_shared_dependency_outage_offers_independent_route_and_manual` |
| Reduced capacity / model fallback | service | Responses continue from a smaller model. | Status card shows the active model (`fixture-lite`) and capability changes (no retrieval of earlier decisions). The comparison measures the effect (an unnecessary question). | `test_reduced_capacity_shows_fallback_and_asks` |
| Ambiguous requirement | — | “Fix demo sign-in”. | With decision DEC-004 available the model uses it; without it, a capable model asks one focused question. Publication still needs approval. | `test_ambiguity_uses_prior_decision_or_asks` |

Additional checks: stale approval after an edit (`test_stale_approval_rejected_after_edit`), six simultaneous retries and four simultaneous first publications (`test_concurrent_*`), Streamlit reruns / double clicks (`test_streamlit_rerun_double_clicks_are_idempotent`), and the full lost-response journey through the headless UI with resume in a second session (`tests/e2e/test_ui.py`).

## Outage layers in plain terms

A server or regional outage can interrupt service immediately (the *shared dependency* and *overload* scenarios). A chip supply constraint is modeled differently: as reduced capacity over time, where the service still answers but through a smaller fallback model with fewer capabilities (*reduced capacity*). Dependency names and which profile uses them are fictional inputs, not claims about real providers.

## The sign-in fixture

`check_signin.py` signs in to the synthetic app (`demo_app/auth.py`) with the draft's credentials, or tries one-click entry, which is disabled in `settings.json` per decision DEC-004. So a draft that guesses one-click entry **fails** verification on real evidence, and a draft with a wrong password fails too.

## Manual demonstrations performed

On Windows 11 with Docker Desktop: published through the normal UI (Normal run → one verified receipt); ran *Lost response (target written)* to UNKNOWN, restarted the app container, opened the run in a new browser tab, pressed *Check publication status*, and confirmed in PostgreSQL that the operation was confirmed via reconciliation with exactly one release.

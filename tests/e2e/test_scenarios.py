"""End-to-end checks of the scenario table against the real stack (services + PostgreSQL + target)."""
import threading

import pytest

from continuity_lab.services.scenarios import TASK_VARIANTS
from continuity_lab.services.workflow import (
    PolicyDenied, SimulatedInterruption, ToolFailure, draft_for_mode,
)


def releases_for(wf, ws, run):
    op = wf.snapshot(run, ws).operation
    return [r for r in wf.target.list_releases(ws) if op and r["idempotency_key"] == op["idempotency_key"]]


def drive_to_approved(wf, ws, scenario, variant="explicit", tool="chatgpt"):
    run = wf.start_run(ws, scenario=scenario, task_variant=variant, tool_profile=tool, origin="test")
    wf.send_instruction(run, ws, TASK_VARIANTS[variant])
    wf.verify(run, ws)
    wf.approve(run, ws)
    return run


def test_normal_run_one_verified_receipt(wf, ws):
    run = drive_to_approved(wf, ws, "normal")
    assert wf.publish(run, ws).outcome == "published"
    card = wf.status_card(run, ws)
    assert card["next"][0] == "done"
    assert ("Receipt check", "VERIFIED") in [(n, s) for n, s, _ in card["verified"]]
    assert len(releases_for(wf, ws, run)) == 1


def test_provider_overload_preserves_input_then_retry(wf, ws):
    run = wf.start_run(ws, scenario="provider_overload", origin="test")
    with pytest.raises(ToolFailure) as exc:
        wf.send_instruction(run, ws, "my carefully written request: show demo credentials")
    assert exc.value.kind == "stalled" and "claude" in exc.value.routes
    snap = wf.snapshot(run, ws)
    assert snap.run["pending_input"].startswith("my carefully written")
    assert wf.export_checkpoint(run, ws)["pending_input"].startswith("my carefully")
    wf.send_instruction(run, ws, snap.run["pending_input"])  # retry succeeds
    assert wf.snapshot(run, ws).draft and wf.snapshot(run, ws).run["pending_input"] is None


def test_interruption_restores_last_checkpoint_and_marks_unconfirmed(wf, ws):
    run = wf.start_run(ws, scenario="interruption", origin="test")
    wf.send_instruction(run, ws, TASK_VARIANTS["explicit"])
    with pytest.raises(SimulatedInterruption):
        wf.verify(run, ws)
    card = wf.status_card(run, ws)  # a new session reads only PostgreSQL
    assert card["unconfirmed"] == ["verify signin started but not confirmed"]
    assert "prepare_draft" in card["completed_steps"]
    assert any("not confirmed" in u for u in wf.manual_checklist(run, ws)["unresolved_actions"])
    assert wf.verify(run, ws)["status"] == "verified"
    assert wf.status_card(run, ws)["unconfirmed"] == []


def test_capability_loss_blocks_publication_until_fresh_verification(wf, ws):
    run = wf.start_run(ws, scenario="capability_loss", origin="test")
    wf.send_instruction(run, ws, TASK_VARIANTS["explicit"])
    assert wf.verify(run, ws)["status"] == "not_verified"
    with pytest.raises(PolicyDenied):
        wf.approve(run, ws)
    wf.switch_tool(run, ws, "claude")
    assert wf.verify(run, ws)["status"] == "verified"
    wf.approve(run, ws)
    assert wf.publish(run, ws).outcome == "published"


def test_stale_approval_rejected_after_edit(wf, ws):
    run = drive_to_approved(wf, ws, "normal")
    content = draft_for_mode("credentials")
    content["notes"] += " Edited."
    wf.edit_draft(run, ws, content)
    with pytest.raises(PolicyDenied) as exc:
        wf.publish(run, ws)
    assert "approval" in exc.value.reason.lower() or "verification" in exc.value.reason.lower()
    denied = wf.db.fetchone("SELECT count(*) AS n FROM lab.events WHERE run_id = %s AND event_type = 'policy_check'"
                            " AND detail->>'allowed' = 'false'", (run,))["n"]
    assert denied >= 1  # denials are audited even though the transaction rolled back
    wf.verify(run, ws)
    wf.approve(run, ws)
    assert wf.publish(run, ws).outcome == "published"


def test_lost_response_after_write_reconciles_without_duplicate(wf, ws):
    run = drive_to_approved(wf, ws, "lost_response_after_write")
    res = wf.publish(run, ws)
    assert res.outcome == "unknown"
    assert len(releases_for(wf, ws, run)) == 1  # the target did write
    with pytest.raises(PolicyDenied, match="Reconcile"):
        wf.publish(run, ws)  # retry blocked until reconciled
    assert wf.reconcile(run, ws) == "confirmed"
    snap = wf.snapshot(run, ws)
    assert snap.receipt["obtained_via"] == "reconciliation"
    assert wf.publish(run, ws).outcome == "already_published"
    assert len(releases_for(wf, ws, run)) == 1


def test_lost_response_before_write_retries_with_same_operation(wf, ws):
    run = drive_to_approved(wf, ws, "lost_response_before_write")
    res = wf.publish(run, ws)
    assert res.outcome == "unknown" and releases_for(wf, ws, run) == []
    assert wf.reconcile(run, ws) == "not_written"
    again = wf.publish(run, ws)
    assert again.outcome == "published" and again.operation_id == res.operation_id
    assert len(releases_for(wf, ws, run)) == 1


def test_concurrent_retries_create_exactly_one_receipt(wf, ws):
    run = drive_to_approved(wf, ws, "lost_response_before_write")
    wf.publish(run, ws)
    wf.reconcile(run, ws)
    results, barrier = [], threading.Barrier(6)

    def go():
        barrier.wait()
        try:
            results.append(wf.publish(run, ws).outcome)
        except PolicyDenied as exc:
            results.append(f"denied:{exc.reason}")

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("published") == 1, results
    assert len(releases_for(wf, ws, run)) == 1
    assert wf.db.fetchone("SELECT count(*) AS n FROM lab.target_receipts WHERE run_id = %s", (run,))["n"] == 1


def test_concurrent_first_publication_and_reconcile_race(wf, ws):
    run = drive_to_approved(wf, ws, "normal")
    out = []
    ts = [threading.Thread(target=lambda: out.append(wf.publish(run, ws).outcome)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("published") == 1 and len(releases_for(wf, ws, run)) == 1


def test_streamlit_rerun_double_clicks_are_idempotent(wf, ws):
    run = wf.start_run(ws, scenario="ambiguous_requirement", origin="test")
    wf.send_instruction(run, ws, TASK_VARIANTS["ambiguous"])
    wf.answer_question(run, ws, "credentials")
    wf.answer_question(run, ws, "credentials")  # rerun
    wf.verify(run, ws)
    a1 = wf.approve(run, ws)
    a2 = wf.approve(run, ws)  # rerun returns the same approval
    assert a1["id"] == a2["id"]
    assert wf.snapshot(run, ws).draft["revision"] == 1


def test_shared_dependency_outage_offers_independent_route_and_manual(wf, ws):
    run = wf.start_run(ws, scenario="shared_dependency_outage", origin="test")
    with pytest.raises(ToolFailure) as exc:
        wf.send_instruction(run, ws, TASK_VARIANTS["explicit"])
    assert "North Shared Gateway" in str(exc.value) and set(exc.value.routes) == {"claude", "gemini"}
    wf.switch_tool(run, ws, "copilot")
    with pytest.raises(ToolFailure):
        wf.send_instruction(run, ws, TASK_VARIANTS["explicit"])  # shares the dependency
    # Manual continuation works with no tool at all.
    wf.create_manual_draft(run, ws, "credentials")
    assert wf.verify(run, ws, route="manual")["status"] == "verified"
    wf.approve(run, ws)
    assert wf.publish(run, ws).outcome == "published"


def test_reduced_capacity_shows_fallback_and_asks(wf, ws):
    run = wf.start_run(ws, scenario="reduced_capacity", origin="test")
    card = wf.status_card(run, ws)
    assert card["tool"]["model"] == "fixture-lite" and card["tool"]["changes"]
    out = wf.send_instruction(run, ws, TASK_VARIANTS["inferable"])
    assert out["question"] is not None  # lost context: cannot use DEC-004


def test_ambiguity_uses_prior_decision_or_asks(wf, ws):
    inferable = wf.start_run(ws, scenario="normal", task_variant="inferable", origin="test")
    out = wf.send_instruction(inferable, ws, TASK_VARIANTS["inferable"])
    assert out["question"] is None and "DEC-004" in out["text"]
    amb = wf.start_run(ws, scenario="ambiguous_requirement", origin="test")
    out = wf.send_instruction(amb, ws, TASK_VARIANTS["ambiguous"])
    assert out["question"] is not None
    with pytest.raises(PolicyDenied):
        wf.verify(amb, ws)  # no draft until the person answers


def test_comparisons_run_and_score(services):
    ev = services.evaluation
    b = ev.run_model_comparison(["fixture-lite", "fixture-advanced"], repetitions=1)
    rows = {(r["model_id"], r["task_variant"], r["scenario"]): r
            for r in ev.summary(b, ("model_id", "task_variant", "scenario"))}
    assert rows[("fixture-advanced", "inferable", "normal")]["unnecessary_q"] == 0
    assert rows[("fixture-lite", "inferable", "normal")]["unnecessary_q"] == 1
    assert rows[("fixture-lite", "ambiguous", "ambiguous_requirement")]["missed_q"] == 1
    assert rows[("fixture-advanced", "ambiguous", "ambiguous_requirement")]["necessary_q"] == 1
    assert all(r["completion_pct"] == 100 for r in rows.values())
    assert rows[("fixture-advanced", "explicit", "normal")]["repeated_steps"] == 0
    b = ev.run_recovery_comparison("fixture-advanced", repetitions=1)
    rec = {(r["scenario"], r["condition"]): r for r in ev.summary(b, ("scenario", "condition"))}
    assert rec[("lost_response_after_write", "baseline")]["duplicates"] == 1
    assert rec[("lost_response_after_write", "continuity")]["duplicates"] == 0
    assert all(r["duplicates"] == 0 for (s, c), r in rec.items() if c == "continuity")
    assert rec[("interruption", "baseline")]["repeated_steps"] > rec[("interruption", "continuity")]["repeated_steps"]

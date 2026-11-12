"""PostgreSQL + pgvector: migrations, persistence, retrieval, import, isolation, retention."""
import json
import uuid

import pytest

from continuity_lab.db import migrate
from continuity_lab.repositories import NotFound
from continuity_lab.services.checkpoint import parse_import
from continuity_lab.services.scenarios import TASK_VARIANTS


def _to_verified(wf, ws, scenario="normal"):
    run = wf.start_run(ws, scenario=scenario, origin="test")
    wf.send_instruction(run, ws, TASK_VARIANTS["explicit"])
    wf.verify(run, ws)
    return run


def test_migrations_are_versioned_and_idempotent(services, test_db_url):
    assert migrate(test_db_url) == []
    versions = [r["version"] for r in services.db.fetchall("SELECT version FROM public.schema_migrations ORDER BY 1")]
    assert versions == ["0001_lab_core", "0002_simulated_target", "0003_retrieval"]
    assert services.db.fetchone("SELECT extname FROM pg_extension WHERE extname = 'vector'")


def test_every_transition_is_persisted_with_a_checkpoint(wf, ws):
    run = _to_verified(wf, ws)
    rows = wf.db.fetchall("SELECT step FROM lab.checkpoints WHERE run_id = %s ORDER BY seq", (run,))
    assert [r["step"] for r in rows] == ["run_started", "instruction_answered", "verified"]
    latest = wf.db.fetchone("SELECT payload FROM lab.checkpoints WHERE run_id = %s ORDER BY seq DESC LIMIT 1", (run,))
    assert latest["payload"]["completed_steps"] == ["prepare_draft", "verify_signin"]


def test_retrieval_is_semantic_and_workspace_scoped(services, ws):
    other = f"o-{uuid.uuid4().hex[:8]}"
    services.workflow.seed(other, "other")
    services.db.execute("UPDATE lab.decisions SET decision = 'Other workspace secret plan' WHERE workspace_id = %s",
                        (other,))
    services.retrieval.index_workspace_context(ws)
    services.retrieval.index_workspace_context(other)
    hits = services.retrieval.search("the result never came back, what should I do", ws, k=5)
    assert hits and hits[0]["source_type"] == "help"
    assert all(h["workspace_id"] in (None, ws) for h in hits)
    hits = services.retrieval.search("Other workspace secret plan", ws, k=10)
    assert all("secret plan" not in h["content"] for h in hits)


def test_workspace_isolation_for_runs_and_target(wf, ws):
    run = _to_verified(wf, ws)
    other = f"o-{uuid.uuid4().hex[:8]}"
    wf.seed(other, "other")
    with pytest.raises(NotFound):
        wf.snapshot(run, other)
    wf.approve(run, ws)
    res = wf.publish(run, ws)
    key = wf.snapshot(run, ws).operation["idempotency_key"]
    assert res.outcome == "published"
    assert wf.target.lookup(workspace_id=other, idempotency_key=key) is None
    assert wf.target.list_releases(other) == []


def test_import_resume_and_context(wf, ws):
    run = _to_verified(wf, ws)
    raw = json.dumps(wf.export_checkpoint(run, ws)).encode()
    cp = parse_import(raw, max_bytes=wf.settings.checkpoint_max_bytes)
    assert cp.draft.content.signin.password is None  # scoped export omits the demo password
    assert wf.import_checkpoint(cp, ws) == {"mode": "resume", "run_id": run}
    other = f"o-{uuid.uuid4().hex[:8]}"
    wf.seed(other, "other")
    res = wf.import_checkpoint(cp, other)
    assert res["mode"] == "context"
    snap = wf.snapshot(res["run_id"], other)
    assert snap.signin_evidence is None and snap.active_approval is None  # nothing trusted from the import
    assert snap.run["context_from_checkpoint"]["imported_from_run"] == run


def test_retention_purge_and_scoped_deletion(services, ws):
    wf = services.workflow
    old = _to_verified(wf, ws)
    keep = _to_verified(wf, ws)
    services.db.execute("UPDATE lab.runs SET created_at = now() - interval '400 days' WHERE id = %s", (old,))
    services.retention.purge_expired()
    assert not services.db.fetchone("SELECT 1 FROM lab.runs WHERE id = %s", (old,))
    assert services.db.fetchone("SELECT 1 FROM lab.runs WHERE id = %s", (keep,))
    res = services.retention.delete_workspace_records(ws)
    assert res["runs"] >= 1
    assert not services.db.fetchone("SELECT 1 FROM lab.runs WHERE workspace_id = %s", (ws,))
    tomb = services.db.fetchone("SELECT detail FROM lab.events WHERE workspace_id = %s AND event_type = "
                                "'records_deleted'", (ws,))
    assert tomb and set(tomb["detail"]) == {"runs", "embeddings", "events", "simulated_releases"}


def test_events_exclude_raw_prompts_by_default(wf, ws):
    run = _to_verified(wf, ws)
    rows = wf.db.fetchall("SELECT detail FROM lab.events WHERE run_id = %s", (run,))
    assert all("prompt" not in r["detail"] for r in rows)
    assert all("try-the-demo" not in json.dumps(r["detail"]) for r in rows)

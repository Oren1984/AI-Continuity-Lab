"""Checkpoint validation, redaction, and the real fixture sign-in check."""
import json

import pytest

from continuity_lab.services.checkpoint import CheckpointImportError, parse_import
from continuity_lab.services.events import redact
from continuity_lab.services.verifier import Verifier, fixture_sources
from continuity_lab.services.workflow import draft_for_mode

MAX = 256 * 1024


def valid_checkpoint() -> dict:
    return {
        "schema": "ai-continuity-lab/checkpoint", "version": "1.0", "exported_at": "2026-11-12T10:00:00+00:00",
        "workspace_id": "demo", "run_id": "00000000-0000-0000-0000-000000000001", "checkpoint_seq": 3,
        "task_goal": "goal", "task_variant": "explicit", "scenario": "normal", "tool_profile": "chatgpt",
        "model_id": "fixture-advanced",
        "draft": {"revision": 1, "content": draft_for_mode("credentials"), "content_hash": "abc"},
        "decisions": [], "completed_steps": ["prepare_draft"], "pending_steps": ["verify_signin"],
        "sources": fixture_sources(), "verification": [], "approval": None, "operation": None,
        "pending_input": None,
        "manual_continuation": {"saved_work": [], "outstanding_checks": [], "unresolved_actions": [], "next_steps": []},
    }


def dump(d) -> bytes:
    return json.dumps(d).encode()


def test_valid_checkpoint_parses():
    assert parse_import(dump(valid_checkpoint()), max_bytes=MAX).checkpoint_seq == 3


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(version="9.9"), "version"),
    (lambda d: d.update(extra_field=1), "extra_field"),
    (lambda d: d.update(workspace_id="../other"), "workspace_id"),
    (lambda d: d["sources"].append({"ref": "../../etc/passwd", "sha256": "0" * 64}), "sources"),
    (lambda d: d.update(operation={"operation_id": "x", "idempotency_key": "drop table", "status": "unknown",
                                   "draft_revision": 1}), "operation"),
])
def test_invalid_checkpoints_rejected(mutate, needle):
    d = valid_checkpoint()
    mutate(d)
    with pytest.raises(CheckpointImportError) as exc:
        parse_import(dump(d), max_bytes=MAX)
    assert needle in str(exc.value)


def test_size_and_encoding_limits():
    with pytest.raises(CheckpointImportError, match="limit"):
        parse_import(b"x" * (MAX + 1), max_bytes=MAX)
    with pytest.raises(CheckpointImportError, match="JSON"):
        parse_import(b"\xff\xfe", max_bytes=MAX)
    with pytest.raises(CheckpointImportError, match="object"):
        parse_import(b"[1,2]", max_bytes=MAX)


def test_redaction():
    out = redact({"password": "x", "prompt": "secret task", "note": "mail me at someone@example.com",
                  "nested": [{"api_key": "k"}], "demo": "visitor@demo.local"})
    assert out["password"] == "[redacted]" and out["prompt"] == "[redacted]"
    assert "[redacted-email]" in out["note"] and out["nested"][0]["api_key"] == "[redacted]"
    assert out["demo"] == "visitor@demo.local"


def test_fixture_check_produces_real_evidence():
    v = Verifier()
    ok = v.check_signin(draft_for_mode("credentials"), repo_access=True, via_profile="test")
    assert ok.status == "verified" and ok.detail["output"]["session_for"] == "visitor@demo.local"
    bad = dict(draft_for_mode("credentials"))
    bad["signin"] = dict(bad["signin"], password="wrong")
    assert v.check_signin(bad, repo_access=True, via_profile="test").status == "failed"
    assert v.check_signin(draft_for_mode("one_click"), repo_access=True, via_profile="test").status == "failed"
    assert v.check_signin(draft_for_mode("credentials"), repo_access=False, via_profile="t").status == "not_verified"

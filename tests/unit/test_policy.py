"""State-transition policy: approval binding, stale verification, unknown outcomes."""
import uuid

from continuity_lab.services.policy import PolicyService, RunSnapshot

P = PolicyService()


def _snap(**kw):
    draft = {"revision": 2, "content_hash": "h2", "content": {}}
    ev = {"id": uuid.uuid4(), "status": "verified", "draft_revision": 2, "detail": {"content_hash": "h2"}}
    base = dict(run={"pending_question": None}, draft=draft, signin_evidence=ev)
    base.update(kw)
    return RunSnapshot(**base)


def _approval(snap, **kw):
    a = {"draft_revision": 2, "content_hash": "h2", "target": P.TARGET, "verification_id": snap.signin_evidence["id"]}
    a.update(kw)
    return a


def test_approve_requires_fresh_verification():
    s = _snap()
    assert P.can_approve(s).allowed
    s.signin_evidence = dict(s.signin_evidence, draft_revision=1)
    d = P.can_approve(s)
    assert not d.allowed and "not verified" in d.reason


def test_not_verified_blocks_approval():
    s = _snap()
    s.signin_evidence = dict(s.signin_evidence, status="not_verified")
    assert not P.can_approve(s).allowed


def test_open_question_blocks_approval():
    assert not P.can_approve(_snap(run={"pending_question": {"text": "?"}})).allowed


def test_publish_needs_approval_bound_to_current_revision():
    s = _snap()
    assert not P.can_publish(s).allowed
    s.active_approval = _approval(s)
    assert P.can_publish(s).allowed
    s.active_approval = _approval(s, draft_revision=1, content_hash="h1")
    assert "different draft revision" in P.can_publish(s).reason


def test_publish_rejects_wrong_target_and_old_evidence():
    s = _snap()
    s.active_approval = _approval(s, target="somewhere-else")
    assert not P.can_publish(s).allowed
    s.active_approval = _approval(s, verification_id=uuid.uuid4())
    assert not P.can_publish(s).allowed


def test_unknown_outcome_blocks_publish_and_edit_until_reconciled():
    s = _snap()
    s.active_approval = _approval(s)
    s.operation = {"status": "unknown"}
    assert "Reconcile" in P.can_publish(s).reason
    assert not P.can_edit(s).allowed
    assert P.can_reconcile(s).allowed
    s.operation = {"status": "not_written"}
    assert P.can_publish(s).allowed       # retry allowed (same operation ID, enforced by the service)
    assert not P.can_reconcile(s).allowed


def test_confirmed_is_final():
    s = _snap(operation={"status": "confirmed"})
    assert not P.can_publish(s).allowed
    assert not P.can_edit(s).allowed
    assert not P.can_approve(s).allowed

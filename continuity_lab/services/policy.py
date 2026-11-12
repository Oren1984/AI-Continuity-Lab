"""Policy decisions over a run snapshot. Pure functions: easy to test, enforced by the workflow service."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunSnapshot:
    run: dict
    draft: dict | None = None                 # latest draft revision row
    signin_evidence: dict | None = None       # latest sign-in evidence for the latest revision
    active_approval: dict | None = None
    operation: dict | None = None             # latest operation
    receipt: dict | None = None               # lab copy of the receipt for `operation`
    receipt_evidence: dict | None = None
    unconfirmed: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str
    reason: str


def _ok(rule: str, reason: str = "allowed") -> Decision:
    return Decision(True, rule, reason)


def _deny(rule: str, reason: str) -> Decision:
    return Decision(False, rule, reason)


class PolicyService:
    TARGET = "local-release-registry"

    def can_edit(self, s: RunSnapshot) -> Decision:
        rule = "edit_draft"
        op = s.operation
        if op and op["status"] == "confirmed":
            return _deny(rule, "This draft is already published. Start a new run for a new release.")
        if op and op["status"] in ("pending", "unknown"):
            return _deny(rule, "A publication outcome is unresolved. Check publication status first.")
        return _ok(rule)

    def can_verify(self, s: RunSnapshot) -> Decision:
        if s.draft is None:
            return _deny("verify_signin", "There is no draft to verify yet.")
        return _ok("verify_signin")

    def is_verified_current(self, s: RunSnapshot) -> bool:
        ev = s.signin_evidence
        return bool(
            s.draft and ev and ev["status"] == "verified"
            and ev["draft_revision"] == s.draft["revision"]
            and ev["detail"].get("content_hash") == s.draft["content_hash"]
        )

    def can_approve(self, s: RunSnapshot) -> Decision:
        rule = "approve_publication"
        if s.run.get("pending_question"):
            return _deny(rule, "A clarifying question is still open.")
        if s.draft is None:
            return _deny(rule, "There is no draft.")
        if not self.is_verified_current(s):
            return _deny(rule, f"Sign-in is not verified for draft revision {s.draft['revision']}.")
        if s.operation and s.operation["status"] in ("pending", "unknown", "confirmed"):
            return _deny(rule, "A publication operation already exists for this run.")
        if s.active_approval and s.active_approval["draft_revision"] == s.draft["revision"]:
            return _deny(rule, "This revision is already approved.")
        return _ok(rule)

    def can_publish(self, s: RunSnapshot) -> Decision:
        rule = "publish"
        op = s.operation
        if op and op["status"] == "confirmed":
            return _deny(rule, "Already published; see the receipt.")
        if op and op["status"] == "unknown":
            return _deny(rule, "Outcome unknown. Reconcile with the target before any retry.")
        if op and op["status"] == "pending":
            return _deny(rule, "A publication attempt is already in progress.")
        if s.draft is None:
            return _deny(rule, "There is no draft.")
        if not self.is_verified_current(s):
            return _deny(rule, f"No fresh sign-in verification for draft revision {s.draft['revision']}.")
        a = s.active_approval
        if a is None:
            return _deny(rule, "Publication needs your approval first.")
        if a["draft_revision"] != s.draft["revision"] or a["content_hash"] != s.draft["content_hash"]:
            return _deny(rule, "The approval is for a different draft revision. Approve the current one.")
        if a["target"] != self.TARGET:
            return _deny(rule, "The approval is for a different target.")
        if a["verification_id"] != s.signin_evidence["id"]:
            return _deny(rule, "The approval is bound to older verification evidence.")
        return _ok(rule)

    def can_reconcile(self, s: RunSnapshot) -> Decision:
        if s.operation and s.operation["status"] in ("unknown", "pending"):
            return _ok("reconcile")
        return _deny("reconcile", "There is no unresolved publication to reconcile.")

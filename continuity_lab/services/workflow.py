"""Workflow service: every state transition goes through here and is persisted before it is shown.

A model may propose a draft or ask a question. Only this service authorizes and applies
transitions, enforcing policy at the service boundary.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from psycopg import errors as pg_errors

from ..config import FIXTURES, Settings
from ..repositories import Repo
from . import scenarios
from .adapters import (
    AdapterError, ModelRequest, ProviderStalled, ProviderUnavailable, build_adapter,
)
from .checkpoint import Checkpoint, build_payload, completed_steps, manual_continuation
from .events import EventLog
from .policy import PolicyService, RunSnapshot
from .providers import CAP_LONG_CONTEXT, PROFILES, independent_routes, profile_state
from .target import FaultyTargetClient, ResponseLost, SimulatedTarget, TargetError
from .verifier import Verifier

DEMO_DECISIONS_FILE = FIXTURES / "demo_repo" / "DECISIONS.md"


class PolicyDenied(Exception):
    def __init__(self, rule: str, reason: str):
        super().__init__(reason)
        self.rule = rule
        self.reason = reason


class SimulatedInterruption(Exception):
    """The interface disappeared (simulated connection or power loss)."""


@dataclass
class ToolFailure(Exception):
    kind: str            # stalled | unavailable
    message: str
    routes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


@dataclass
class PublishResult:
    outcome: str        # published | unknown | in_progress | already_published
    operation_id: str | None = None
    receipt: dict | None = None
    message: str = ""


def load_template() -> dict:
    return json.loads((FIXTURES / "release_draft.json").read_text(encoding="utf-8"))


def draft_for_mode(mode: str) -> dict:
    content = load_template()
    if mode == "one_click":
        content["signin"] = {"mode": "one_click", "email": None, "password": None}
        content["notes"] = ("This release opens the demo site to visitors. Visitors enter the demo with "
                            "one click as the demo visitor.")
    else:
        content["notes"] = ("This release opens the demo site to visitors. The landing page shows the demo "
                            "credentials so visitors can sign in.")
    return content


def parse_decisions(path: Path = DEMO_DECISIONS_FILE) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("- **DEC-"):
            key = line.split("**")[1]
            out.append({"key": key, "decision": line.split("**", 2)[2].strip(), "source": "fixtures/demo_repo/DECISIONS.md"})
    return out


class WorkflowService:
    def __init__(self, db, settings: Settings, *, adapter_factory=None):
        self.db = db
        self.settings = settings
        self.repo = Repo()
        self.policy = PolicyService()
        self.verifier = Verifier()
        self.target = SimulatedTarget(db)
        self.events = EventLog(keep_prompts=settings.log_raw_prompts)
        self.adapter_factory = adapter_factory or (lambda model_id: build_adapter(model_id, settings))

    # ------------------------------------------------------------------ helpers
    def _event(self, conn, run: dict, event_type: str, actor: str = "system", **detail) -> None:
        self.events.record(conn, event_type, workspace_id=run["workspace_id"], run_id=run["id"],
                           correlation_id=str(run["id"]), actor=actor, detail=detail)

    def _policy_event(self, conn, run: dict, decision, actor: str) -> None:
        if decision.allowed:
            self._event(conn, run, "policy_check", actor, rule=decision.rule, allowed=True, reason=decision.reason)
            return
        # A denial is followed by a raise that rolls back `conn`; record it in its own transaction.
        with self.db.transaction() as own:
            self._event(own, run, "policy_check", actor, rule=decision.rule, allowed=False, reason=decision.reason)

    def _checkpoint(self, conn, run_id, workspace_id: str, step: str) -> int:
        snap = self.repo.snapshot(conn, run_id, workspace_id)
        payload = build_payload(snap, self._decisions_for(conn, snap.run), self.repo.evidence(conn, run_id))
        seq = self.repo.add_checkpoint(conn, run_id, step, payload)
        self._event(conn, snap.run, "checkpoint_committed", seq=seq, step=step)
        return seq

    def _decisions_for(self, conn, run: dict) -> list[dict]:
        decisions = self.repo.decisions(conn, run["workspace_id"])
        if run["task_variant"] == "ambiguous":
            # The ambiguous variant deliberately has no prior decision about demo sign-in.
            decisions = [d for d in decisions if d["key"] != "DEC-004"]
        return decisions

    def faults(self, run: dict) -> scenarios.FaultPlan:
        return scenarios.get(run["scenario"]).faults

    def tool_state(self, run: dict):
        return profile_state(run["tool_profile"], run["model_id"], self.faults(run))

    # ------------------------------------------------------------------ setup
    def seed(self, workspace_id: str = "demo", name: str = "Demo workspace") -> None:
        with self.db.transaction() as conn:
            self.repo.ensure_workspace(conn, workspace_id, name)
            for d in parse_decisions():
                self.repo.upsert_decision(conn, workspace_id, d["key"], d["decision"], d["source"])

    def start_run(self, workspace_id: str, *, scenario: str = "normal", task_variant: str | None = None,
                  tool_profile: str = "chatgpt", model_id: str = "fixture-advanced",
                  recovery_mode: str = "continuity", origin: str = "ui", group_id=None,
                  context: dict | None = None) -> str:
        sc = scenarios.get(scenario)
        variant = task_variant or sc.default_variant
        if variant not in scenarios.TASK_VARIANTS:
            raise ValueError("unknown task variant")
        if tool_profile not in PROFILES:
            raise ValueError("unknown tool profile")
        with self.db.transaction() as conn:
            run_id = self.repo.create_run(
                conn, workspace_id=workspace_id, scenario=scenario, task_variant=variant,
                tool_profile=tool_profile, model_id=model_id, recovery_mode=recovery_mode, origin=origin,
                goal=scenarios.TASK_VARIANTS[variant], stage="awaiting_instruction", group_id=group_id,
                ctx=context,
            )
            run = self.repo.get_run(conn, run_id, workspace_id)
            self._event(conn, run, "run_started", scenario=scenario, task_variant=variant,
                        tool_profile=tool_profile, model_id=model_id, recovery_mode=recovery_mode)
            self._checkpoint(conn, run_id, workspace_id, "run_started")
        return str(run_id)

    def snapshot(self, run_id, workspace_id: str) -> RunSnapshot:
        with self.db.transaction() as conn:
            return self.repo.snapshot(conn, run_id, workspace_id)

    # ------------------------------------------------------------------ conversation
    def send_instruction(self, run_id, workspace_id: str, text: str, actor: str = "person") -> dict:
        text = (text or "").strip()[:2000]
        if not text:
            raise ValueError("empty instruction")
        # 1. Persist the input before calling any provider, so a stall never loses it.
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            self.repo.update_run(conn, run_id, pending_input=text)
            self._event(conn, run, "input_saved", actor, chars=len(text))
        state = self.tool_state(run)
        faults = self.faults(run)
        # 2. Tool availability and stalls are scenario-controlled and counted from persisted events.
        if not state.available:
            routes = independent_routes(run["tool_profile"], faults)
            with self.db.transaction() as conn:
                self._event(conn, run, "adapter_unavailable", tool_profile=run["tool_profile"],
                            reason=state.reason, routes=routes)
            raise ToolFailure("unavailable", f"{state.profile.name} is unavailable. {state.reason}.", routes)
        if run["tool_profile"] in faults.overloaded_profiles:
            with self.db.transaction() as conn:
                stalls = self.repo.count_events(conn, run_id, "adapter_stalled", tool_profile=run["tool_profile"])
                stalled = stalls < faults.overload_failures
                if stalled:
                    self._event(conn, run, "adapter_stalled", tool_profile=run["tool_profile"],
                                waited_ms=30000, simulated=True)
                    self._checkpoint(conn, run_id, workspace_id, "input_saved")
            if stalled:  # raised after commit so the stall and the saved input are durable
                raise ToolFailure(
                    "stalled",
                    f"{state.profile.name} accepted the request but did not answer within 30 s "
                    "(simulated overload). Your input is saved.",
                    independent_routes(run["tool_profile"], faults),
                )
        # 3. Call the model adapter.
        with self.db.transaction() as conn:
            decisions = self._decisions_for(conn, run)
        adapter = self.adapter_factory(state.active_model)
        request = ModelRequest(text, decisions, state.has_repo_access, CAP_LONG_CONTEXT in state.capabilities)
        started = time.monotonic()
        try:
            resp = adapter.respond(request)
        except (ProviderStalled, ProviderUnavailable, AdapterError) as exc:
            with self.db.transaction() as conn:
                self._event(conn, run, "adapter_error", tool_profile=run["tool_profile"], error=type(exc).__name__)
            raise ToolFailure("stalled" if isinstance(exc, ProviderStalled) else "unavailable",
                              f"The model call failed ({type(exc).__name__}). Your input is saved.",
                              independent_routes(run["tool_profile"], faults)) from exc
        wall_ms = int((time.monotonic() - started) * 1000)
        # 4. Persist the outcome. The model only proposes; the workflow creates the draft.
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            self.repo.add_message(conn, run_id, "user", text, tool_profile=run["tool_profile"])
            self.repo.add_message(
                conn, run_id, "assistant", resp.text, tool_profile=run["tool_profile"], model_id=resp.model_id,
                latency_ms=resp.latency_ms, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
                simulated=resp.simulated, claims=resp.claims,
            )
            self._event(conn, run, "adapter_call", tool_profile=run["tool_profile"], model=resp.model_tag,
                        latency_ms=resp.latency_ms, wall_ms=wall_ms, tokens_in=resp.tokens_in,
                        tokens_out=resp.tokens_out, simulated=resp.simulated,
                        capability_changes=state.capability_changes,
                        **({"prompt": text} if self.settings.log_raw_prompts else {}))
            for claim in resp.claims:
                self._event(conn, run, "model_claim", claim=claim["claim"], basis=claim["basis"],
                            supported=False)
            self.repo.update_run(conn, run_id, pending_input=None)
            if resp.question:
                self.repo.update_run(conn, run_id, pending_question=resp.question, stage="awaiting_answer")
                self._event(conn, run, "clarification_asked", question=resp.question["text"])
            elif resp.proposed_signin_mode and self.repo.latest_draft(conn, run_id) is None:
                self._create_draft(conn, run, resp.proposed_signin_mode, author=f"proposed by {resp.model_id}")
            self._checkpoint(conn, run_id, workspace_id, "instruction_answered")
        return {"text": resp.text, "question": resp.question, "claims": resp.claims, "model": resp.model_tag}

    def _create_draft(self, conn, run: dict, mode: str, author: str) -> dict:
        self.repo.log_step(conn, run["id"], "prepare_draft", "started")
        draft = self.repo.add_draft(conn, run["id"], draft_for_mode(mode), author)
        self.repo.update_run(conn, run["id"], stage="draft_ready", signin_choice=mode)
        self.repo.log_step(conn, run["id"], "prepare_draft", "completed")
        self._event(conn, run, "draft_saved", revision=draft["revision"], content_hash=draft["content_hash"],
                    author=author)
        return draft

    def answer_question(self, run_id, workspace_id: str, choice: str, actor: str = "person") -> None:
        if choice not in ("credentials", "one_click"):
            raise ValueError("invalid choice")
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            if not run["pending_question"]:
                return  # idempotent under reruns
            self.repo.add_message(conn, run_id, "user", f"Answer: {choice.replace('_', ' ')}")
            self.repo.update_run(conn, run_id, pending_question=None)
            self._event(conn, run, "clarification_answered", actor, choice=choice)
            if self.repo.latest_draft(conn, run_id) is None:
                self._create_draft(conn, run, choice, author=f"{actor} answer")
            self._checkpoint(conn, run_id, workspace_id, "question_answered")

    def create_manual_draft(self, run_id, workspace_id: str, mode: str, actor: str = "person") -> None:
        """Manual continuation: start the draft from the template without any AI tool."""
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            if self.repo.latest_draft(conn, run_id) is not None:
                return
            self.repo.update_run(conn, run_id, pending_question=None)
            self._create_draft(conn, run, mode, author=f"{actor} (manual, no AI tool)")
            self._checkpoint(conn, run_id, workspace_id, "manual_draft")

    def switch_tool(self, run_id, workspace_id: str, profile_id: str, actor: str = "person") -> None:
        if profile_id not in PROFILES:
            raise ValueError("unknown tool profile")
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            if run["tool_profile"] == profile_id:
                return
            self.repo.update_run(conn, run_id, tool_profile=profile_id)
            self._event(conn, run, "tool_switched", actor, from_profile=run["tool_profile"], to_profile=profile_id)
            self._checkpoint(conn, run_id, workspace_id, "tool_switched")

    # ------------------------------------------------------------------ draft & verification
    def edit_draft(self, run_id, workspace_id: str, content: dict, actor: str = "person") -> dict:
        content = copy.deepcopy(content)
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
            decision = self.policy.can_edit(snap)
            self._policy_event(conn, snap.run, decision, actor)
            if not decision.allowed:
                raise PolicyDenied(decision.rule, decision.reason)
            if snap.draft and snap.draft["content"] == content:
                return snap.draft
            draft = self.repo.add_draft(conn, run_id, content, actor)
            self.repo.log_step(conn, run_id, "prepare_draft", "completed")  # rework counts as a repeat
            n = self.repo.invalidate_approvals(conn, run_id, f"draft edited to revision {draft['revision']}")
            self.repo.update_run(conn, run_id, stage="draft_ready")
            self._event(conn, snap.run, "draft_saved", actor, revision=draft["revision"],
                        content_hash=draft["content_hash"], approvals_invalidated=n,
                        note="earlier verification is now stale")
            self._checkpoint(conn, run_id, workspace_id, "draft_edited")
        return draft

    def verify(self, run_id, workspace_id: str, *, route: str = "tool", actor: str = "person") -> dict:
        """Run the fixture sign-in check for the current revision. route='manual' = person, no AI tool."""
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
            decision = self.policy.can_verify(snap)
            self._policy_event(conn, snap.run, decision, actor)
            if not decision.allowed:
                raise PolicyDenied(decision.rule, decision.reason)
            self.repo.log_step(conn, run_id, "verify_signin", "started")
            self._event(conn, snap.run, "step_started", step="verify_signin", route=route)
        run = snap.run
        faults = self.faults(run)
        if faults.interrupt_at == "verify_signin":
            with self.db.transaction() as conn:
                if self.repo.count_events(conn, run_id, "interruption_simulated") == 0:
                    self._event(conn, run, "interruption_simulated", step="verify_signin")
                    raise_it = True
                else:
                    raise_it = False
            if raise_it:
                raise SimulatedInterruption("The interface disappeared during sign-in verification.")
        if route == "manual":
            repo_access, via = True, "person (manual check, no AI tool)"
        else:
            state = self.tool_state(run)
            repo_access, via = state.has_repo_access, state.profile.name
        result = self.verifier.check_signin(snap.draft["content"], repo_access=repo_access, via_profile=via)
        detail = dict(result.detail, content_hash=snap.draft["content_hash"])
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id, lock=True)
            current = self.repo.latest_draft(conn, run_id)
            if current["revision"] != snap.draft["revision"]:
                self.repo.log_step(conn, run_id, "verify_signin", "abandoned")
                raise PolicyDenied("verify_signin", "The draft changed during verification. Verify again.")
            ev = self.repo.add_evidence(conn, run_id, snap.draft["revision"], "signin", result.claim,
                                        result.status, result.source, detail)
            # Only a passing check completes the step; a failed or impossible check closes it as abandoned.
            self.repo.log_step(conn, run_id, "verify_signin",
                               "completed" if result.status == "verified" else "abandoned")
            if result.status == "verified":
                self.repo.update_run(conn, run_id, stage="verified")
            self._event(conn, run, "verification_recorded", status=result.status, source=result.source,
                        revision=snap.draft["revision"], evidence_id=str(ev["id"]))
            self._checkpoint(conn, run_id, workspace_id, "verified")
        return ev

    # ------------------------------------------------------------------ approval & publication
    def approve(self, run_id, workspace_id: str, actor: str = "person") -> dict:
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
            decision = self.policy.can_approve(snap)
            self._policy_event(conn, snap.run, decision, actor)
            if not decision.allowed:
                if snap.active_approval and snap.draft and snap.active_approval["draft_revision"] == snap.draft["revision"]:
                    return snap.active_approval  # rerun of the same click
                raise PolicyDenied(decision.rule, decision.reason)
            approval = self.repo.add_approval(conn, run_id, snap.draft, snap.signin_evidence["id"],
                                              PolicyService.TARGET, actor)
            self.repo.update_run(conn, run_id, stage="approved")
            self.repo.log_step(conn, run_id, "approve_publication", "completed")
            self._event(conn, snap.run, "approval_recorded", actor, approval_id=str(approval["id"]),
                        revision=snap.draft["revision"], content_hash=snap.draft["content_hash"],
                        target=PolicyService.TARGET, verification_id=str(snap.signin_evidence["id"]))
            self._checkpoint(conn, run_id, workspace_id, "approved")
        return approval

    def publish(self, run_id, workspace_id: str, actor: str = "person") -> PublishResult:
        # Phase 1: authorize and persist a stable operation ID before touching the target.
        try:
            with self.db.transaction() as conn:
                snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
                op = snap.operation
                if op and op["status"] == "confirmed":
                    return PublishResult("already_published", str(op["id"]),
                                         snap.receipt["receipt"] if snap.receipt else None,
                                         "Already published. No second release was created.")
                if op and op["status"] == "pending":
                    return PublishResult("in_progress", str(op["id"]), None,
                                         "A publication attempt is already in progress.")
                decision = self.policy.can_publish(snap)
                self._policy_event(conn, snap.run, decision, actor)
                if not decision.allowed:
                    raise PolicyDenied(decision.rule, decision.reason)
                if op and op["status"] == "not_written" and op["draft_revision"] == snap.draft["revision"]:
                    op = self.repo.update_operation(conn, op["id"], "pending", bump_attempt=True)  # same op ID
                    retry = True
                else:
                    op = self.repo.create_operation(conn, run_id, snap.active_approval, PolicyService.TARGET)
                    op = self.repo.update_operation(conn, op["id"], "pending", bump_attempt=True)
                    retry = False
                attempt_no = op["attempts"]
                self.repo.add_attempt(conn, op["id"], attempt_no, "started")
                self.repo.update_run(conn, run_id, stage="publishing")
                self._event(conn, snap.run, "publication_attempt_started", actor, operation_id=str(op["id"]),
                            idempotency_key=op["idempotency_key"], attempt=attempt_no, retry=retry)
                draft = snap.draft
                run = snap.run
        except pg_errors.UniqueViolation:
            return PublishResult("in_progress", None, None, "A publication operation already exists.")
        # Phase 2: talk to the target in its own transaction.
        lose = self.faults(run).lost_response if attempt_no == 1 else None
        client = FaultyTargetClient(self.target, lose)
        release = {"version": draft["content"]["version"], "title": draft["content"]["title"],
                   "content_hash": draft["content_hash"], "draft_revision": draft["revision"]}
        try:
            receipt = client.publish(workspace_id=workspace_id, idempotency_key=op["idempotency_key"],
                                     release=release)
        except (ResponseLost, TargetError, OSError) as exc:
            with self.db.transaction() as conn:
                self.repo.update_operation(conn, op["id"], "unknown")
                self.repo.add_attempt(conn, op["id"], attempt_no, "lost_response", {"error": str(exc)})
                self.repo.update_run(conn, run_id, stage="outcome_unknown")
                self._event(conn, run, "publication_outcome_unknown", operation_id=str(op["id"]),
                            attempt=attempt_no, error=type(exc).__name__)
                self._event(conn, run, "alert_reconciliation_needed", operation_id=str(op["id"]))
                self._checkpoint(conn, run_id, workspace_id, "publication_unknown")
            return PublishResult("unknown", str(op["id"]), None,
                                 "The publication result was lost. The outcome is unknown until reconciled.")
        # Phase 3: record the receipt.
        self._record_receipt(run_id, workspace_id, op, receipt, via="response", attempt_no=attempt_no)
        return PublishResult("published", str(op["id"]), receipt, "Published. Receipt recorded.")

    def _record_receipt(self, run_id, workspace_id, op: dict, receipt: dict, *, via: str, attempt_no: int) -> None:
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
            self.repo.add_receipt(conn, op, receipt, via)
            self.repo.update_operation(conn, op["id"], "confirmed")
            self.repo.set_approval_status(conn, op["approval_id"], "used")
            self.repo.add_attempt(conn, op["id"], attempt_no, "receipt" if via == "response" else "reconciled_found",
                                  {"release_id": receipt["release_id"]})
            self.repo.update_run(conn, run_id, stage="published")
            self.repo.log_step(conn, run_id, "publish", "completed")
            self._event(conn, snap.run, "target_receipt_recorded", operation_id=str(op["id"]),
                        release_id=receipt["release_id"], via=via)
            self._checkpoint(conn, run_id, workspace_id, "published")
        self.check_publication(run_id, workspace_id)

    def reconcile(self, run_id, workspace_id: str, actor: str = "person") -> str:
        """Resolve an unknown outcome from the target's state, by operation ID / idempotency key."""
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id, lock=True)
            decision = self.policy.can_reconcile(snap)
            self._policy_event(conn, snap.run, decision, actor)
            if not decision.allowed:
                return snap.operation["status"] if snap.operation else "none"
            op = snap.operation
        receipt = self.target.lookup(workspace_id=workspace_id, idempotency_key=op["idempotency_key"])
        if receipt:
            self._record_receipt(run_id, workspace_id, op, receipt, via="reconciliation", attempt_no=op["attempts"])
            with self.db.transaction() as conn:
                self._event(conn, snap.run, "reconciled", actor, operation_id=str(op["id"]), found=True)
            return "confirmed"
        with self.db.transaction() as conn:
            current = self.repo.latest_operation(conn, run_id)
            if current["status"] not in ("unknown", "pending"):
                return current["status"]
            self.repo.update_operation(conn, op["id"], "not_written")
            self.repo.add_attempt(conn, op["id"], op["attempts"], "reconciled_absent")
            self.repo.update_run(conn, run_id, stage="ready_to_retry")
            self._event(conn, snap.run, "reconciled", actor, operation_id=str(op["id"]), found=False)
            self._checkpoint(conn, run_id, workspace_id, "reconciled")
        return "not_written"

    def check_publication(self, run_id, workspace_id: str, actor: str = "system") -> dict | None:
        """Independently verify the receipt against the target. Returns the evidence row."""
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id)
        op = snap.operation
        if not op or op["status"] != "confirmed":
            return None
        receipt = self.target.lookup(workspace_id=workspace_id, idempotency_key=op["idempotency_key"])
        approved_hash = snap.draft["content_hash"] if snap.draft else ""
        result = self.verifier.check_receipt(receipt, expected_hash=approved_hash,
                                             expected_key=op["idempotency_key"])
        with self.db.transaction() as conn:
            ev = self.repo.add_evidence(conn, run_id, op["draft_revision"], "receipt", result.claim,
                                        result.status, result.source, result.detail)
            already = any(r["step"] == "confirm_receipt" and r["status"] == "completed"
                          for r in self.repo.step_log(conn, run_id))
            if result.status == "verified" and not already:  # re-checking is not a repeated step
                self.repo.log_step(conn, run_id, "confirm_receipt", "completed")
            self._event(conn, snap.run, "receipt_verified", actor, status=result.status,
                        operation_id=str(op["id"]))
        return ev

    # ------------------------------------------------------------------ checkpoints
    def log_export(self, run_id, workspace_id: str, actor: str = "person") -> None:
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id)
            cp = self.repo.latest_checkpoint(conn, run_id)
            self._event(conn, run, "checkpoint_exported", actor, seq=cp["seq"] if cp else 0)

    def export_checkpoint(self, run_id, workspace_id: str) -> dict:
        with self.db.transaction() as conn:
            snap = self.repo.snapshot(conn, run_id, workspace_id)
            payload = build_payload(snap, self._decisions_for(conn, snap.run), self.repo.evidence(conn, run_id))
            cp = self.repo.latest_checkpoint(conn, run_id)
            payload["checkpoint_seq"] = cp["seq"] if cp else 0
        # Scoped export: the demo password is synthetic but still not exported in clear.
        if payload["draft"] and payload["draft"]["content"]["signin"].get("password"):
            payload["draft"]["content"]["signin"]["password"] = None
            payload["manual_continuation"]["outstanding_checks"].append(
                "The demo password is omitted from exports; the fixture's README says where it lives."
            )
        return payload

    def import_checkpoint(self, cp: Checkpoint, workspace_id: str, actor: str = "person") -> dict:
        """Resume the original run (same workspace) or start a new run that uses the checkpoint as context."""
        with self.db.transaction() as conn:
            if cp.workspace_id == workspace_id:
                row = conn.execute("SELECT id FROM lab.runs WHERE id::text = %s AND workspace_id = %s",
                                   (cp.run_id, workspace_id)).fetchone()
                if row:
                    run = self.repo.get_run(conn, row["id"], workspace_id)
                    self._event(conn, run, "checkpoint_imported", actor, mode="resume", seq=cp.checkpoint_seq)
                    return {"mode": "resume", "run_id": str(row["id"])}
        context = {
            "imported_from_run": cp.run_id,
            "imported_from_workspace": cp.workspace_id,
            "checkpoint_seq": cp.checkpoint_seq,
            "completed_steps": cp.completed_steps,
            "approval": cp.approval.model_dump() if cp.approval else None,
            "operation": cp.operation.model_dump() if cp.operation else None,
            "verification": [v.model_dump() for v in cp.verification],
            "note": "Imported approvals, receipts and verification are informational until resolved here.",
        }
        # A context run starts healthy: the failure that produced the checkpoint is not replayed.
        new_id = self.start_run(workspace_id, scenario="normal", task_variant=cp.task_variant,
                                tool_profile=cp.tool_profile if cp.tool_profile in PROFILES else "claude",
                                model_id="fixture-advanced", context=context)
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, new_id, workspace_id, lock=True)
            if cp.pending_input:
                self.repo.update_run(conn, new_id, pending_input=cp.pending_input)
            if cp.draft:
                content = cp.draft.content.model_dump()
                if content["signin"]["mode"] == "credentials" and not content["signin"].get("password"):
                    content["signin"]["password"] = load_template()["signin"]["password"]
                self.repo.log_step(conn, new_id, "prepare_draft", "started")
                d = self.repo.add_draft(conn, new_id, content, "imported checkpoint (untrusted until verified)")
                self.repo.log_step(conn, new_id, "prepare_draft", "completed")
                self.repo.update_run(conn, new_id, stage="draft_ready", signin_choice=content["signin"]["mode"])
                self._event(conn, run, "draft_saved", actor, revision=d["revision"], source="checkpoint")
            self._event(conn, run, "checkpoint_imported", actor, mode="context", from_run=cp.run_id,
                        from_workspace=cp.workspace_id)
            self._checkpoint(conn, new_id, workspace_id, "checkpoint_imported")
        return {"mode": "context", "run_id": new_id}

    def check_imported_operation(self, run_id, workspace_id: str) -> dict:
        """Look up an imported operation at the target, scoped to the current workspace only."""
        with self.db.transaction() as conn:
            run = self.repo.get_run(conn, run_id, workspace_id)
        ctx = run.get("context_from_checkpoint") or {}
        op = ctx.get("operation")
        if not op:
            return {"status": "none"}
        receipt = self.target.lookup(workspace_id=workspace_id, idempotency_key=op["idempotency_key"])
        result = {"status": "found" if receipt else "not_found_in_this_workspace", "receipt": receipt,
                  "operation_id": op["operation_id"]}
        with self.db.transaction() as conn:
            self._event(conn, run, "imported_operation_checked", operation_id=op["operation_id"],
                        found=bool(receipt))
        return result

    # ------------------------------------------------------------------ presentation helpers
    def status_card(self, run_id, workspace_id: str) -> dict:
        snap = self.snapshot(run_id, workspace_id)
        return describe(snap, self.policy, self.tool_state(snap.run))

    def manual_checklist(self, run_id, workspace_id: str) -> dict:
        return manual_continuation(self.snapshot(run_id, workspace_id))


def describe(snap: RunSnapshot, policy: PolicyService, tool_state) -> dict:
    """The three answers: what was saved, what was verified, what can I do now."""
    run = snap.run
    saved, verified = [], []
    if run.get("pending_input"):
        saved.append(("Unsent request preserved", "saved"))
    if snap.draft:
        saved.append((f"Draft revision {snap.draft['revision']} ({snap.draft['author']})", "saved"))
    if snap.active_approval:
        saved.append((f"Approval for revision {snap.active_approval['draft_revision']}", "saved"))
    ev = snap.signin_evidence
    if snap.draft:
        if policy.is_verified_current(snap):
            verified.append(("Sign-in", "VERIFIED", ev["source"]))
        elif ev:
            verified.append(("Sign-in", ev["status"].replace("_", " ").upper(), ev["source"]))
        else:
            verified.append(("Sign-in", "NOT CHECKED", f"no evidence for revision {snap.draft['revision']}"))
    op = snap.operation
    if op:
        label = {"confirmed": "CONFIRMED", "unknown": "UNKNOWN", "pending": "IN PROGRESS / UNKNOWN",
                 "not_written": "NOT WRITTEN"}[op["status"]]
        src = f"operation {str(op['id'])[:8]}"
        if snap.receipt:
            src += f" · receipt {snap.receipt['release_id']} via {snap.receipt['obtained_via']}"
        verified.append(("Publication", label, src))
    if snap.receipt_evidence:
        verified.append(("Receipt check", snap.receipt_evidence["status"].upper(), snap.receipt_evidence["source"]))
    unconfirmed = [f"{u['step'].replace('_', ' ')} started but not confirmed" for u in snap.unconfirmed]

    if not tool_state.available and not snap.draft:
        nxt = ("switch_tool", f"{tool_state.profile.name} is unavailable. Switch to an independent tool or "
                              "continue manually.")
    elif run.get("pending_question"):
        nxt = ("answer", "Answer the clarifying question.")
    elif run.get("pending_input") and not snap.draft:
        nxt = ("retry", "Retry the saved request, switch tools, or download the checkpoint.")
    elif not snap.draft:
        nxt = ("instruct", "Send the task to the assistant to prepare the draft.")
    elif op and op["status"] in ("unknown", "pending"):
        nxt = ("reconcile", "Check publication status. Do not publish again until the outcome is known.")
    elif op and op["status"] == "confirmed":
        nxt = ("done", "Done. One verified release exists." if snap.receipt_evidence and
               snap.receipt_evidence["status"] == "verified" else "Check publication status to verify the receipt.")
    elif op and op["status"] == "not_written":
        nxt = ("retry_publish", "Nothing was written. Retry publication with the same operation ID.")
    elif not policy.is_verified_current(snap):
        if ev and ev["status"] == "not_verified":
            nxt = ("verify", "Sign-in was not verified in this run. Switch to a tool with repository access or "
                             "run the manual check. Publication is blocked.")
        elif ev and ev["status"] == "failed":
            nxt = ("edit", "Verification failed. Edit the draft, then verify again.")
        else:
            nxt = ("verify", "Verify that a visitor can sign in.")
    elif not snap.active_approval:
        nxt = ("approve", "Review the draft and evidence, then approve publication.")
    else:
        nxt = ("publish", "Publish the approved revision.")
    return {
        "saved": saved, "verified": verified, "next": nxt, "unconfirmed": unconfirmed,
        "stage": run["stage"], "completed_steps": completed_steps(snap),
        "tool": {"name": tool_state.profile.name, "available": tool_state.available,
                 "model": tool_state.active_model, "capabilities": sorted(tool_state.capabilities),
                 "changes": tool_state.capability_changes, "reason": tool_state.reason},
    }

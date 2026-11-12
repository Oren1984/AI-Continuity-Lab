"""Versioned JSON checkpoint: build, export, validate on import, and manual continuation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .policy import PolicyService, RunSnapshot
from .verifier import FIXTURE_FILES, fixture_sources

SCHEMA_ID = "ai-continuity-lab/checkpoint"
SCHEMA_VERSION = "1.0"
ALLOWED_REFS = {f"fixtures/demo_repo/{f}" for f in FIXTURE_FILES}
_WS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
STEPS = ["prepare_draft", "verify_signin", "approve_publication", "publish", "confirm_receipt"]


class CheckpointImportError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=4000)


class SigninSpec(_Strict):
    mode: Literal["credentials", "one_click"] | None = None
    email: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=200)


class DraftContent(_Strict):
    version: str = Field(max_length=40)
    title: str = Field(max_length=200)
    target: str = Field(max_length=60)
    notes: str = Field(max_length=4000)
    signin: SigninSpec


class DraftRef(_Strict):
    revision: int = Field(ge=1, le=10_000)
    content: DraftContent
    content_hash: str = Field(max_length=64)


class Decision(_Strict):
    key: str = Field(max_length=20)
    decision: str = Field(max_length=1000)
    source: str = Field(max_length=200)


class SourceRef(_Strict):
    ref: str = Field(max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("ref")
    @classmethod
    def _in_scope(cls, v: str) -> str:
        if v not in ALLOWED_REFS:
            raise ValueError(f"reference outside the fixture scope: {v!r}")
        return v


class VerificationRef(_Strict):
    id: str = Field(max_length=64)
    kind: Literal["signin", "receipt"]
    draft_revision: int
    status: Literal["verified", "failed", "not_verified"]
    claim: str = Field(max_length=400)
    source: str = Field(max_length=300)


class ApprovalRef(_Strict):
    id: str = Field(max_length=64)
    draft_revision: int
    content_hash: str = Field(max_length=64)
    target: str = Field(max_length=60)
    status: str = Field(max_length=20)


class OperationRef(_Strict):
    operation_id: str = Field(max_length=64)
    idempotency_key: str = Field(pattern=r"^op-[0-9a-f-]{36}$")
    status: Literal["pending", "confirmed", "unknown", "not_written"]
    draft_revision: int
    receipt: dict | None = None


class ManualContinuation(_Strict):
    saved_work: list[str] = Field(max_length=30)
    outstanding_checks: list[str] = Field(max_length=30)
    unresolved_actions: list[str] = Field(max_length=30)
    next_steps: list[str] = Field(max_length=30)


class Checkpoint(_Strict):
    schema_id: Literal["ai-continuity-lab/checkpoint"] = Field(alias="schema")
    version: Literal["1.0"]
    exported_at: str = Field(max_length=40)
    workspace_id: str
    run_id: str = Field(max_length=64)
    checkpoint_seq: int = Field(ge=0)
    task_goal: str = Field(max_length=1000)
    task_variant: Literal["explicit", "inferable", "ambiguous"]
    scenario: str = Field(max_length=60)
    tool_profile: str = Field(max_length=30)
    model_id: str = Field(max_length=120)
    draft: DraftRef | None
    decisions: list[Decision] = Field(max_length=50)
    completed_steps: list[str] = Field(max_length=20)
    pending_steps: list[str] = Field(max_length=20)
    sources: list[SourceRef] = Field(max_length=20)
    verification: list[VerificationRef] = Field(max_length=50)
    approval: ApprovalRef | None
    operation: OperationRef | None
    pending_input: str | None = Field(default=None, max_length=4000)
    manual_continuation: ManualContinuation

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_max_length=4000)

    @field_validator("workspace_id")
    @classmethod
    def _ws(cls, v: str) -> str:
        if not _WS_RE.match(v):
            raise ValueError("invalid workspace id")
        return v


def completed_steps(s: RunSnapshot) -> list[str]:
    policy = PolicyService()
    done = []
    if s.draft:
        done.append("prepare_draft")
    if policy.is_verified_current(s):
        done.append("verify_signin")
    if s.active_approval or (s.operation and s.operation["status"] == "confirmed"):
        done.append("approve_publication")
    if s.operation and s.operation["status"] == "confirmed":
        done.append("publish")
    if s.receipt_evidence and s.receipt_evidence["status"] == "verified":
        done.append("confirm_receipt")
    return done


def manual_continuation(s: RunSnapshot) -> dict:
    policy = PolicyService()
    saved, checks, unresolved, steps = [], [], [], []
    run = s.run
    if run.get("pending_input"):
        saved.append(f"Unsent request preserved: “{run['pending_input'][:160]}”")
    if s.draft:
        c = s.draft["content"]
        saved.append(f"Draft revision {s.draft['revision']} ({c['version']}): {c['title']} — sign-in mode "
                     f"{c['signin'].get('mode')}")
    ev = s.signin_evidence
    if policy.is_verified_current(s):
        saved.append(f"Sign-in verified for revision {s.draft['revision']} by {ev['source']}.")
    elif s.draft:
        state = ev["status"].replace("_", " ") if ev else "not checked"
        checks.append(f"Verify sign-in for draft revision {s.draft['revision']} (currently: {state}). "
                      "Manually: run `python fixtures/demo_repo/checks/check_signin.py` with the draft's "
                      "sign-in details on stdin, or use the lab's manual check.")
    else:
        checks.append("Create the release draft (the template is fixtures/release_draft.json).")
    for u in s.unconfirmed:
        unresolved.append(f"Step “{u['step']}” started {u['created_at']:%Y-%m-%d %H:%M:%S} UTC but was not "
                          "confirmed. Re-run it.")
    op = s.operation
    if op and op["status"] in ("unknown", "pending"):
        unresolved.append(f"Publication {op['id']} has an UNKNOWN outcome. Check the target for idempotency key "
                          f"{op['idempotency_key']} before any retry. Do not publish again by other means.")
    if op and op["status"] == "confirmed" and s.receipt:
        saved.append(f"Published: release {s.receipt['release_id']} (operation {op['id']}).")
    if op and op["status"] == "not_written":
        unresolved.append(f"Publication {op['id']} was confirmed NOT written. Retry with the same operation ID.")
    if s.active_approval:
        saved.append(f"Approval for revision {s.active_approval['draft_revision']} to "
                     f"{s.active_approval['target']} (valid only in the original workspace).")
    else:
        steps.append("Approval is required before publication. Imported approvals are informational only.")
    done = completed_steps(s)
    steps += [f"Then: {step.replace('_', ' ')}" for step in STEPS if step not in done]
    return {"saved_work": saved, "outstanding_checks": checks, "unresolved_actions": unresolved, "next_steps": steps}


def build_payload(s: RunSnapshot, decisions: list[dict], evidence: list[dict]) -> dict:
    run = s.run
    done = completed_steps(s)
    op = s.operation
    return {
        "schema": SCHEMA_ID,
        "version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace_id": run["workspace_id"],
        "run_id": str(run["id"]),
        "checkpoint_seq": 0,
        "task_goal": run["goal"],
        "task_variant": run["task_variant"],
        "scenario": run["scenario"],
        "tool_profile": run["tool_profile"],
        "model_id": run["model_id"],
        "draft": (
            {"revision": s.draft["revision"], "content": s.draft["content"], "content_hash": s.draft["content_hash"]}
            if s.draft else None
        ),
        "decisions": [dict(d) for d in decisions],
        "completed_steps": done,
        "pending_steps": [st for st in STEPS if st not in done],
        "sources": fixture_sources(),
        "verification": [
            {"id": str(e["id"]), "kind": e["kind"], "draft_revision": e["draft_revision"], "status": e["status"],
             "claim": e["claim"], "source": e["source"]}
            for e in evidence[-20:]
        ],
        "approval": (
            {"id": str(s.active_approval["id"]), "draft_revision": s.active_approval["draft_revision"],
             "content_hash": s.active_approval["content_hash"], "target": s.active_approval["target"],
             "status": s.active_approval["status"]}
            if s.active_approval else None
        ),
        "operation": (
            {"operation_id": str(op["id"]), "idempotency_key": op["idempotency_key"], "status": op["status"],
             "draft_revision": op["draft_revision"], "receipt": s.receipt["receipt"] if s.receipt else None}
            if op else None
        ),
        "pending_input": run.get("pending_input"),
        "manual_continuation": manual_continuation(s),
    }


def parse_import(raw: bytes, *, max_bytes: int) -> Checkpoint:
    """Validate size, encoding, schema and fixture scope. Raises CheckpointImportError."""
    if len(raw) > max_bytes:
        raise CheckpointImportError(f"checkpoint is {len(raw)} bytes; the limit is {max_bytes}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CheckpointImportError("not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise CheckpointImportError("checkpoint must be a JSON object")
    try:
        return Checkpoint.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise CheckpointImportError(f"invalid checkpoint at {loc}: {first['msg']}") from exc

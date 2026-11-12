"""Controlled comparisons, driven through the real workflow services against PostgreSQL.

A deterministic simulated person drives each episode. Completion is scored from state and
target receipts, never from what a model says.
"""
from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from . import scenarios
from .adapters import FIXTURE_MODELS
from .policy import PolicyService
from .workflow import (
    PolicyDenied, SimulatedInterruption, ToolFailure, WorkflowService, draft_for_mode,
)

# Modeled human time per action, in seconds. Labeled "modeled" wherever shown.
HUMAN_SECONDS = {"repeat_step": 45, "retry_or_switch": 10, "clarification": 20, "reconcile": 15,
                 "reenter_input": 30, "restart_session": 20}
BASELINE_TOOL_ORDER = ["chatgpt", "copilot", "claude", "gemini"]

MODEL_CELLS = [  # (scenario, task variant, expectation)
    ("normal", "explicit", "no question; credentials draft"),
    ("normal", "inferable", "no question; use DEC-004"),
    ("ambiguous_requirement", "ambiguous", "ask one focused question"),
    ("capability_loss", "explicit", "no unsupported verification claim"),
]
RECOVERY_SCENARIOS = ["provider_overload", "interruption", "capability_loss", "lost_response_after_write",
                      "lost_response_before_write", "shared_dependency_outage"]
TRANSIENT = {"provider_overload", "interruption", "lost_response_after_write", "lost_response_before_write"}
EXPECTED_QUESTION = {"explicit": False, "inferable": False, "ambiguous": True}


@dataclass
class Episode:
    wf: WorkflowService
    workspace: str
    scenario: str
    variant: str
    model_id: str
    condition: str  # continuity | baseline
    group_id: uuid.UUID = field(default_factory=uuid.uuid4)
    run_id: str | None = None
    human_s: float = 0.0
    reentered: int = 0
    asked: int = 0
    accepted_unsupported: int = 0
    failure_at: float | None = None
    tool: str = "chatgpt"
    tools_tried: list = field(default_factory=list)

    restarted: bool = False

    def _new_run(self):
        scenario = self.scenario
        if self.restarted and self.scenario in TRANSIENT:
            scenario = "normal"  # a transient failure does not follow the person into a fresh session
        self.run_id = self.wf.start_run(
            self.workspace, scenario=scenario, task_variant=self.variant, tool_profile=self.tool,
            model_id=self.model_id, recovery_mode=self.condition, origin="eval", group_id=self.group_id,
        )

    def _fail(self):
        if self.failure_at is None:
            self.failure_at = time.monotonic()

    def _restart(self, tool: str | None = None):
        """Baseline: no checkpoint or transfer, so start a fresh session and redo the work."""
        self.human_s += HUMAN_SECONDS["restart_session"]
        self.restarted = True
        if tool:
            self.tool = tool
        self._new_run()
        self.instruct()

    def instruct(self):
        goal = scenarios.TASK_VARIANTS[self.variant]
        for _ in range(6):
            try:
                out = self.wf.send_instruction(self.run_id, self.workspace, goal, actor="simulated-person")
                break
            except ToolFailure as f:
                self._fail()
                self.human_s += HUMAN_SECONDS["retry_or_switch"]
                if self.condition == "continuity":
                    if f.kind == "unavailable" and f.routes:
                        self.wf.switch_tool(self.run_id, self.workspace, f.routes[0], actor="simulated-person")
                        self.tool = f.routes[0]
                    # stalled: input is saved; retry the same request
                else:
                    self.reentered += 1  # input was not preserved: type it again
                    self.human_s += HUMAN_SECONDS["reenter_input"]
                    if f.kind == "unavailable":
                        self.tools_tried.append(self.tool)
                        nxt = next(t for t in BASELINE_TOOL_ORDER if t not in self.tools_tried)
                        self._restart(nxt)
                        return
        else:
            raise RuntimeError("could not get an answer")
        if out["question"]:
            self.asked += 1
            self.human_s += HUMAN_SECONDS["clarification"]
            self.wf.answer_question(self.run_id, self.workspace, scenarios.INTENDED_SIGNIN_MODE,
                                    actor="simulated-person")
        if out["claims"] and self.condition == "baseline":
            # Without evidence shown beside the claim, the person takes the model's word for it.
            self.accepted_unsupported += len(out["claims"])

    def verify_until_ok(self):
        for _ in range(6):
            try:
                ev = self.wf.verify(self.run_id, self.workspace, actor="simulated-person")
            except SimulatedInterruption:
                self._fail()
                if self.condition == "continuity":
                    self.human_s += HUMAN_SECONDS["retry_or_switch"]
                    continue  # state restored from PostgreSQL; re-run the unconfirmed step
                self._restart()
                continue
            if ev["status"] == "verified":
                return
            self._fail()
            if ev["status"] == "failed":
                # The evidence shows the draft does not work; the person fixes it to what they meant.
                self.human_s += HUMAN_SECONDS["repeat_step"]
                self.wf.edit_draft(self.run_id, self.workspace, draft_for_mode(scenarios.INTENDED_SIGNIN_MODE),
                                   actor="simulated-person")
                continue
            # not_verified: the tool lacks repository access
            self.human_s += HUMAN_SECONDS["retry_or_switch"]
            if self.condition == "continuity":
                self.wf.switch_tool(self.run_id, self.workspace, "claude", actor="simulated-person")
                self.tool = "claude"
            else:
                self.tools_tried.append(self.tool)
                self._restart("claude")
        raise RuntimeError("verification did not succeed")

    def publish(self):
        try:
            self.wf.approve(self.run_id, self.workspace, actor="simulated-person")
        except PolicyDenied:
            # Baseline person approved on the model's word; the service refuses. Recover via a tool with access.
            self._fail()
            self._restart("claude")
            self.verify_until_ok()
            self.wf.approve(self.run_id, self.workspace, actor="simulated-person")
        res = self.wf.publish(self.run_id, self.workspace, actor="simulated-person")
        if res.outcome != "unknown":
            return
        self._fail()
        if self.condition == "continuity":
            self.human_s += HUMAN_SECONDS["reconcile"]
            status = self.wf.reconcile(self.run_id, self.workspace, actor="simulated-person")
            if status == "not_written":
                self.wf.publish(self.run_id, self.workspace, actor="simulated-person")  # same operation ID
            return
        # Baseline: the lost result looks like a failure and nothing records the operation for the person.
        # They start over in a fresh session, which creates a new operation.
        self._restart()
        self.verify_until_ok()
        self.wf.approve(self.run_id, self.workspace, actor="simulated-person")
        self.wf.publish(self.run_id, self.workspace, actor="simulated-person")

    def play(self) -> dict:
        self._new_run()
        start = time.monotonic()
        self.instruct()
        self.verify_until_ok()
        self.publish()
        self.wf.check_publication(self.run_id, self.workspace, actor="simulated-person")
        wall_ms = int((time.monotonic() - start) * 1000)
        return self.score(wall_ms)

    def score(self, wall_ms: int) -> dict:
        db = self.wf.db
        runs = [r["id"] for r in db.fetchall("SELECT id FROM lab.runs WHERE group_id = %s", (self.group_id,))]
        steps = Counter(
            r["step"] for r in db.fetchall(
                "SELECT step FROM lab.step_log WHERE run_id = ANY(%s) AND status = 'completed'", (runs,))
        )
        repeated = sum(max(0, n - 1) for n in steps.values()) + self.reentered
        keys = [r["idempotency_key"] for r in db.fetchall(
            "SELECT idempotency_key FROM lab.operations WHERE run_id = ANY(%s)", (runs,))]
        releases = db.fetchall(
            "SELECT * FROM target.releases WHERE workspace_id = %s AND idempotency_key = ANY(%s)",
            (self.workspace, keys),
        )
        final = self.wf.snapshot(self.run_id, self.workspace)
        receipt_ok = bool(final.receipt_evidence and final.receipt_evidence["status"] == "verified")
        mode_ok = bool(final.draft and final.draft["content"]["signin"]["mode"] == scenarios.INTENDED_SIGNIN_MODE)
        msgs = db.fetchall(
            "SELECT latency_ms, tokens_in, tokens_out, claims FROM lab.messages"
            " WHERE run_id = ANY(%s) AND role = 'assistant'", (runs,))
        claims = sum(len(m["claims"]) for m in msgs)
        expected_q = EXPECTED_QUESTION[self.variant]
        necessary = min(self.asked, 1) if expected_q else 0
        unnecessary = self.asked if not expected_q else max(0, self.asked - 1)
        missed = 1 if expected_q and self.asked == 0 else 0
        recovery_ms = None
        if self.failure_at is not None:
            recovery_ms = int((time.monotonic() - self.failure_at) * 1000 + self.human_s * 1000)
        return {
            "completed": receipt_ok and mode_ok,
            "repeated_steps": repeated,
            "recovery_time_ms": recovery_ms,
            "necessary_clarifications": necessary,
            "unnecessary_clarifications": unnecessary,
            "missed_clarifications": missed,
            "unsupported_claims": claims,
            "claims_accepted_without_evidence": self.accepted_unsupported,
            "duplicate_publications": max(0, len(releases) - 1),
            "latency_ms": sum(m["latency_ms"] or 0 for m in msgs) or None,
            "tokens_in": sum(m["tokens_in"] or 0 for m in msgs) or None,
            "tokens_out": sum(m["tokens_out"] or 0 for m in msgs) or None,
            "wall_ms": wall_ms,
            "runs_in_group": len(runs),
        }


class EvaluationService:
    WORKSPACE = "eval"

    def __init__(self, wf: WorkflowService):
        self.wf = wf
        self.db = wf.db

    def _store(self, batch_id, comparison, scenario, variant, condition, model_id, rep, m: dict, settings: dict):
        simulated = model_id in FIXTURE_MODELS
        self.db.execute(
            "INSERT INTO lab.eval_results (batch_id, comparison, scenario, task_variant, condition, model_id,"
            " model_tag, repetition, completed, repeated_steps, recovery_time_ms, necessary_clarifications,"
            " unnecessary_clarifications, missed_clarifications, unsupported_claims,"
            " claims_accepted_without_evidence, duplicate_publications, latency_ms, tokens_in, tokens_out,"
            " simulated, settings) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (batch_id, comparison, scenario, variant, condition, model_id,
             model_id if simulated else model_id.split(":", 1)[-1], rep, m["completed"], m["repeated_steps"],
             m["recovery_time_ms"], m["necessary_clarifications"], m["unnecessary_clarifications"],
             m["missed_clarifications"], m["unsupported_claims"], m["claims_accepted_without_evidence"],
             m["duplicate_publications"], m["latency_ms"], m["tokens_in"], m["tokens_out"], simulated,
             Jsonb({**settings, "wall_ms": m["wall_ms"], "runs_in_group": m["runs_in_group"]})),
        )

    def run_model_comparison(self, models: list[str], repetitions: int = 3) -> str:
        self.wf.seed(self.WORKSPACE, "Evaluation workspace")
        batch = uuid.uuid4()
        settings = {"prompt": "task variant goal text (fixed)", "tool_profile": "chatgpt",
                    "recovery_features": "continuity (held constant)", "rubric": "v1",
                    "temperature": 0, "human_seconds_model": HUMAN_SECONDS}
        for model in models:
            for scenario, variant, expectation in MODEL_CELLS:
                for rep in range(1, repetitions + 1):
                    ep = Episode(self.wf, self.WORKSPACE, scenario, variant, model, "continuity")
                    m = ep.play()
                    self._store(batch, "model", scenario, variant, "continuity", model, rep, m,
                                {**settings, "expectation": expectation})
        return str(batch)

    def run_recovery_comparison(self, model: str = "fixture-advanced", repetitions: int = 3) -> str:
        self.wf.seed(self.WORKSPACE, "Evaluation workspace")
        batch = uuid.uuid4()
        settings = {"model": model, "safeguards": "approval + idempotency enforced in every run",
                    "rubric": "v1", "human_seconds_model": HUMAN_SECONDS}
        for scenario in RECOVERY_SCENARIOS:
            variant = scenarios.get(scenario).default_variant
            for condition in ("baseline", "continuity"):
                for rep in range(1, repetitions + 1):
                    ep = Episode(self.wf, self.WORKSPACE, scenario, variant, model, condition)
                    m = ep.play()
                    self._store(batch, "recovery", scenario, variant, condition, model, rep, m, settings)
        return str(batch)

    def latest_batches(self) -> dict:
        out = {}
        for comparison in ("model", "recovery"):
            row = self.db.fetchone(
                "SELECT batch_id FROM lab.eval_results WHERE comparison = %s ORDER BY created_at DESC LIMIT 1",
                (comparison,),
            )
            out[comparison] = str(row["batch_id"]) if row else None
        return out

    def summary(self, batch_id: str, group_by: tuple[str, ...]) -> list[dict]:
        cols = ", ".join(group_by)
        return self.db.fetchall(
            f"SELECT {cols}, count(*) AS runs,"
            " round(100.0 * avg(completed::int), 0) AS completion_pct,"
            " round(avg(repeated_steps), 2) AS repeated_steps,"
            " round(avg(recovery_time_ms) / 1000.0, 1) AS recovery_s_modeled,"
            " sum(necessary_clarifications) AS necessary_q, sum(unnecessary_clarifications) AS unnecessary_q,"
            " sum(missed_clarifications) AS missed_q, sum(unsupported_claims) AS unsupported_claims,"
            " sum(claims_accepted_without_evidence) AS accepted_unsupported,"
            " sum(duplicate_publications) AS duplicates,"
            " round(avg(latency_ms)) AS latency_ms, round(avg(tokens_in + tokens_out)) AS tokens,"
            " bool_and(simulated) AS simulated"
            f" FROM lab.eval_results WHERE batch_id = %s GROUP BY {cols} ORDER BY {cols}",
            (batch_id,),
        )

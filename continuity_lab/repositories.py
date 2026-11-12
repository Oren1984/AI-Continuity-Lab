"""PostgreSQL repositories. Every query that touches run data is scoped by workspace."""
from __future__ import annotations

import hashlib
import json
import uuid

from psycopg import Connection
from psycopg.types.json import Jsonb

from .services.policy import RunSnapshot


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


class NotFound(Exception):
    pass


class Repo:
    # ---- workspaces & decisions -------------------------------------------------
    def ensure_workspace(self, conn: Connection, workspace_id: str, name: str) -> None:
        conn.execute(
            "INSERT INTO lab.workspaces (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (workspace_id, name),
        )

    def list_workspaces(self, conn: Connection) -> list[dict]:
        return conn.execute("SELECT * FROM lab.workspaces ORDER BY created_at").fetchall()

    def upsert_decision(self, conn, workspace_id: str, key: str, decision: str, source: str) -> None:
        conn.execute(
            "INSERT INTO lab.decisions (workspace_id, key, decision, source) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (workspace_id, key) DO UPDATE SET decision = EXCLUDED.decision, source = EXCLUDED.source",
            (workspace_id, key, decision, source),
        )

    def decisions(self, conn, workspace_id: str) -> list[dict]:
        return conn.execute(
            "SELECT key, decision, source FROM lab.decisions WHERE workspace_id = %s ORDER BY key", (workspace_id,)
        ).fetchall()

    # ---- runs ---------------------------------------------------------------------
    def create_run(self, conn, **fields) -> uuid.UUID:
        run_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO lab.runs (id, workspace_id, scenario, task_variant, tool_profile, model_id,"
            " recovery_mode, origin, goal, stage, context_from_checkpoint, group_id)"
            " VALUES (%(id)s, %(workspace_id)s, %(scenario)s, %(task_variant)s, %(tool_profile)s, %(model_id)s,"
            " %(recovery_mode)s, %(origin)s, %(goal)s, %(stage)s, %(ctx)s, %(group_id)s)",
            {**fields, "id": run_id, "ctx": Jsonb(fields.get("ctx")) if fields.get("ctx") else None,
             "group_id": fields.get("group_id") or run_id},
        )
        return run_id

    def get_run(self, conn, run_id, workspace_id: str, *, lock: bool = False) -> dict:
        sql = "SELECT * FROM lab.runs WHERE id = %s AND workspace_id = %s"
        if lock:
            sql += " FOR UPDATE"
        row = conn.execute(sql, (run_id, workspace_id)).fetchone()
        if row is None:
            raise NotFound(f"run {run_id} not found in workspace {workspace_id}")
        return row

    def update_run(self, conn, run_id, **fields) -> None:
        sets = []
        params = []
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            params.append(Jsonb(v) if isinstance(v, (dict, list)) else v)
        sets.append("updated_at = now()")
        conn.execute(f"UPDATE lab.runs SET {', '.join(sets)} WHERE id = %s", (*params, run_id))

    def list_runs(self, conn, workspace_id: str, origin: str | None = "ui", limit: int = 30) -> list[dict]:
        if origin:
            return conn.execute(
                "SELECT * FROM lab.runs WHERE workspace_id = %s AND origin = %s ORDER BY created_at DESC LIMIT %s",
                (workspace_id, origin, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM lab.runs WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
            (workspace_id, limit),
        ).fetchall()

    # ---- drafts ---------------------------------------------------------------------
    def add_draft(self, conn, run_id, content: dict, author: str) -> dict:
        rev = conn.execute(
            "SELECT coalesce(max(revision), 0) + 1 AS r FROM lab.draft_revisions WHERE run_id = %s", (run_id,)
        ).fetchone()["r"]
        return conn.execute(
            "INSERT INTO lab.draft_revisions (id, run_id, revision, content, content_hash, author)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (uuid.uuid4(), run_id, rev, Jsonb(content), content_hash(content), author),
        ).fetchone()

    def latest_draft(self, conn, run_id) -> dict | None:
        return conn.execute(
            "SELECT * FROM lab.draft_revisions WHERE run_id = %s ORDER BY revision DESC LIMIT 1", (run_id,)
        ).fetchone()

    # ---- steps & checkpoints ---------------------------------------------------------
    def log_step(self, conn, run_id, step: str, status: str) -> None:
        conn.execute("INSERT INTO lab.step_log (run_id, step, status) VALUES (%s, %s, %s)", (run_id, step, status))

    def step_log(self, conn, run_id) -> list[dict]:
        return conn.execute("SELECT * FROM lab.step_log WHERE run_id = %s ORDER BY id", (run_id,)).fetchall()

    def add_checkpoint(self, conn, run_id, step: str, payload: dict) -> int:
        seq = conn.execute(
            "SELECT coalesce(max(seq), 0) + 1 AS s FROM lab.checkpoints WHERE run_id = %s", (run_id,)
        ).fetchone()["s"]
        payload["checkpoint_seq"] = seq
        conn.execute(
            "INSERT INTO lab.checkpoints (id, run_id, seq, step, payload) VALUES (%s, %s, %s, %s, %s)",
            (uuid.uuid4(), run_id, seq, step, Jsonb(payload)),
        )
        return seq

    def latest_checkpoint(self, conn, run_id) -> dict | None:
        return conn.execute(
            "SELECT * FROM lab.checkpoints WHERE run_id = %s ORDER BY seq DESC LIMIT 1", (run_id,)
        ).fetchone()

    # ---- evidence, approvals, operations ---------------------------------------------
    def add_evidence(self, conn, run_id, revision: int, kind: str, claim: str, status: str, source: str,
                     detail: dict) -> dict:
        return conn.execute(
            "INSERT INTO lab.verification_evidence (id, run_id, draft_revision, kind, claim, status, source, detail)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (uuid.uuid4(), run_id, revision, kind, claim, status, source, Jsonb(detail)),
        ).fetchone()

    def evidence(self, conn, run_id) -> list[dict]:
        return conn.execute(
            "SELECT * FROM lab.verification_evidence WHERE run_id = %s ORDER BY created_at", (run_id,)
        ).fetchall()

    def add_approval(self, conn, run_id, draft: dict, verification_id, target: str, actor: str) -> dict:
        return conn.execute(
            "INSERT INTO lab.approvals (id, run_id, draft_revision, content_hash, verification_id, target,"
            " approved_by, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active') RETURNING *",
            (uuid.uuid4(), run_id, draft["revision"], draft["content_hash"], verification_id, target, actor),
        ).fetchone()

    def invalidate_approvals(self, conn, run_id, reason: str) -> int:
        return conn.execute(
            "UPDATE lab.approvals SET status = 'invalidated', reason = %s, updated_at = now()"
            " WHERE run_id = %s AND status = 'active'",
            (reason, run_id),
        ).rowcount

    def set_approval_status(self, conn, approval_id, status: str) -> None:
        conn.execute("UPDATE lab.approvals SET status = %s, updated_at = now() WHERE id = %s", (status, approval_id))

    def create_operation(self, conn, run_id, approval: dict, target: str) -> dict:
        op_id = uuid.uuid4()
        return conn.execute(
            "INSERT INTO lab.operations (id, run_id, approval_id, draft_revision, target, idempotency_key, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING *",
            (op_id, run_id, approval["id"], approval["draft_revision"], target, f"op-{op_id}"),
        ).fetchone()

    def latest_operation(self, conn, run_id) -> dict | None:
        return conn.execute(
            "SELECT * FROM lab.operations WHERE run_id = %s ORDER BY created_at DESC LIMIT 1", (run_id,)
        ).fetchone()

    def update_operation(self, conn, op_id, status: str, bump_attempt: bool = False) -> dict:
        return conn.execute(
            "UPDATE lab.operations SET status = %s, attempts = attempts + %s, updated_at = now()"
            " WHERE id = %s RETURNING *",
            (status, 1 if bump_attempt else 0, op_id),
        ).fetchone()

    def add_attempt(self, conn, op_id, attempt_no: int, outcome: str, detail: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO lab.publication_attempts (operation_id, attempt_no, outcome, detail) VALUES (%s, %s, %s, %s)",
            (op_id, attempt_no, outcome, Jsonb(detail or {})),
        )

    def attempts(self, conn, op_id) -> list[dict]:
        return conn.execute(
            "SELECT * FROM lab.publication_attempts WHERE operation_id = %s ORDER BY id", (op_id,)
        ).fetchall()

    def add_receipt(self, conn, op: dict, receipt: dict, via: str) -> dict:
        return conn.execute(
            "INSERT INTO lab.target_receipts (id, operation_id, run_id, release_id, receipt, obtained_via)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (operation_id) DO NOTHING RETURNING *",
            (uuid.uuid4(), op["id"], op["run_id"], receipt["release_id"], Jsonb(receipt), via),
        ).fetchone()

    def receipt_for(self, conn, op_id) -> dict | None:
        return conn.execute("SELECT * FROM lab.target_receipts WHERE operation_id = %s", (op_id,)).fetchone()

    # ---- conversation ---------------------------------------------------------------
    def add_message(self, conn, run_id, role: str, content: str, **meta) -> None:
        conn.execute(
            "INSERT INTO lab.messages (run_id, role, content, tool_profile, model_id, latency_ms, tokens_in,"
            " tokens_out, simulated, claims) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id, role, content, meta.get("tool_profile"), meta.get("model_id"), meta.get("latency_ms"),
                meta.get("tokens_in"), meta.get("tokens_out"), meta.get("simulated", True),
                Jsonb(meta.get("claims", [])),
            ),
        )

    def messages(self, conn, run_id) -> list[dict]:
        return conn.execute("SELECT * FROM lab.messages WHERE run_id = %s ORDER BY id", (run_id,)).fetchall()

    def events(self, conn, run_id, limit: int = 200) -> list[dict]:
        return conn.execute(
            "SELECT * FROM lab.events WHERE run_id = %s ORDER BY id DESC LIMIT %s", (run_id, limit)
        ).fetchall()

    def count_events(self, conn, run_id, event_type: str, **detail_match) -> int:
        sql = "SELECT count(*) AS n FROM lab.events WHERE run_id = %s AND event_type = %s"
        params: list = [run_id, event_type]
        for k, v in detail_match.items():
            sql += " AND detail ->> %s = %s"
            params += [k, v]
        return conn.execute(sql, params).fetchone()["n"]

    # ---- snapshot ---------------------------------------------------------------------
    def snapshot(self, conn, run_id, workspace_id: str, *, lock: bool = False) -> RunSnapshot:
        run = self.get_run(conn, run_id, workspace_id, lock=lock)
        draft = self.latest_draft(conn, run_id)
        signin = None
        if draft:
            signin = conn.execute(
                "SELECT * FROM lab.verification_evidence WHERE run_id = %s AND kind = 'signin'"
                " AND draft_revision = %s ORDER BY created_at DESC LIMIT 1",
                (run_id, draft["revision"]),
            ).fetchone()
        approval = conn.execute(
            "SELECT * FROM lab.approvals WHERE run_id = %s AND status = 'active'", (run_id,)
        ).fetchone()
        op = self.latest_operation(conn, run_id)
        receipt = self.receipt_for(conn, op["id"]) if op else None
        receipt_ev = conn.execute(
            "SELECT * FROM lab.verification_evidence WHERE run_id = %s AND kind = 'receipt'"
            " ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        steps = self.step_log(conn, run_id)
        open_steps: dict[str, dict] = {}
        for s in steps:
            if s["status"] == "started":
                open_steps[s["step"]] = s
            else:
                open_steps.pop(s["step"], None)
        return RunSnapshot(run, draft, signin, approval, op, receipt, receipt_ev, list(open_steps.values()))

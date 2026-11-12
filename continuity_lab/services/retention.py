"""Data minimization: configurable retention, scoped deletion, audit tombstones."""
from __future__ import annotations

from .events import EventLog


class RetentionService:
    def __init__(self, db, retention_days: int):
        self.db = db
        self.retention_days = retention_days
        self.events = EventLog()

    def purge_expired(self) -> dict:
        """Delete runs (and everything that cascades from them) older than the retention window.

        Audit events are kept for the same window plus 7 days, then deleted too."""
        with self.db.transaction() as conn:
            runs = conn.execute(
                "DELETE FROM lab.runs WHERE created_at < now() - make_interval(days => %s)",
                (self.retention_days,),
            ).rowcount
            events = conn.execute(
                "DELETE FROM lab.events WHERE created_at < now() - make_interval(days => %s)",
                (self.retention_days + 7,),
            ).rowcount
            evals = conn.execute(
                "DELETE FROM lab.eval_results WHERE created_at < now() - make_interval(days => %s)",
                (self.retention_days,),
            ).rowcount
            self.events.record(conn, "retention_purge", workspace_id=None, actor="system",
                               detail={"runs": runs, "events": events, "eval_results": evals,
                                       "retention_days": self.retention_days})
        return {"runs": runs, "events": events, "eval_results": evals}

    def delete_workspace_records(self, workspace_id: str, actor: str = "person") -> dict:
        """Delete demo records for one workspace. A content-free tombstone is retained for audit."""
        with self.db.transaction() as conn:
            runs = conn.execute("DELETE FROM lab.runs WHERE workspace_id = %s", (workspace_id,)).rowcount
            emb = conn.execute("DELETE FROM lab.embeddings WHERE workspace_id = %s", (workspace_id,)).rowcount
            events = conn.execute(
                "DELETE FROM lab.events WHERE workspace_id = %s", (workspace_id,)
            ).rowcount
            releases = conn.execute(
                "DELETE FROM target.releases WHERE workspace_id = %s", (workspace_id,)
            ).rowcount
            self.events.record(conn, "records_deleted", workspace_id=workspace_id, actor=actor,
                               detail={"runs": runs, "embeddings": emb, "events": events,
                                       "simulated_releases": releases})
        return {"runs": runs, "embeddings": emb, "events": events, "simulated_releases": releases}

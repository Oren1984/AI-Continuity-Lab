"""Simulated local publishing target behind a narrow interface.

The target keeps its own records in the `target` schema and writes in its own
transaction, so it can succeed even when the lab never hears back.
"""
from __future__ import annotations

import uuid

TARGET_NAME = "local-release-registry"


class TargetError(Exception):
    pass


class ResponseLost(TargetError):
    """The request may or may not have been applied; the response did not arrive."""


class SimulatedTarget:
    """Only these four operations exist. Writes are idempotent on `idempotency_key`."""

    name = TARGET_NAME

    def __init__(self, db):
        self.db = db

    def publish(self, *, workspace_id: str, idempotency_key: str, release: dict) -> dict:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO target.releases (release_id, idempotency_key, workspace_id, version, title,"
                " content_hash, draft_revision) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (idempotency_key) DO NOTHING",
                (
                    uuid.uuid4(), idempotency_key, workspace_id, release["version"], release["title"],
                    release["content_hash"], release["draft_revision"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM target.releases WHERE idempotency_key = %s AND workspace_id = %s",
                (idempotency_key, workspace_id),
            ).fetchone()
        if row is None:
            raise TargetError("idempotency key belongs to another workspace")
        return self._receipt(row)

    def lookup(self, *, workspace_id: str, idempotency_key: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT * FROM target.releases WHERE idempotency_key = %s AND workspace_id = %s",
            (idempotency_key, workspace_id),
        )
        return self._receipt(row) if row else None

    def list_releases(self, workspace_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM target.releases WHERE workspace_id = %s ORDER BY created_at", (workspace_id,)
        )
        return [self._receipt(r) for r in rows]

    def delete_workspace(self, workspace_id: str) -> int:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM target.releases WHERE workspace_id = %s", (workspace_id,)).rowcount

    def _receipt(self, row: dict) -> dict:
        return {
            "target": self.name,
            "release_id": str(row["release_id"]),
            "idempotency_key": row["idempotency_key"],
            "version": row["version"],
            "title": row["title"],
            "content_hash": row["content_hash"],
            "draft_revision": row["draft_revision"],
            "created_at": row["created_at"].isoformat(),
        }


class FaultyTargetClient:
    """Wraps the target to lose one response, either before or after the write."""

    def __init__(self, target: SimulatedTarget, lose: str | None):
        self.target = target
        self.lose = lose

    def publish(self, **kwargs) -> dict:
        if self.lose == "before_write":
            raise ResponseLost("connection dropped before the target received the request")
        receipt = self.target.publish(**kwargs)
        if self.lose == "after_write":
            raise ResponseLost("the target applied the request but the response was lost")
        return receipt

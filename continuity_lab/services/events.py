"""Structured, correlated, redacted events. Doubles as the audit log."""
from __future__ import annotations

import re
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

_SENSITIVE_KEYS = {"password", "prompt", "raw_prompt", "secret", "token", "api_key", "authorization"}
_PATTERNS = [
    (re.compile(r"(?i)(password\s*[:=]\s*)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(sk|pk|ghp|xox[bp])[-_][A-Za-z0-9_-]{8,}"), "[redacted-key]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@(?!demo\.local\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[redacted-email]"),
]


def redact(value: Any, *, keep_prompts: bool = False) -> Any:
    """Recursively redact sensitive keys and patterns. Raw prompts are dropped unless enabled."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k.lower() in _SENSITIVE_KEYS and not (keep_prompts and k.lower() in {"prompt", "raw_prompt"}):
                out[k] = "[redacted]"
            else:
                out[k] = redact(v, keep_prompts=keep_prompts)
        return out
    if isinstance(value, list):
        return [redact(v, keep_prompts=keep_prompts) for v in value]
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value
    return value


class EventLog:
    def __init__(self, keep_prompts: bool = False):
        self.keep_prompts = keep_prompts

    def record(
        self,
        conn: Connection,
        event_type: str,
        *,
        workspace_id: str | None,
        run_id: Any = None,
        correlation_id: str | None = None,
        actor: str = "system",
        detail: dict | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO lab.events (workspace_id, run_id, correlation_id, event_type, actor, detail)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                workspace_id,
                run_id,
                correlation_id or (str(run_id) if run_id else None),
                event_type,
                actor,
                Jsonb(redact(detail or {}, keep_prompts=self.keep_prompts)),
            ),
        )

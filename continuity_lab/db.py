"""PostgreSQL access: connection pool, versioned migrations, readiness."""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_LOCK = 724_001


class Database:
    """Thin wrapper over a psycopg connection pool. Every `transaction()` commits or rolls back."""

    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.url = url
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn

    def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)

    def ready(self) -> tuple[bool, str]:
        try:
            row = self.fetchone("SELECT max(version) AS v FROM public.schema_migrations")
            return True, f"migrations at {row['v']}"
        except Exception as exc:  # readiness must never raise
            return False, type(exc).__name__

    def close(self) -> None:
        self.pool.close()


def wait_for_database(url: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def migrate(url: str) -> list[str]:
    """Apply pending migrations in order under an advisory lock. Returns applied versions."""
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
                " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM public.schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (version,))
                applied.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied

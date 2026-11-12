"""Shared fixtures. Integration and e2e tests use a throwaway PostgreSQL database."""
from __future__ import annotations

import dataclasses
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _db_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def test_db_url():
    base = _db_url()
    if not base:
        pytest.skip("DATABASE_URL not set: run inside the Compose stack")
    import psycopg

    name = f"lab_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + "/" + name
    yield url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture(scope="session")
def services(test_db_url):
    from continuity_lab.bootstrap import build
    from continuity_lab.config import get_settings

    settings = dataclasses.replace(get_settings(), database_url=test_db_url)
    svc = build(settings)
    yield svc
    svc.db.close()


@pytest.fixture
def ws(services):
    """A fresh, isolated workspace per test."""
    wid = f"t-{uuid.uuid4().hex[:10]}"
    services.workflow.seed(wid, "test workspace")
    return wid


@pytest.fixture
def wf(services):
    return services.workflow

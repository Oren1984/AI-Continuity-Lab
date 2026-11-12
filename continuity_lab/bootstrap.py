"""Wire up services once per process. Idempotent setup: migrations, seed, retrieval index, retention."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .db import Database, migrate, wait_for_database
from .help_content import ARTICLES
from .services.evaluation import EvaluationService
from .services.retention import RetentionService
from .services.retrieval import RetrievalService, load_embedder
from .services.workflow import WorkflowService


@dataclass
class Services:
    settings: Settings
    db: Database
    workflow: WorkflowService
    retrieval: RetrievalService
    retention: RetentionService
    evaluation: EvaluationService


def build(settings: Settings | None = None, *, setup: bool = True) -> Services:
    settings = settings or get_settings()
    if setup:
        wait_for_database(settings.database_url)
        migrate(settings.database_url)
    db = Database(settings.database_url)
    wf = WorkflowService(db, settings)
    retrieval = RetrievalService(db, load_embedder(settings))
    retention = RetentionService(db, settings.retention_days)
    services = Services(settings, db, wf, retrieval, retention, EvaluationService(wf))
    if setup:
        wf.seed(settings.default_workspace, "Demo workspace")
        retrieval.index_help(ARTICLES)
        retrieval.index_workspace_context(settings.default_workspace)
        retention.purge_expired()
    return services

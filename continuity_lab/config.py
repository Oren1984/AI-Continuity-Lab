"""Runtime settings, read once from the environment. Local-only defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
DEMO_REPO = FIXTURES / "demo_repo"


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "postgresql://continuity:local-only-demo@localhost:5432/continuity"
        )
    )
    retention_days: int = field(default_factory=lambda: int(os.environ.get("RETENTION_DAYS", "30")))
    model_adapter: str = field(default_factory=lambda: os.environ.get("LAB_MODEL_ADAPTER", "fixture"))
    ollama_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://ollama:11434"))
    ollama_model_light: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL_LIGHT", ""))
    ollama_model_heavy: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL_HEAVY", ""))
    log_raw_prompts: bool = field(default_factory=lambda: _bool("LOG_RAW_PROMPTS", False))
    embedding_model: str = field(default_factory=lambda: os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    embedding_cache_dir: str = field(default_factory=lambda: os.environ.get("EMBEDDING_CACHE_DIR", "/opt/models"))
    checkpoint_max_bytes: int = 256 * 1024
    default_workspace: str = "demo"


def get_settings() -> Settings:
    return Settings()

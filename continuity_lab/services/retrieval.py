"""Semantic retrieval with pgvector over Help Center entries and eligible saved context."""
from __future__ import annotations

import hashlib
import math
import re

DIM = 384


class HashingEmbedder:
    """Deterministic fallback used only if the packaged model cannot load. Labeled as such in the UI."""

    name = "hashing-fallback-384"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * DIM
            for tok in re.findall(r"[a-z0-9]+", text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % DIM] += 1.0 if (h >> 9) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FastEmbedder:
    def __init__(self, model: str, cache_dir: str):
        from fastembed import TextEmbedding  # imported lazily: heavy

        self._model = TextEmbedding(model, cache_dir=cache_dir, local_files_only=True)
        self.name = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts)]


def load_embedder(settings):
    try:
        return FastEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    except Exception:  # model missing outside the container, or offline cache miss
        return HashingEmbedder()


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class RetrievalService:
    def __init__(self, db, embedder):
        self.db = db
        self.embedder = embedder

    def index_help(self, articles: list[dict]) -> int:
        texts = [f"{a['title']}. {a['summary']} {a['body']}" for a in articles]
        vecs = self.embedder.embed(texts)
        with self.db.transaction() as conn:
            for a, text, v in zip(articles, texts, vecs):
                conn.execute(
                    "INSERT INTO lab.embeddings (workspace_id, source_type, source_id, content, embedding, model)"
                    " VALUES (NULL, 'help', %s, %s, %s::vector, %s)"
                    " ON CONFLICT (source_type, source_id) WHERE workspace_id IS NULL"
                    " DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding, model = EXCLUDED.model",
                    (a["id"], text[:4000], _vec(v), self.embedder.name),
                )
        return len(articles)

    def index_workspace_context(self, workspace_id: str) -> int:
        """Eligible saved context: project decisions only. Drafts, prompts and credentials are not embedded."""
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT key, decision FROM lab.decisions WHERE workspace_id = %s", (workspace_id,)
            ).fetchall()
        if not rows:
            return 0
        vecs = self.embedder.embed([f"{r['key']}: {r['decision']}" for r in rows])
        with self.db.transaction() as conn:
            for r, v in zip(rows, vecs):
                conn.execute(
                    "INSERT INTO lab.embeddings (workspace_id, source_type, source_id, content, embedding, model)"
                    " VALUES (%s, 'decision', %s, %s, %s::vector, %s)"
                    " ON CONFLICT (workspace_id, source_type, source_id)"
                    " DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding, model = EXCLUDED.model",
                    (workspace_id, r["key"], f"{r['key']}: {r['decision']}", _vec(v), self.embedder.name),
                )
        return len(rows)

    def search(self, query: str, workspace_id: str | None, k: int = 5) -> list[dict]:
        query = (query or "").strip()[:500]
        if not query:
            return []
        v = _vec(self.embedder.embed([query])[0])
        return self.db.fetchall(
            "SELECT source_type, source_id, content, workspace_id, 1 - (embedding <=> %s::vector) AS score"
            " FROM lab.embeddings WHERE workspace_id IS NULL OR workspace_id = %s"
            " ORDER BY embedding <=> %s::vector LIMIT %s",
            (v, workspace_id, v, k),
        )

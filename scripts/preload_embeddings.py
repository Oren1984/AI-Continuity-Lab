"""Build-time step: download the small embedding model into the image."""
import os

from fastembed import TextEmbedding

MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
CACHE = os.environ.get("EMBEDDING_CACHE_DIR", "/opt/models")

model = TextEmbedding(MODEL, cache_dir=CACHE)
vec = next(iter(model.embed(["warm-up"])))
print(f"preloaded {MODEL} dim={len(vec)} into {CACHE}")

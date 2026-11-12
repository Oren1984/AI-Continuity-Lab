FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBEDDING_CACHE_DIR=/opt/models \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Preload the small embedding model at build time so startup needs no model setup step.
COPY scripts/preload_embeddings.py scripts/preload_embeddings.py
RUN python scripts/preload_embeddings.py

COPY . .

RUN useradd --create-home --uid 10001 lab \
    && chown -R lab:lab /app \
    && chmod -R a+rX /opt/models
USER lab

EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["python", "-m", "continuity_lab.entrypoint"]

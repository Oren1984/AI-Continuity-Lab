"""Container entrypoint: wait for the database, migrate, seed, then serve Streamlit."""
from __future__ import annotations

import os
import sys

from .bootstrap import build


def main() -> None:
    if len(sys.argv) > 1:  # e.g. `docker compose run --rm app pytest -q`
        os.execvp(sys.argv[1], sys.argv[1:])
    services = build()
    print(f"[lab] ready: {services.db.ready()[1]}; embedder={services.retrieval.embedder.name}", flush=True)
    services.db.close()
    os.execvp(
        "streamlit",
        ["streamlit", "run", "app/main.py", "--server.address=0.0.0.0", "--server.port=8501",
         "--server.headless=true", "--browser.gatherUsageStats=false", "--server.maxUploadSize=1"],
    )


if __name__ == "__main__":
    main()

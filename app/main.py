"""AI Continuity Lab — Streamlit entry point."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import footer, services, show_flash  # noqa: E402
from views import help_center, journey, operations, results  # noqa: E402

st.set_page_config(page_title="AI Continuity Lab", page_icon="🧭", layout="wide")

PAGES = {
    "journey": ("🧭 Journey", journey.render),
    "results": ("📊 Results", results.render),
    "operations": ("🛠️ Operations", operations.render),
    "help": ("❓ Help Center", help_center.render),
}


def sidebar(svc) -> tuple[str, str]:
    qp = st.query_params
    with st.sidebar:
        st.markdown("### AI Continuity Lab")
        st.caption("What was saved? What was verified? What can I do now?")
        keys = list(PAGES)
        default = keys.index(qp.get("page", "journey")) if qp.get("page") in PAGES else 0
        page = st.radio("Go to", keys, index=default, format_func=lambda k: PAGES[k][0],
                        label_visibility="collapsed", key="nav")
        if page != qp.get("page"):
            qp["page"] = page
        st.divider()
        with svc.db.transaction() as conn:
            workspaces = [w["id"] for w in svc.workflow.repo.list_workspaces(conn) if w["id"] != "eval"]
        current = qp.get("ws", svc.settings.default_workspace)
        if current not in workspaces:
            current = svc.settings.default_workspace
        ws = st.selectbox("Workspace", workspaces, index=workspaces.index(current),
                          help="Runs, checkpoints and releases are scoped to one workspace.")
        if ws != qp.get("ws"):
            if qp.get("ws") and ws != qp.get("ws") and "run" in qp:
                del qp["run"]
            qp["ws"] = ws
        with st.expander("New workspace"):
            new = st.text_input("Workspace ID", placeholder="e.g. second-laptop",
                                help="Lowercase letters, digits and dashes.")
            if st.button("Create workspace"):
                if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", new or ""):
                    svc.workflow.seed(new, new)
                    svc.retrieval.index_workspace_context(new)
                    qp["ws"] = new
                    qp.pop("run", None)
                    st.rerun()
                else:
                    st.error("Use 2–40 lowercase letters, digits or dashes.")
        ok, detail = svc.db.ready()
        st.caption(f"{'🟢' if ok else '🔴'} Database {'ready' if ok else 'not ready'} · {detail}")
        st.caption(f"Model adapter: fixtures (simulated) · Embeddings: {svc.retrieval.embedder.name}")
    return page, ws


def main() -> None:
    svc = services()
    page, ws = sidebar(svc)
    show_flash()
    PAGES[page][1](svc, ws)
    footer()


main()

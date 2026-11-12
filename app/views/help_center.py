"""In-app Help Center with semantic search (pgvector)."""
from __future__ import annotations

import streamlit as st

from continuity_lab.help_content import ARTICLES, GLOSSARY, WALKTHROUGHS


def render(svc, ws: str) -> None:
    st.markdown("## Help Center")
    st.write("Plain-English answers about what this lab shows, why continuity matters, and how to use it. "
             "To try it, go to **🧭 Journey**, keep *Normal run*, and press **Start run**.")
    q = st.text_input("Search help and your saved project decisions", placeholder="e.g. what does unknown mean?")
    if q:
        hits = svc.retrieval.search(q, ws, k=4)
        st.caption(f"Semantic search over Help Center and decisions in “{ws}” · embeddings: "
                   f"{svc.retrieval.embedder.name}")
        by_id = {a["id"]: a for a in ARTICLES}
        for h in hits:
            if h["source_type"] == "help" and h["source_id"] in by_id:
                a = by_id[h["source_id"]]
                st.markdown(f"**{a['title']}** · score {h['score']:.2f}  \n{a['summary']}")
            else:
                st.markdown(f"**Project decision** · score {h['score']:.2f}  \n{h['content']}")
    st.markdown("### Articles")
    for a in ARTICLES:
        with st.expander(f"{a['title']} — {a['summary']}"):
            st.write(a["body"])
    st.markdown("### Short walkthroughs")
    for title, steps in WALKTHROUGHS:
        with st.expander(title):
            for i, s in enumerate(steps, 1):
                st.markdown(f"{i}. {s}")
    st.markdown("### Glossary")
    st.dataframe([{"Term": k, "Meaning": v} for k, v in GLOSSARY.items()], hide_index=True, width="stretch")

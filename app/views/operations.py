"""Operations view: readiness, simulated dependencies, errors, latency, unresolved operations, data controls."""
from __future__ import annotations

import resource

import streamlit as st

from continuity_lab.services import scenarios
from continuity_lab.services.providers import DEPENDENCIES, PROFILES, profile_state


def render(svc, ws: str) -> None:
    st.markdown("## Operations")
    db = svc.db
    ok, detail = db.ready()
    c = st.columns(4)
    c[0].metric("App", "ready")
    c[1].metric("Database", "ready" if ok else "NOT READY", detail)
    c[2].metric("Embeddings", "loaded", svc.retrieval.embedder.name)
    unresolved = db.fetchall(
        "SELECT o.id, o.status, o.run_id, o.updated_at FROM lab.operations o JOIN lab.runs r ON r.id = o.run_id"
        " WHERE r.workspace_id = %s AND o.status IN ('unknown', 'pending') ORDER BY o.updated_at", (ws,))
    c[3].metric("Unresolved operations", len(unresolved))
    if unresolved:
        st.error(f"🚨 ALERT: {len(unresolved)} publication(s) need reconciliation. Open the run and press "
                 "**Check publication status**.")
        st.dataframe([{"operation": str(u["id"]), "status": u["status"].upper(), "run": str(u["run_id"])[:8],
                       "since": u["updated_at"].strftime("%H:%M:%S")} for u in unresolved], hide_index=True)

    st.markdown("### Simulated providers and dependencies")
    sid = st.selectbox("Show state under scenario", list(scenarios.SCENARIOS),
                       format_func=lambda s: scenarios.SCENARIOS[s].title)
    faults = scenarios.get(sid).faults
    st.dataframe([
        {"Dependency (fictional)": d.name, "Layer": d.layer.replace("_", " "),
         "State": "DOWN" if d.id in faults.down_dependencies else "up",
         "Used by": ", ".join(p.name for p in PROFILES.values() if d.id in p.dependencies)}
        for d in DEPENDENCIES.values()], hide_index=True, width="stretch")
    st.dataframe([
        {"Profile (simulated)": p.name, "Available": (s := profile_state(pid, p.default_model, faults)).available,
         "Capabilities": ", ".join(sorted(s.capabilities)), "Active model": s.active_model,
         "Changes": "; ".join(s.capability_changes) or "—"}
        for pid, p in PROFILES.items()], hide_index=True, width="stretch")

    st.markdown("### Errors, response times and recovery (this workspace)")
    counts = db.fetchall(
        "SELECT event_type, count(*) AS n FROM lab.events WHERE workspace_id = %s AND event_type IN"
        " ('adapter_stalled','adapter_unavailable','adapter_error','publication_outcome_unknown',"
        "'interruption_simulated','policy_check') GROUP BY event_type ORDER BY event_type", (ws,))
    denials = db.fetchone("SELECT count(*) AS n FROM lab.events WHERE workspace_id = %s AND event_type = 'policy_check'"
                          " AND detail->>'allowed' = 'false'", (ws,))["n"]
    lat = db.fetchone(
        "SELECT round(avg((detail->>'latency_ms')::int)) AS avg_ms, max((detail->>'latency_ms')::int) AS max_ms,"
        " sum((detail->>'tokens_in')::int) AS tin, sum((detail->>'tokens_out')::int) AS tout,"
        " bool_and((detail->>'simulated')::boolean) AS sim, count(*) AS calls"
        " FROM lab.events WHERE workspace_id = %s AND event_type = 'adapter_call'", (ws,))
    rec = db.fetchone("SELECT round(avg(recovery_time_ms)/1000.0, 1) AS s FROM lab.eval_results "
                      "WHERE comparison = 'recovery' AND condition = 'continuity'")
    m = st.columns(4)
    m[0].metric("Policy denials", denials)
    m[1].metric("Adapter calls", lat["calls"])
    m[2].metric("Avg response (ms)", lat["avg_ms"] if lat["avg_ms"] is not None else "unavailable",
                "simulated" if lat["sim"] else None)
    m[3].metric("Avg recovery (s, modeled)", rec["s"] if rec["s"] is not None else "unavailable")
    st.dataframe([{"event": r["event_type"], "count": r["n"]} for r in counts], hide_index=True)

    st.markdown("### Costs")
    ru = resource.getrusage(resource.RUSAGE_SELF)
    st.markdown(
        f"- Tokens in/out (this workspace): **{lat['tin'] or 0} / {lat['tout'] or 0}** "
        f"{'(simulated fixture counts)' if lat['sim'] else ''}\n"
        f"- Local compute, app process: CPU {ru.ru_utime + ru.ru_stime:.1f} s, peak memory {ru.ru_maxrss / 1024:.0f} MB\n"
        "- Cost estimate: **unavailable** — fixture tokens are simulated and no price per token is configured. "
        "A real estimate would need provider prices (currency per 1M tokens) and live token counts."
    )

    st.markdown("### Simulated target (local release registry)")
    rel = svc.workflow.target.list_releases(ws)
    st.caption(f"{len(rel)} release(s) in workspace “{ws}”. One per operation, enforced by a unique idempotency key.")
    if rel:
        st.dataframe(rel, hide_index=True, width="stretch")

    st.markdown("### Data controls")
    st.markdown(f"Retention: runs older than **{svc.settings.retention_days} days** are deleted at startup "
                "(audit events 7 days later). See docs/CONTROLS.md for what is covered.")
    d1, d2 = st.columns(2)
    if d1.button("Purge expired records now"):
        st.success(f"Purged: {svc.retention.purge_expired()}")
    confirm = d2.checkbox(f"I understand this deletes every demo record in “{ws}”")
    if d2.button("Delete demo records for this workspace", disabled=not confirm):
        res = svc.retention.delete_workspace_records(ws)
        st.query_params.pop("run", None)
        st.success(f"Deleted: {res}. A content-free audit tombstone was kept.")

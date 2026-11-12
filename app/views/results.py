"""Compact results view for the two controlled comparisons."""
from __future__ import annotations

import streamlit as st

from continuity_lab.services.adapters import FIXTURE_MODELS, model_catalog
from continuity_lab.services.evaluation import HUMAN_SECONDS

COLS = {
    "runs": "Runs", "completion_pct": "Completed %", "repeated_steps": "Repeated steps (avg)",
    "recovery_s_modeled": "Recovery s (modeled)", "necessary_q": "Necessary Qs", "unnecessary_q": "Unnecessary Qs",
    "missed_q": "Missed Qs", "unsupported_claims": "Unsupported claims made",
    "accepted_unsupported": "Claims accepted w/o evidence", "duplicates": "Duplicate releases",
    "latency_ms": "Latency ms (avg)", "tokens": "Tokens (avg)",
}


def _table(rows: list[dict], keys: list[str]) -> None:
    shown = []
    for r in rows:
        out = {k.replace("_", " ").capitalize(): r[k] for k in keys}
        for k, label in COLS.items():
            v = r[k]
            if v is None:
                v = "— (no failure)" if k == "recovery_s_modeled" else "unavailable"
            out[label] = float(v) if hasattr(v, "is_finite") else v
        out["Source"] = "simulation (fixture)" if r["simulated"] else "live local model"
        shown.append(out)
    st.dataframe(shown, hide_index=True, width="stretch")


def _total(rows, key):
    return sum((r[key] or 0) for r in rows)


def render(svc, ws: str) -> None:
    ev = svc.evaluation
    st.markdown("## Results")
    st.write("Two controlled comparisons, run through the real workflow, database and simulated target. "
             "Completion is scored from saved state and target receipts. Fixture results are **simulation "
             "results** from deterministic models: repetitions are identical by design.")
    c1, c2, c3 = st.columns([2, 2, 1])
    reps = c3.number_input("Repetitions", 1, 5, 3)
    available = [m for m in model_catalog(svc.settings) if m["status"].startswith("available")]
    if c1.button("Run model comparison", type="primary", width="stretch"):
        with st.spinner("Running model comparison…"):
            ev.run_model_comparison([m["id"] for m in available], int(reps))
        st.rerun()
    if c2.button("Run recovery comparison", type="primary", width="stretch"):
        with st.spinner("Running recovery comparison…"):
            ev.run_recovery_comparison("fixture-advanced", int(reps))
        st.rerun()
    batches = ev.latest_batches()

    st.markdown("### 1 · Model comparison")
    st.caption("Changes only the model. Prompt, scenario, starting state, tool access, recovery features "
               "(continuity) and rubric are held constant.")
    if batches["model"]:
        rows = ev.summary(batches["model"], ("model_id", "scenario", "task_variant"))
        _table(rows, ["model_id", "scenario", "task_variant"])
        by_model = {}
        for r in rows:
            by_model.setdefault(r["model_id"], []).append(r)
        if len(by_model) >= 2:
            parts = []
            for m, rs in by_model.items():
                parts.append(f"**{m}**: {_total(rs, 'unnecessary_q')} unnecessary and {_total(rs, 'missed_q')} "
                             f"missed questions, {_total(rs, 'unsupported_claims')} unsupported claims, "
                             f"{sum(float(r['repeated_steps']) * r['runs'] for r in rs):.0f} repeated steps")
            st.markdown("**From model behavior:** " + " · ".join(parts) + ". Completion stays the same because the "
                        "verifier and policy catch the weaker model's mistakes — at the cost of rework.")
    else:
        st.info("Not run yet. Press **Run model comparison**.")

    st.markdown("### 2 · Recovery comparison")
    st.caption("Holds the model (fixture-advanced) and scenario constant. Baseline = no checkpoint, no transfer, "
               "no evidence beside claims. Approval and idempotent publication are enforced in both.")
    if batches["recovery"]:
        rows = ev.summary(batches["recovery"], ("scenario", "condition"))
        _table(rows, ["scenario", "condition"])
        base = [r for r in rows if r["condition"] == "baseline"]
        cont = [r for r in rows if r["condition"] == "continuity"]
        rep_b = sum(float(r["repeated_steps"]) * r["runs"] for r in base)
        rep_c = sum(float(r["repeated_steps"]) * r["runs"] for r in cont)
        st.markdown(
            f"**From preserving and verifying the work:** repeated steps {rep_b:.0f} → {rep_c:.0f}, duplicate "
            f"releases {_total(base, 'duplicates')} → {_total(cont, 'duplicates')}, claims accepted without "
            f"evidence {_total(base, 'accepted_unsupported')} → {_total(cont, 'accepted_unsupported')}."
        )
    else:
        st.info("Not run yet. Press **Run recovery comparison**.")

    with st.expander("Models, settings and measurement notes"):
        st.dataframe(model_catalog(svc.settings), hide_index=True, width="stretch")
        st.markdown(
            f"- Fixture models: {', '.join(FIXTURE_MODELS)} — deterministic; latency and tokens are simulated.\n"
            "- Live local comparison: **pending** unless both Ollama models show *available*. See docs/EVALUATION.md.\n"
            f"- Recovery time = measured system time + modeled human time per action (seconds): {HUMAN_SECONDS}.\n"
            "- Each episode runs in the isolated `eval` workspace and is deleted by retention like any run."
        )

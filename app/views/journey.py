"""Home screen: the user journey with a persistent status card."""
from __future__ import annotations

import json

import streamlit as st

from common import badge, flash
from continuity_lab.services import scenarios
from continuity_lab.services.adapters import FIXTURE_MODELS
from continuity_lab.services.checkpoint import CheckpointImportError, parse_import
from continuity_lab.services.providers import DEPENDENCIES, PROFILES, profile_state
from continuity_lab.services.workflow import PolicyDenied, SimulatedInterruption, ToolFailure


def _act(fn, *args, ok: str | None = None, **kwargs):
    """Run one service call; turn known failures into plain-English messages."""
    try:
        result = fn(*args, **kwargs)
        if ok:
            flash("success", ok)
        return result
    except PolicyDenied as exc:
        flash("warning", f"Blocked by policy ({exc.rule}): {exc.reason}")
    except ToolFailure as exc:
        st.session_state["tool_failure"] = {"kind": exc.kind, "message": exc.message, "routes": exc.routes}
    except SimulatedInterruption as exc:
        # Simulate the interface disappearing: drop everything held in this browser session.
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.session_state["interrupted"] = str(exc)
    return None


def _start_panel(svc, ws: str) -> None:
    st.markdown("## Prepare a demo release with AI — and keep going when something breaks")
    st.write("Pick a scenario and press **Start run**. The *Normal run* is the happy path. "
             "Everything is local and simulated; no API keys, no external services.")
    c1, c2 = st.columns([3, 2])
    with c1:
        sid = st.selectbox("Scenario", list(scenarios.SCENARIOS),
                           format_func=lambda s: scenarios.SCENARIOS[s].title, key="pick_scenario")
        sc = scenarios.SCENARIOS[sid]
        st.markdown(f"**What you will see:** {sc.sees}  \n**Expected behavior:** {sc.expected}  \n"
                    f"*Failure layer:* {sc.layer.replace('_', ' ')}")
    with c2:
        tool = st.selectbox("Tool profile (simulated)", list(PROFILES), format_func=lambda p: PROFILES[p].name)
        model = st.selectbox("Model", list(FIXTURE_MODELS), index=1,
                             format_func=lambda m: FIXTURE_MODELS[m].label)
        if st.button("▶ Start run", type="primary", width="stretch"):
            run_id = svc.workflow.start_run(ws, scenario=sid, tool_profile=tool, model_id=model)
            st.query_params["run"] = run_id
            st.session_state.pop("tool_failure", None)
            st.rerun()
    with svc.db.transaction() as conn:
        runs = svc.workflow.repo.list_runs(conn, ws)
    if runs:
        with st.expander(f"Resume a run ({len(runs)} saved in workspace “{ws}”)", expanded=False):
            labels = {str(r["id"]): f"{r['created_at']:%H:%M:%S} · {scenarios.SCENARIOS[r['scenario']].title} · "
                                    f"stage {r['stage']} · {str(r['id'])[:8]}" for r in runs}
            pick = st.selectbox("Saved runs", list(labels), format_func=labels.get)
            if st.button("Resume this run"):
                st.query_params["run"] = pick
                st.rerun()


def _status_card(card: dict) -> None:
    with st.container(border=True):
        st.markdown("#### Status")
        st.markdown("**💾 Saved work**")
        if card["saved"]:
            for text, _ in card["saved"]:
                st.markdown(f"- {text}")
        else:
            st.markdown("- Nothing yet beyond the run itself.")
        if card["unconfirmed"]:
            for u in card["unconfirmed"]:
                st.markdown(f"- ⚠️ UNCONFIRMED: {u}")
        st.markdown("**🔎 Verified evidence**")
        if card["verified"]:
            for name, label, source in card["verified"]:
                st.markdown(f"- {name}: **{badge(label)}**  \n  <small>source: {source}</small>",
                            unsafe_allow_html=True)
        else:
            st.markdown("- No evidence yet.")
        st.markdown("**➡️ Next action**")
        st.info(card["next"][1])
        t = card["tool"]
        st.caption(f"Active route: {t['name']} (simulated) · {'available' if t['available'] else 'UNAVAILABLE'} · "
                   f"model {t['model']}")
        if t["changes"]:
            st.caption("Capability changes: " + "; ".join(t["changes"]))


def _tool_failure_panel(svc, ws, run_id, run) -> None:
    tf = st.session_state.get("tool_failure")
    if not tf:
        return
    with st.container(border=True):
        st.error(("⏳ Stalled: " if tf["kind"] == "stalled" else "⛔ Unavailable: ") + tf["message"])
        st.write("Your request is **saved** with the last checkpoint. Choose how to continue:")
        cols = st.columns(4)
        if cols[0].button("↻ Retry", key="tf_retry"):
            st.session_state.pop("tool_failure", None)
            _act(svc.workflow.send_instruction, run_id, ws, run["pending_input"] or run["goal"])
            st.rerun()
        if tf["routes"]:
            route = cols[1].selectbox("Independent route", tf["routes"], format_func=lambda p: PROFILES[p].name,
                                      key="tf_route", label_visibility="collapsed")
            if cols[2].button(f"Switch to {PROFILES[route].name}", key="tf_switch"):
                st.session_state.pop("tool_failure", None)
                svc.workflow.switch_tool(run_id, ws, route)
                _act(svc.workflow.send_instruction, run_id, ws, run["pending_input"] or run["goal"])
                st.rerun()
        if cols[3].button("Continue manually", key="tf_manual"):
            st.session_state.pop("tool_failure", None)
            st.session_state["show_manual"] = True
            st.rerun()


def _profiles_table(run) -> None:
    faults = scenarios.get(run["scenario"]).faults
    rows = []
    for pid, p in PROFILES.items():
        stt = profile_state(pid, p.default_model, faults)
        rows.append({
            "Profile (simulated)": p.name + (" ← active" if pid == run["tool_profile"] else ""),
            "Status": "available" if stt.available else "UNAVAILABLE",
            "Repository access": "yes" if stt.has_repo_access else "no",
            "Fictional dependencies": ", ".join(DEPENDENCIES[d].name for d in p.dependencies),
            "Why": stt.reason or "; ".join(stt.capability_changes) or "—",
        })
    st.dataframe(rows, hide_index=True, width="stretch")


def _conversation(svc, ws, run_id, snap) -> None:
    with svc.db.transaction() as conn:
        msgs = svc.workflow.repo.messages(conn, run_id)
    verified_now = svc.workflow.policy.is_verified_current(snap)
    for m in msgs:
        with st.chat_message("user" if m["role"] == "user" else "assistant"):
            st.write(m["content"])
            if m["role"] == "assistant":
                meta = f"{PROFILES.get(m['tool_profile']).name if m['tool_profile'] in PROFILES else ''} · " \
                       f"{m['model_id']} · {m['latency_ms']} ms · {m['tokens_in']}→{m['tokens_out']} tokens"
                st.caption(meta + (" · simulated" if m["simulated"] else ""))
                for c in m["claims"]:
                    if verified_now:
                        st.caption("Claim “sign-in verified”: evidence now exists from the verifier — but the "
                                   "claim itself was made without evidence.")
                    else:
                        st.warning("Claim “sign-in verified” is UNSUPPORTED: no verifier evidence for the "
                                   "current draft revision. Do not rely on it.")


def _draft_and_evidence(svc, ws, run_id, snap, card) -> None:
    d = snap.draft
    if not d:
        return
    c = d["content"]
    ev = snap.signin_evidence
    with st.container(border=True):
        st.markdown(f"#### Draft · revision {d['revision']}  <small>({d['author']}, hash {d['content_hash']})</small>",
                    unsafe_allow_html=True)
        st.markdown(f"**{c['version']} — {c['title']}**  \n{c['notes']}")
        s = c["signin"]
        if s.get("mode") == "credentials":
            st.markdown(f"Sign-in shown to visitors: `{s.get('email')}` / `{s.get('password')}` *(synthetic)*")
        else:
            st.markdown(f"Sign-in: **{s.get('mode')}**")
        left, right = st.columns([3, 2])
        with left:
            from continuity_lab.services.verifier import signin_claim
            st.markdown(f"**Claim:** {signin_claim(c)}")
        with right:
            if svc.workflow.policy.is_verified_current(snap):
                st.success(f"{badge('VERIFIED')} · {ev['source']} · fixture {ev['detail'].get('fixture_hash')}")
            elif ev:
                label = ev["status"].replace("_", " ").upper()
                st.warning(f"{badge(label)} · {ev['source']}"
                           + (f" · {ev['detail'].get('output', {}).get('reason', '')}" if ev["status"] == "failed" else ""))
            else:
                st.caption(f"{badge('NOT CHECKED')} for revision {d['revision']}")
        b1, b2 = st.columns(2)
        if b1.button("🔎 Verify sign-in (via active tool)", key="verify", width="stretch"):
            _act(svc.workflow.verify, run_id, ws, ok="Verification recorded.")
            st.rerun()
        if b2.button("🧑 Run manual check (no AI tool)", key="verify_manual", width="stretch"):
            _act(svc.workflow.verify, run_id, ws, route="manual", ok="Manual check recorded.")
            st.rerun()
        with st.expander("Edit draft (creates a new revision; invalidates approval and earlier verification)"):
            with st.form("edit"):
                mode = st.radio("Sign-in mode", ["credentials", "one_click"],
                                index=0 if s.get("mode") == "credentials" else 1, horizontal=True)
                email = st.text_input("Demo email", s.get("email") or "visitor@demo.local")
                pw = st.text_input("Demo password (synthetic)", s.get("password") or "")
                notes = st.text_area("Release notes", c["notes"], max_chars=2000)
                if st.form_submit_button("Save new revision"):
                    new = dict(c, notes=notes, signin={"mode": mode, "email": email if mode == "credentials" else None,
                                                       "password": pw if mode == "credentials" else None})
                    _act(svc.workflow.edit_draft, run_id, ws, new, ok="Saved as a new revision.")
                    st.rerun()


def _approval_and_publication(svc, ws, run_id, snap) -> None:
    if not snap.draft:
        return
    policy = svc.workflow.policy
    op = snap.operation
    with st.container(border=True):
        st.markdown("#### Approval and publication · target: local release registry (simulated)")
        a = snap.active_approval
        if a:
            st.markdown(f"💾 Approved by **{a['approved_by']}** for revision {a['draft_revision']} "
                        f"(hash {a['content_hash']}) → `{a['target']}`")
        elif not op or op["status"] == "not_written":
            dec = policy.can_approve(snap)
            st.button("✔ Approve publication of this revision", key="approve", disabled=not dec.allowed,
                      type="primary", on_click=lambda: _act(svc.workflow.approve, run_id, ws,
                                                            ok="Approval recorded."))
            if not dec.allowed:
                st.caption(f"Approval blocked: {dec.reason}")
        if op is None or op["status"] == "not_written":
            dec = policy.can_publish(snap)
            label = ("↻ Retry publication (same operation ID)" if op and op["status"] == "not_written"
                     else "🚀 Publish")
            if st.button(label, key="publish", disabled=not dec.allowed):
                res = _act(svc.workflow.publish, run_id, ws)
                if res:
                    flash("success" if res.outcome == "published" else "warning", res.message)
                st.rerun()
            if not dec.allowed and a:
                st.caption(f"Publication blocked: {dec.reason}")
            if op and op["status"] == "not_written":
                st.info(f"Reconciliation found **nothing written** for operation `{op['id']}`. A retry reuses this "
                        "operation ID, so it can create at most one release.")
        if op and op["status"] in ("unknown", "pending"):
            st.error(f"🚨 {badge('UNKNOWN')} outcome for operation `{op['id']}` (idempotency key "
                     f"`{op['idempotency_key']}`). The result was lost. Do **not** publish again by other means: "
                     "check the target first.")
            if st.button("🔍 Check publication status", key="reconcile", type="primary"):
                status = _act(svc.workflow.reconcile, run_id, ws)
                if status == "confirmed":
                    flash("success", "The target has the release. Receipt recorded from reconciliation — no retry needed.")
                elif status == "not_written":
                    flash("warning", "The target has no release for this operation. You can retry safely.")
                st.rerun()
        if op and op["status"] == "confirmed":
            r = snap.receipt["receipt"] if snap.receipt else {}
            st.success(f"{badge('CONFIRMED')} release `{r.get('release_id')}` · {r.get('version')} · "
                       f"obtained via {snap.receipt['obtained_via'] if snap.receipt else '?'}")
            if st.button("🔍 Check publication status", key="check_pub"):
                ev = _act(svc.workflow.check_publication, run_id, ws, actor="person")
                if ev:
                    flash("success" if ev["status"] == "verified" else "error",
                          f"Receipt check: {ev['status'].upper()} — {ev['source']}")
                st.rerun()
            releases = [x for x in svc.workflow.target.list_releases(ws)
                        if x["idempotency_key"] == op["idempotency_key"]]
            st.caption(f"Releases in the target for this operation: **{len(releases)}**")


def _checkpoint_tools(svc, ws, run_id) -> None:
    with st.container(border=True):
        st.markdown("#### Checkpoint and manual continuation")
        c1, c2 = st.columns(2)
        payload = svc.workflow.export_checkpoint(run_id, ws)
        c1.download_button("⬇ Download checkpoint (JSON)", json.dumps(payload, indent=2, default=str),
                           file_name=f"continuity-{str(run_id)[:8]}.checkpoint.json", mime="application/json",
                           width="stretch", on_click=svc.workflow.log_export, args=(run_id, ws))
        if c2.button("📋 Continue manually", width="stretch"):
            st.session_state["show_manual"] = not st.session_state.get("show_manual", False)
        if st.session_state.get("show_manual"):
            m = svc.workflow.manual_checklist(run_id, ws)
            for title, key in (("Saved work", "saved_work"), ("Outstanding checks", "outstanding_checks"),
                               ("Unresolved actions", "unresolved_actions"), ("Next steps", "next_steps")):
                st.markdown(f"**{title}**")
                for item in m[key] or ["—"]:
                    st.markdown(f"- {item}")
            snap = svc.workflow.snapshot(run_id, ws)
            if not snap.draft:
                mode = st.radio("Create the draft yourself from the template", ["credentials", "one_click"],
                                horizontal=True, key="manual_mode")
                if st.button("Create draft manually"):
                    _act(svc.workflow.create_manual_draft, run_id, ws, mode, ok="Draft saved (manual).")
                    st.rerun()


def _import_panel(svc, ws) -> None:
    with st.expander("Import a checkpoint (resume, or use as context in this workspace)"):
        up = st.file_uploader("Checkpoint JSON", type=["json"], key="cp_upload")
        if up is not None and st.button("Validate and import"):
            try:
                cp = parse_import(up.getvalue(), max_bytes=svc.settings.checkpoint_max_bytes)
            except CheckpointImportError as exc:
                st.error(f"Rejected: {exc}")
                return
            res = svc.workflow.import_checkpoint(cp, ws)
            st.query_params["run"] = res["run_id"]
            flash("success", "Resumed the original run from PostgreSQL." if res["mode"] == "resume" else
                  "Started a new run using the checkpoint as context. Imported approvals and receipts are "
                  "informational until resolved here; verify again before approving.")
            st.rerun()


def _imported_context(svc, ws, run_id, run) -> None:
    ctx = run.get("context_from_checkpoint")
    if not ctx:
        return
    with st.container(border=True):
        st.markdown(f"#### Imported context (informational) · from run `{ctx['imported_from_run'][:8]}` in "
                    f"workspace `{ctx['imported_from_workspace']}`")
        st.caption(ctx["note"])
        if ctx.get("approval"):
            st.markdown(f"- Imported approval for revision {ctx['approval']['draft_revision']} — **not valid here**.")
        op = ctx.get("operation")
        if op:
            st.markdown(f"- Imported operation `{op['operation_id']}` with status **{op['status'].upper()}**.")
            if st.button("Check the imported operation at the target (this workspace only)"):
                res = svc.workflow.check_imported_operation(run_id, ws)
                flash("info", f"Imported operation: {res['status'].replace('_', ' ')}"
                      + (f" — release {res['receipt']['release_id']}" if res.get("receipt") else ""))
                st.rerun()


def _timeline(svc, run_id) -> None:
    with st.expander("Event timeline (technical details)"):
        with svc.db.transaction() as conn:
            events = svc.workflow.repo.events(conn, run_id)
        st.dataframe(
            [{"time": e["created_at"].strftime("%H:%M:%S.%f")[:-3], "event": e["event_type"], "actor": e["actor"],
              "detail": json.dumps(e["detail"], default=str)[:300]} for e in events],
            hide_index=True, width="stretch",
        )


def render(svc, ws: str) -> None:
    if st.session_state.get("interrupted"):
        st.error("🔌 Connection lost. " + st.session_state["interrupted"])
        st.write("This browser session was cleared, as if the page or power went away. Your work up to the last "
                 "committed checkpoint is in PostgreSQL. Reload to restore it — or open this URL in another browser.")
        if st.button("Reload and restore", type="primary"):
            del st.session_state["interrupted"]
            flash("info", "Restored from the last committed checkpoint in PostgreSQL. Steps that started but were "
                          "not confirmed are marked UNCONFIRMED.")
            st.rerun()
        return

    _start_panel(svc, ws)
    _import_panel(svc, ws)
    run_id = st.query_params.get("run")
    if not run_id:
        st.info("Start a run above. New here? Open **Help Center → Run the happy path**.")
        return
    try:
        snap = svc.workflow.snapshot(run_id, ws)
    except Exception:
        st.warning("That run is not in this workspace.")
        return
    run = snap.run
    card = svc.workflow.status_card(run_id, ws)
    st.divider()
    sc = scenarios.SCENARIOS[run["scenario"]]
    st.markdown(f"### Run `{str(run_id)[:8]}` · {sc.title} · task: *{run['task_variant']}*")
    main, side = st.columns([3, 2], gap="large")
    with side:
        _status_card(card)
        with st.expander("Tool profiles and fictional dependencies"):
            _profiles_table(run)
            new_tool = st.selectbox("Switch active tool", list(PROFILES), index=list(PROFILES).index(run["tool_profile"]),
                                    format_func=lambda p: PROFILES[p].name, key="switch_any")
            if new_tool != run["tool_profile"] and st.button("Switch"):
                svc.workflow.switch_tool(run_id, ws, new_tool)
                st.rerun()
    with main:
        _imported_context(svc, ws, run_id, run)
        _conversation(svc, ws, run_id, snap)
        _tool_failure_panel(svc, ws, run_id, run)
        if run["pending_question"]:
            q = run["pending_question"]
            with st.container(border=True):
                st.markdown(f"**❔ {q['text']}**")
                choice = st.radio("Your answer", list(q["options"]), format_func=q["options"].get, key="q_choice")
                if st.button("Answer", type="primary"):
                    _act(svc.workflow.answer_question, run_id, ws, choice)
                    st.rerun()
        if not snap.draft and not run["pending_question"] and not st.session_state.get("tool_failure"):
            text = st.text_area("Task for the assistant", value=run["pending_input"] or run["goal"], height=90)
            if st.button("💬 Send task", type="primary"):
                _act(svc.workflow.send_instruction, run_id, ws, text)
                st.rerun()
        _draft_and_evidence(svc, ws, run_id, snap, card)
        _approval_and_publication(svc, ws, run_id, snap)
        _checkpoint_tools(svc, ws, run_id)
        _timeline(svc, run_id)

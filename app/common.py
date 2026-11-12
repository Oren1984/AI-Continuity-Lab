"""Shared UI helpers. Rendering only: every state change goes through the service layer."""
from __future__ import annotations

import streamlit as st

from continuity_lab import CREDIT
from continuity_lab.bootstrap import Services, build

BADGE = {
    "VERIFIED": "✅ VERIFIED", "CONFIRMED": "✅ CONFIRMED", "FAILED": "❌ FAILED",
    "NOT VERIFIED": "⚠️ NOT VERIFIED", "NOT CHECKED": "○ NOT CHECKED", "UNKNOWN": "❓ UNKNOWN",
    "IN PROGRESS / UNKNOWN": "❓ IN PROGRESS / UNKNOWN", "NOT WRITTEN": "↺ NOT WRITTEN", "saved": "💾 SAVED",
    "STALE": "⌛ STALE",
}


@st.cache_resource(show_spinner="Connecting to the local database…")
def services() -> Services:
    # The container entrypoint already migrated and seeded; setup is idempotent and safe to repeat.
    return build(setup=True)


def badge(label: str) -> str:
    return BADGE.get(label, label)


def flash(kind: str, text: str) -> None:
    st.session_state.setdefault("_flash", []).append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state.pop("_flash", []):
        getattr(st, kind)(text)


def footer() -> None:
    st.divider()
    st.caption(f"{CREDIT} · Local demo with synthetic data. Tool profiles are simulations; "
               "nothing is sent to any external service.")

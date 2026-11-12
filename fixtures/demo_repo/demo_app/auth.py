"""Synthetic demo application used as the seeded repository fixture.

Everything here is synthetic. The demo account exists only inside this fixture.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_USERS = Path(__file__).with_name("users.json")
_SETTINGS = Path(__file__).with_name("settings.json")


def _hash(salt: str, password: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sign_in(email: str, password: str) -> dict:
    """Credential sign-in for the demo visitor account."""
    users = _load(_USERS)["users"]
    for user in users:
        if user["email"] == email:
            if _hash(user["salt"], password) == user["password_hash"]:
                return {"ok": True, "session_for": user["email"], "role": user["role"]}
            return {"ok": False, "reason": "wrong password"}
    return {"ok": False, "reason": "unknown account"}


def one_click_entry() -> dict:
    """One-click entry, only when enabled in settings."""
    settings = _load(_SETTINGS)
    if not settings.get("one_click_demo_entry", False):
        return {"ok": False, "reason": "one-click demo entry is disabled in settings.json"}
    return {"ok": True, "session_for": "visitor@demo.local", "role": "visitor"}

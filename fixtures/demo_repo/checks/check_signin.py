"""Fixture check: can a visitor sign in to the demo the way the release draft claims?

Run as a subprocess by the lab's verifier. Reads its request as JSON on stdin and
prints one JSON result line. Uses only the standard library.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo_app import auth  # noqa: E402


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    mode = request.get("mode")
    if mode == "credentials":
        result = auth.sign_in(request.get("email", ""), request.get("password", ""))
    elif mode == "one_click":
        result = auth.one_click_entry()
    else:
        result = {"ok": False, "reason": f"unsupported sign-in mode: {mode!r}"}
    result["check"] = "demo_signin"
    result["mode"] = mode
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

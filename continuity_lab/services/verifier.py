"""Independent verifier. Conclusions come only from fixture checks and target receipts."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass

from ..config import DEMO_REPO

FIXTURE_FILES = ("demo_app/auth.py", "demo_app/users.json", "demo_app/settings.json", "checks/check_signin.py")
CHECK_SCRIPT = DEMO_REPO / "checks" / "check_signin.py"


def fixture_sources() -> list[dict]:
    out = []
    for rel in FIXTURE_FILES:
        data = (DEMO_REPO / rel).read_bytes()
        out.append({"ref": f"fixtures/demo_repo/{rel}", "sha256": hashlib.sha256(data).hexdigest()})
    return out


def fixture_hash() -> str:
    h = hashlib.sha256()
    for src in fixture_sources():
        h.update(src["sha256"].encode())
    return h.hexdigest()[:16]


def signin_claim(content: dict) -> str:
    s = content.get("signin", {})
    if s.get("mode") == "credentials":
        return f"A visitor can sign in with the demo credentials shown ({s.get('email', '?')})."
    if s.get("mode") == "one_click":
        return "A visitor can enter the demo with one click."
    return "Demo sign-in method is not defined in the draft."


@dataclass
class CheckResult:
    status: str  # verified | failed | not_verified
    claim: str
    source: str
    detail: dict


class Verifier:
    def check_signin(self, content: dict, *, repo_access: bool, via_profile: str) -> CheckResult:
        claim = signin_claim(content)
        if not repo_access:
            return CheckResult(
                "not_verified", claim, f"none — {via_profile} has no repository access in this run",
                {"reason": "repository access unavailable", "via": via_profile},
            )
        signin = content.get("signin", {})
        request = {"mode": signin.get("mode"), "email": signin.get("email", ""), "password": signin.get("password", "")}
        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-I", str(CHECK_SCRIPT)],
            input=json.dumps(request), capture_output=True, text=True, timeout=20, cwd=str(DEMO_REPO),
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            output = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            output = {"ok": False, "reason": "check produced no parseable output", "stderr": proc.stderr[-300:]}
        status = "verified" if output.get("ok") else "failed"
        return CheckResult(
            status, claim, "fixture check: checks/check_signin.py",
            {
                "command": "python -I fixtures/demo_repo/checks/check_signin.py",
                "exit_code": proc.returncode,
                "output": output,
                "duration_ms": duration_ms,
                "fixture_hash": fixture_hash(),
                "via": via_profile,
            },
        )

    def check_receipt(self, receipt: dict | None, *, expected_hash: str, expected_key: str) -> CheckResult:
        claim = "Exactly this approved draft exists as a release in the local target."
        if receipt is None:
            return CheckResult("failed", claim, "target lookup", {"reason": "no release for this operation"})
        ok = receipt["content_hash"] == expected_hash and receipt["idempotency_key"] == expected_key
        return CheckResult(
            "verified" if ok else "failed", claim, f"target receipt {receipt['release_id']}",
            {"receipt": receipt, "expected_hash": expected_hash},
        )

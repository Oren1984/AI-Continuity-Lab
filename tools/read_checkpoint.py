"""Print the manual continuation checklist from a downloaded checkpoint — no app, no database needed.

Usage:  python tools/read_checkpoint.py continuity-xxxxxxxx.checkpoint.json
Standard library only. The file is treated as data and never executed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_BYTES = 256 * 1024


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        print(f"Refusing: {len(raw)} bytes exceeds {MAX_BYTES}.")
        return 1
    cp = json.loads(raw.decode("utf-8"))
    if cp.get("schema") != "ai-continuity-lab/checkpoint":
        print("Not an AI Continuity Lab checkpoint.")
        return 1
    print(f"AI Continuity Lab checkpoint v{cp.get('version')} · run {cp.get('run_id')} · "
          f"workspace {cp.get('workspace_id')} · exported {cp.get('exported_at')}")
    print(f"Goal: {cp.get('task_goal')}")
    draft = cp.get("draft")
    if draft:
        c = draft["content"]
        print(f"Draft revision {draft['revision']}: {c['version']} — {c['title']} (sign-in: {c['signin'].get('mode')})")
    op = cp.get("operation")
    if op:
        print(f"Publication operation {op['operation_id']}: {op['status'].upper()} (key {op['idempotency_key']})")
    mc = cp.get("manual_continuation", {})
    for title, key in (("Saved work", "saved_work"), ("Outstanding checks", "outstanding_checks"),
                       ("Unresolved actions", "unresolved_actions"), ("Next steps", "next_steps")):
        print(f"\n{title}:")
        for item in mc.get(key) or ["—"]:
            print(f"  - {item}")
    print("\nCompleted: " + ", ".join(cp.get("completed_steps", [])) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

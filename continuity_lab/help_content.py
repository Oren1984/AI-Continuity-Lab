"""In-app Help Center: plain-English articles, walkthroughs and a glossary."""
from __future__ import annotations

ARTICLES = [
    {
        "id": "what", "title": "What this lab demonstrates",
        "summary": "One person, one task with AI, and what happens when something breaks along the way.",
        "body": (
            "You prepare a demo release, check that a visitor can sign in, approve publication, and confirm "
            "the result. Along the way the lab can break things on purpose: a slow provider, a lost "
            "connection, a tool that lost repository access, a publication whose result never came back, "
            "or two tools that fail together because they share a dependency. On every run the status card "
            "answers three questions: What was saved? What was verified? What can I do now?"
        ),
    },
    {
        "id": "why", "title": "Why continuity matters",
        "summary": "Different failures need different next steps. Guessing wrong costs time or duplicates work.",
        "body": (
            "A stalled chat means your request may still be fine — retry or switch tools, but keep your input. "
            "A confident answer with no evidence means you still have to check. An action with an unknown "
            "outcome means you must not simply do it again: first find out whether it already happened. "
            "Continuity is keeping your work, your evidence, and the identity of every risky action, so the "
            "next step is clear even when the tool you were using is gone."
        ),
    },
    {
        "id": "happy-path", "title": "Run the happy path",
        "summary": "Start a Normal run, send the task, verify sign-in, approve, publish, check the receipt.",
        "body": (
            "1. On the Journey page choose the scenario 'Normal run' and press Start run. "
            "2. Press Send task. The assistant proposes a draft; the lab saves it as revision 1. "
            "3. Press Verify sign-in. The lab runs the fixture check and shows the evidence next to the claim. "
            "4. Review the draft and press Approve. Approval is bound to this exact revision and target. "
            "5. Press Publish. The lab records an operation ID first, then writes to the local release registry. "
            "6. Press Check publication status. The verifier compares the target receipt with the approved draft. "
            "You should see exactly one verified receipt."
        ),
    },
    {
        "id": "recover", "title": "Recover from a failure",
        "summary": "Read the status card, then use the control it points to: retry, switch, reconcile or resume.",
        "body": (
            "Every failure leaves the last committed checkpoint in PostgreSQL. After a stall, your input is "
            "kept: Retry, switch tools, or download the checkpoint. After an interruption, reload the page or "
            "open the run from another browser: the lab restores the last checkpoint and marks any step that "
            "started but was not confirmed. After a lost publication response, the outcome is UNKNOWN: press "
            "Check publication status. The lab looks up the operation ID at the target. If the release exists "
            "you get the receipt; if not, you can retry with the same operation ID, which cannot create a second release."
        ),
    },
    {
        "id": "switch", "title": "Switch tools",
        "summary": "Profiles are simulated. Each shows availability, capabilities and fictional dependencies.",
        "body": (
            "ChatGPT, Claude, Gemini and GitHub Copilot appear as simulated tool profiles. None of them is "
            "contacted. Each profile lists its capabilities (for example repository access) and fictional "
            "dependencies. When a shared dependency fails, every profile that depends on it fails at once; the "
            "lab names the dependency and offers a route that does not share it. Switching keeps the run, the "
            "draft and the evidence: only the active route changes, and it is recorded in the timeline."
        ),
    },
    {
        "id": "manual", "title": "Continue manually",
        "summary": "The checklist lists saved work, outstanding checks, and any action with an unresolved outcome.",
        "body": (
            "Open 'Continue manually' on the Journey page or download the checkpoint. The checkpoint is a "
            "readable JSON file that includes a manual continuation section. If the app is not reachable, run "
            "`python tools/read_checkpoint.py your-file.json` to print the checklist; it needs only Python. "
            "You can also create the draft from the template and run the sign-in check yourself."
        ),
    },
    {
        "id": "states", "title": "Saved, verified and unknown",
        "summary": "Saved means stored. Verified means checked by evidence. Unknown means nobody can tell yet.",
        "body": (
            "SAVED: the lab committed it to PostgreSQL before showing it. VERIFIED: the independent verifier "
            "produced evidence — the fixture sign-in check passed for this exact draft revision, or the target "
            "receipt matches the approved draft. A model saying something works is never verification. "
            "NOT VERIFIED: no evidence in this run (for example, the tool had no repository access). FAILED: "
            "the check ran and did not pass. UNKNOWN: an action may or may not have happened; reconcile before "
            "acting again. STALE: evidence or approval for an older draft revision; editing the draft makes it stale."
        ),
    },
    {
        "id": "approval", "title": "Which actions need your approval",
        "summary": "Publication always needs your approval, bound to one draft revision and one target.",
        "body": (
            "A model may propose a draft or ask a question. The workflow service decides what happens. "
            "Publication requires your explicit approval of the current, verified draft revision for the "
            "local release registry. Editing the draft invalidates the approval and the earlier verification. "
            "Imported approvals and receipts from a checkpoint are shown for information only. Retrying a "
            "publication with an unknown outcome is blocked until the lab has checked the target."
        ),
    },
    {
        "id": "compare", "title": "Interpret the comparisons",
        "summary": "Model comparison changes only the model. Recovery comparison changes only the recovery features.",
        "body": (
            "The model comparison holds the prompt, scenario, starting state, tool access, recovery features and "
            "rubric constant and changes the model. The recovery comparison holds the model and scenario "
            "constant and compares a baseline (no checkpoint, no transfer, no evidence beside claims) with full "
            "continuity. Approval and idempotent publication stay enforced in both. Fixture results are "
            "simulation results from deterministic models, so repetitions are identical; recovery time combines "
            "measured system time with modeled human time and is labeled as such. Treat results as evidence "
            "about this task, these models and these conditions — not about any real product."
        ),
    },
    {
        "id": "outages", "title": "Outages, capacity and fictional dependencies",
        "summary": "A regional outage stops service at once; a chip supply constraint shows up as reduced capacity.",
        "body": (
            "The simulator models failures at four layers: your device or connection, a service, a tool, and "
            "shared infrastructure. A server or regional outage can interrupt service immediately. A chip "
            "supply constraint is different: it is modeled as reduced capacity over time — the service still "
            "responds, but with a smaller fallback model or fewer capabilities. Provider dependency assignments "
            "in this lab are fictional scenario inputs and say nothing about how real providers are built."
        ),
    },
    {
        "id": "data", "title": "Data, retention and deletion",
        "summary": "Synthetic data only. Configurable retention, scoped export, and deletion of demo records.",
        "body": (
            "All data is synthetic. Raw prompts are not stored in events by default and sensitive fields are "
            "redacted. Runs older than RETENTION_DAYS are deleted at startup; audit events are kept seven days "
            "longer. Embeddings cover Help Center articles and project decisions only — not drafts, prompts or "
            "credentials. Exports omit the demo password. The Operations page can delete every demo record for "
            "a workspace, including its simulated releases, keeping only a content-free audit tombstone. This "
            "is a proof of concept and makes no compliance certification claims."
        ),
    },
]

WALKTHROUGHS = [
    ("Lost response, then resume elsewhere", [
        "Start 'Lost response after approval (target written)'.",
        "Send task → Verify sign-in → Approve → Publish. The result is lost: status UNKNOWN.",
        "Open the app in a second browser window, pick the same run under 'Resume a run'.",
        "Press Check publication status. The lab finds the release by operation ID and records the receipt.",
        "Operations → Simulated target shows exactly one release for this operation.",
    ]),
    ("Shared dependency outage", [
        "Start 'Shared dependency outage'. ChatGPT is the active route.",
        "Send task: ChatGPT is unavailable because of North Shared Gateway (fictional). Copilot is down too.",
        "Switch to Claude or Gemini, which do not share that dependency — or open 'Continue manually'.",
    ]),
    ("Capability loss", [
        "Start 'Capability loss with a responsive chat'. The chat still answers.",
        "Verify sign-in: NOT VERIFIED — the tool has no repository access. Approve stays blocked.",
        "Switch to Claude (or run the manual check), verify again, then approve and publish.",
    ]),
]

GLOSSARY = {
    "Checkpoint": "A committed snapshot of the run: goal, draft, decisions, steps, evidence references and operation status.",
    "Draft revision": "A numbered, saved version of the release draft. Editing creates a new revision.",
    "Evidence": "A record produced by the verifier from a fixture check or a target receipt.",
    "Operation ID": "A stable ID saved before publication is attempted. Retries reuse it.",
    "Idempotency key": "The key the target uses to make sure one operation creates at most one release.",
    "Reconciliation": "Looking up the operation at the target to turn an unknown outcome into a known one.",
    "Receipt": "The target's record of a release: release ID, key, content hash and time.",
    "Tool profile": "A simulated stand-in for a familiar AI tool, with capabilities and fictional dependencies.",
    "Fixture": "Deterministic, synthetic data or behavior that replaces a real service in the demo.",
    "Manual continuation": "Finishing the task without an AI tool, guided by the checklist.",
}

"""Deterministic scenario catalog and fault plans."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FaultPlan:
    overloaded_profiles: frozenset[str] = frozenset()
    overload_failures: int = 1                     # adapter calls that stall before recovering
    down_dependencies: frozenset[str] = frozenset()
    capability_removed: dict = field(default_factory=dict)  # profile -> tuple of capabilities
    fallback_model: dict = field(default_factory=dict)       # profile -> model id
    interrupt_at: str | None = None                # step name
    lost_response: str | None = None               # 'after_write' | 'before_write'


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    sees: str
    expected: str
    layer: str
    default_variant: str
    faults: FaultPlan


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in [
        Scenario(
            "normal", "Normal run",
            "The draft is ready, sign-in verification is visible, and publication awaits approval.",
            "Complete the workflow and show one verified receipt.",
            "none", "explicit", FaultPlan(),
        ),
        Scenario(
            "provider_overload", "Provider overload or slow response",
            "The request is delayed or the session stalls.",
            "Preserve input and the last checkpoint. Retry, switch tools, or download the checkpoint.",
            "service", "explicit", FaultPlan(overloaded_profiles=frozenset({"chatgpt"}), overload_failures=1),
        ),
        Scenario(
            "interruption", "Connection or power interruption",
            "The interface disappears during work.",
            "Restore the last committed checkpoint from PostgreSQL and mark unconfirmed work.",
            "device_connection", "explicit", FaultPlan(interrupt_at="verify_signin"),
        ),
        Scenario(
            "capability_loss", "Capability loss with a responsive chat",
            "The chat answers, but its simulated repository access is gone.",
            "Sign-in shown as not verified in this run. Publication blocked until fresh verification.",
            "tool", "explicit", FaultPlan(capability_removed={"chatgpt": ("repository_access",)}),
        ),
        Scenario(
            "lost_response_after_write", "Lost response after approval (target written)",
            "You approved publication and the result never came back.",
            "Unknown outcome. Reconcile by operation ID; the target receipt is found; no duplicate.",
            "service", "explicit", FaultPlan(lost_response="after_write"),
        ),
        Scenario(
            "lost_response_before_write", "Lost response after approval (target not written)",
            "You approved publication and the result never came back.",
            "Unknown outcome. Reconcile by operation ID; nothing was written; retry with the same operation ID.",
            "service", "explicit", FaultPlan(lost_response="before_write"),
        ),
        Scenario(
            "shared_dependency_outage", "Shared dependency outage",
            "Two apparently separate tool profiles become unavailable at once.",
            "Show the shared dependency. Offer an independent route or a manual checklist.",
            "shared_infrastructure", "explicit",
            FaultPlan(down_dependencies=frozenset({"dep-north-gateway"})),
        ),
        Scenario(
            "reduced_capacity", "Reduced capacity or model fallback",
            "The service still responds, with different capabilities.",
            "Show the active model and capability changes; measure the effect.",
            "service", "inferable", FaultPlan(fallback_model={"chatgpt": "fixture-lite"}),
        ),
        Scenario(
            "ambiguous_requirement", "Ambiguous requirement with healthy services",
            "“Fix demo sign-in” could mean visible credentials or one-click entry.",
            "Use prior decisions when available; otherwise ask one focused question. Approval still required.",
            "none", "ambiguous", FaultPlan(),
        ),
    ]
}

TASK_VARIANTS = {
    "explicit": "Prepare the demo release. Show the demo credentials on the landing page so visitors "
                "can sign in, verify that a visitor can sign in, and get it ready to publish.",
    "inferable": "Fix demo sign-in for the demo release and verify that a visitor can sign in.",
    "ambiguous": "Fix demo sign-in for the demo release and verify that a visitor can sign in.",
}

# What the person actually wants. Used only to score outcomes, never shown to a model.
INTENDED_SIGNIN_MODE = "credentials"


def get(scenario_id: str) -> Scenario:
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario_id!r}")
    return SCENARIOS[scenario_id]

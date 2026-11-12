"""Simulated tool profiles and their fictional dependency mapping.

The profile names are familiar products, but every profile here is a local simulation.
Dependency names and assignments are fictional scenario inputs, not claims about how
any real provider is built or hosted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CAP_CHAT = "chat"
CAP_REPO = "repository_access"
CAP_LONG_CONTEXT = "long_context"


@dataclass(frozen=True)
class ToolProfile:
    id: str
    name: str
    default_model: str
    capabilities: frozenset[str]
    dependencies: tuple[str, ...]
    note: str = "Simulated profile"


@dataclass(frozen=True)
class Dependency:
    id: str
    name: str
    layer: str  # device_connection | service | tool | shared_infrastructure


DEPENDENCIES: dict[str, Dependency] = {
    d.id: d
    for d in [
        Dependency("dep-north-gateway", "North Shared Gateway (fictional)", "shared_infrastructure"),
        Dependency("dep-west-cloud", "West Compute Region (fictional)", "service"),
        Dependency("dep-east-cloud", "East Compute Region (fictional)", "service"),
        Dependency("dep-code-host", "Code Host Bridge (fictional)", "tool"),
        Dependency("dep-local-network", "Your connection", "device_connection"),
    ]
}

PROFILES: dict[str, ToolProfile] = {
    p.id: p
    for p in [
        ToolProfile(
            "chatgpt", "ChatGPT", "fixture-advanced",
            frozenset({CAP_CHAT, CAP_REPO, CAP_LONG_CONTEXT}),
            ("dep-local-network", "dep-north-gateway"),
        ),
        ToolProfile(
            "claude", "Claude", "fixture-advanced",
            frozenset({CAP_CHAT, CAP_REPO, CAP_LONG_CONTEXT}),
            ("dep-local-network", "dep-west-cloud"),
        ),
        ToolProfile(
            "gemini", "Gemini", "fixture-advanced",
            frozenset({CAP_CHAT, CAP_REPO, CAP_LONG_CONTEXT}),
            ("dep-local-network", "dep-east-cloud"),
        ),
        ToolProfile(
            "copilot", "GitHub Copilot", "fixture-advanced",
            frozenset({CAP_CHAT, CAP_REPO}),
            ("dep-local-network", "dep-north-gateway", "dep-code-host"),
        ),
    ]
}


@dataclass
class ProfileState:
    profile: ToolProfile
    available: bool
    capabilities: set[str]
    active_model: str
    capability_changes: list[str] = field(default_factory=list)
    reason: str = ""
    down_dependencies: list[str] = field(default_factory=list)

    @property
    def has_repo_access(self) -> bool:
        return self.available and CAP_REPO in self.capabilities


def profile_state(profile_id: str, requested_model: str, faults) -> ProfileState:
    """Apply a scenario's fault plan to one profile. `faults` is a scenarios.FaultPlan."""
    profile = PROFILES[profile_id]
    caps = set(profile.capabilities)
    changes: list[str] = []
    down = [d for d in profile.dependencies if d in faults.down_dependencies]
    available = not down
    reason = ""
    if down:
        reason = "Unavailable: depends on " + ", ".join(DEPENDENCIES[d].name for d in down)
    model = requested_model
    for cap in faults.capability_removed.get(profile_id, ()):
        if cap in caps:
            caps.discard(cap)
            changes.append(f"lost {cap.replace('_', ' ')}")
    fallback = faults.fallback_model.get(profile_id)
    if fallback and fallback != requested_model:
        changes.append(f"fallback model {fallback} replaces {requested_model}")
        model = fallback
        caps.discard(CAP_LONG_CONTEXT)
        changes.append("reduced context: earlier project decisions are not retrieved")
    return ProfileState(profile, available, caps, model, changes, reason, down)


def independent_routes(profile_id: str, faults) -> list[str]:
    """Other profiles that share no down dependency and are available."""
    out = []
    for pid in PROFILES:
        if pid == profile_id:
            continue
        if profile_state(pid, PROFILES[pid].default_model, faults).available:
            out.append(pid)
    return out

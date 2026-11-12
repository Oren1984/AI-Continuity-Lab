"""Fixture models, simulated profiles and fault plans."""
from continuity_lab.services import scenarios
from continuity_lab.services.adapters import FixtureAdapter, ModelRequest
from continuity_lab.services.providers import independent_routes, profile_state

DEC = [{"key": "DEC-004", "decision": "visible credentials", "source": "x"}]
VAGUE = scenarios.TASK_VARIANTS["inferable"]


def req(text, decisions=(), repo=True, ctx=True):
    return ModelRequest(text, list(decisions), repo, ctx)


def test_explicit_both_models_agree():
    for m in ("fixture-lite", "fixture-advanced"):
        r = FixtureAdapter(m).respond(req(scenarios.TASK_VARIANTS["explicit"]))
        assert r.proposed_signin_mode == "credentials" and r.question is None and r.simulated


def test_inferable_advanced_uses_decision_lite_asks():
    assert FixtureAdapter("fixture-advanced").respond(req(VAGUE, DEC)).proposed_signin_mode == "credentials"
    lite = FixtureAdapter("fixture-lite").respond(req(VAGUE, DEC))
    assert lite.question is not None and lite.proposed_signin_mode is None


def test_ambiguous_advanced_asks_lite_guesses():
    assert FixtureAdapter("fixture-advanced").respond(req(VAGUE)).question is not None
    assert FixtureAdapter("fixture-lite").respond(req(VAGUE)).proposed_signin_mode == "one_click"


def test_unsupported_claim_only_from_lite_without_repo():
    assert FixtureAdapter("fixture-lite").respond(req(scenarios.TASK_VARIANTS["explicit"], repo=False)).claims
    assert not FixtureAdapter("fixture-advanced").respond(req(scenarios.TASK_VARIANTS["explicit"], repo=False)).claims
    assert not FixtureAdapter("fixture-lite").respond(req(scenarios.TASK_VARIANTS["explicit"], repo=True)).claims


def test_shared_dependency_takes_down_two_profiles():
    f = scenarios.get("shared_dependency_outage").faults
    assert not profile_state("chatgpt", "fixture-advanced", f).available
    assert not profile_state("copilot", "fixture-advanced", f).available
    assert set(independent_routes("chatgpt", f)) == {"claude", "gemini"}


def test_capability_loss_keeps_chat():
    s = profile_state("chatgpt", "fixture-advanced", scenarios.get("capability_loss").faults)
    assert s.available and not s.has_repo_access and s.capability_changes


def test_reduced_capacity_falls_back_and_loses_context():
    s = profile_state("chatgpt", "fixture-advanced", scenarios.get("reduced_capacity").faults)
    assert s.active_model == "fixture-lite" and "long_context" not in s.capabilities

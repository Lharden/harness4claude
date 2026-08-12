import sys
from pathlib import Path

# The hooks are standalone scripts rather than an importable package, so
# the suite puts the hooks directory on the path the way a hook runner does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

from harness_lite_adapter import (
    EvidenceBundle,
    build_supervisor_request,
    bundle_is_acceptable,
    checkpoint_points,
    is_replay,
    project_fingerprint_for,
    reaches_the_plane,
    to_wire,
)

"""Conformance suite for the thin harness-lite adapter.

Ported from harness-lite core/src/control/adapter-contracts.test.ts. The
properties are the ones that keep two adapters from producing two request
shapes the plane would then have to accept both of.
"""

FINGERPRINT = "sha256:" + "a" * 64


def _request(**overrides):
    inputs = {
        "level": "L2",
        "stage": "execute",
        "spec_text": "a spec",
        "plan_text": "a plan",
        "project_fingerprint": FINGERPRINT,
        "session_id": "S-1",
        "requested_alias": "local-fast",
    }
    inputs.update(overrides)
    return build_supervisor_request(**inputs)


def test_l0_never_reaches_the_plane():
    # Submitting one would pay for a round trip to be told what the supervisor
    # already knew.
    assert reaches_the_plane("L0") is False
    assert reaches_the_plane("L1") is True
    assert reaches_the_plane("L2") is True


def test_plan_is_hashed_and_an_absent_plan_is_none():
    assert _request().plan_sha256.startswith("sha256:")
    # A zero hash reads as "a plan whose content is empty", which is a
    # different and false claim.
    assert _request(plan_text=None).plan_sha256 is None


def test_the_same_plan_hashes_the_same_and_a_different_one_does_not():
    assert _request().plan_sha256 == _request().plan_sha256
    assert _request(plan_text="outro").plan_sha256 != _request().plan_sha256


def test_neither_spec_nor_plan_text_travels():
    wire = str(to_wire(_request()))
    # The plane gets a name for the plan, not the plan. What a document says
    # must never change what is executed.
    assert "a spec" not in wire
    assert "a plan" not in wire


def test_the_same_inputs_produce_the_same_key():
    assert is_replay(_request(), _request()) is True


def test_a_different_plan_is_different_work():
    assert is_replay(_request(), _request(plan_text="outro")) is False


def test_a_different_project_is_different_work():
    other = _request(project_fingerprint="sha256:" + "b" * 64)
    # Two projects with the same prompt are two tasks.
    assert is_replay(_request(), other) is False


def test_a_different_stage_is_different_work():
    assert is_replay(_request(), _request(stage="verify")) is False


def test_the_requested_alias_does_not_change_the_key():
    # Policy may route it elsewhere and it is still the same task.
    assert is_replay(_request(), _request(requested_alias="cloud-strong")) is True


def test_the_key_is_stable_across_calls():
    # A timestamp in the key would make every replay a new task.
    assert _request().idempotency_key == _request().idempotency_key


def test_premium_calls_are_bracketed_and_local_ones_are_not():
    for stage in ("spec", "execute", "verify"):
        # Before, so a crash mid-call does not lose that the call was about to
        # happen; after, because the result is the expensive thing.
        assert checkpoint_points(stage, True) == ("before", "after")
        assert checkpoint_points(stage, False) == ()


def test_classify_and_plan_do_not_bracket():
    assert checkpoint_points("classify", True) == ()
    assert checkpoint_points("plan", True) == ()


def test_a_passed_bundle_with_artifacts_is_acceptable():
    assert bundle_is_acceptable(EvidenceBundle("T-1", (("patch", "x"),), "passed")) is True


def test_a_passed_bundle_with_no_artifacts_is_not():
    # A pass that produced nothing is a verdict about nothing.
    assert bundle_is_acceptable(EvidenceBundle("T-1", (), "passed")) is False


def test_provisional_is_not_a_pass():
    # A bundle that did not finish is an absent answer, and treating it as a
    # wrong one is how a truncation becomes a verdict.
    assert bundle_is_acceptable(EvidenceBundle("T-1", (("p", "x"),), "provisional")) is False


def test_failed_is_not_a_pass():
    assert bundle_is_acceptable(EvidenceBundle("T-1", (("p", "x"),), "failed")) is False


def test_the_fingerprint_does_not_depend_on_order():
    # Two orders of the same project are one project, and one task.
    assert project_fingerprint_for(["b", "a"]) == project_fingerprint_for(["a", "b"])


def test_a_different_project_fingerprints_differently():
    assert project_fingerprint_for(["a"]) != project_fingerprint_for(["a", "b"])

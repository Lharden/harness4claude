import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location("transactional_state", ROOT / "scripts" / "transactional_state.py")
state = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(state)


def test_revision_evidence_and_scope_invariants(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="session|repo|worktree", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    db.record_artifact(task["task_id"], "spec-light", "spec.md", "abc")
    task = db.task(task["task_id"])
    task = db.transition(task["task_id"], "tdd", expected_revision=task["revision"])
    task = db.transition(task["task_id"], "verify-against-spec", expected_revision=task["revision"])
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, output_hash="out",
    )
    done = db.complete(task["task_id"], expected_revision=task["revision"])

    assert done["status"] == "done"
    with pytest.raises(state.StateTransitionError, match="revision"):
        db.complete(task["task_id"], expected_revision=0)


def test_starting_new_task_cancels_pending_gates_on_abandoned_task(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    first = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    first = db.open_gate(first["task_id"], "escalation")

    db.start_task(
        scope_id="s", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )

    abandoned = db.task(first["task_id"])
    assert abandoned["status"] == "abandoned"
    assert abandoned["pending_gate"] is None


def test_zero_tests_and_stale_owner_do_not_verify(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=0, tests_passed=0, output_hash="out",
    )
    assert task["verified"] is False
    lease = db.acquire_lease("s", "owner-a", ttl_seconds=1, now=10)
    takeover = db.acquire_lease("s", "owner-b", ttl_seconds=1, now=12)
    with pytest.raises(state.StateTransitionError, match="owner epoch"):
        db.transition(
            task["task_id"],
            "missing",
            expected_revision=db.task(task["task_id"])["revision"],
            owner_epoch=lease["owner_epoch"],
        )
    assert takeover["owner_epoch"] == 2


def test_latest_failing_test_revokes_passing_evidence_for_same_revision(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=3, tests_passed=3, output_hash="passing",
    )

    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=1,
        tests_collected=3, tests_passed=2, output_hash="failing",
    )

    assert task["verified"] is False
    assert task["status"] == "active"
    with pytest.raises(state.StateTransitionError, match="fresh verification"):
        db.complete(task["task_id"], expected_revision=task["revision"])


def test_non_test_evidence_does_not_revoke_latest_passing_test(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["verify"], prompt="fix",
    )
    task = db.record_evidence(
        task["task_id"], evidence_type="test", command="pytest", exit_code=0,
        tests_collected=2, tests_passed=2, output_hash="passing",
    )

    task = db.record_evidence(
        task["task_id"], evidence_type="review", command=None, exit_code=None,
        tests_collected=None, tests_passed=None, output_hash="review",
    )

    assert task["verified"] is True


def test_state_cli_creates_database_and_projection(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "state_cli.py"),
            "--home", str(tmp_path), "init", "--scope", "session|repo|worktree",
            "--task", "t-cli", "--classification", "L1-feature", "--prompt", "build",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    projection = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projection["task_id"] == "t-cli"
    assert projection["current_step"] == "write-spec-light"
    assert (tmp_path / "harness.db").exists()


def test_stale_task_ttl_abandons_pipeline_and_releases_scope(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="session|repo|worktree", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    started = datetime.fromisoformat(task["started_at"]).timestamp()

    assert db.expire_stale_task("session|repo|worktree", ttl_seconds=3600, now=started + 3599) is None
    expired = db.expire_stale_task("session|repo|worktree", ttl_seconds=3600, now=started + 3601)

    assert expired is not None
    assert expired["status"] == "abandoned"
    assert db.current_task("session|repo|worktree") is None


def test_stale_task_ttl_cancels_a_pending_human_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    task = db.open_gate(task["task_id"], "escalation")
    started = datetime.fromisoformat(task["started_at"]).timestamp()

    expired = db.expire_stale_task("s", ttl_seconds=1, now=started + 2)

    assert expired is not None
    assert expired["status"] == "abandoned"
    assert expired["pending_gate"] is None


def test_ttl_compare_and_set_does_not_expire_a_replacement_task(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    old = db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=["systematic-debugging", "tdd", "verify"], prompt="fix",
    )
    replacement = db.start_task(
        scope_id="s", legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"], prompt="build",
    )
    started = datetime.fromisoformat(replacement["started_at"]).timestamp()

    expired = db.expire_stale_task(
        "s", ttl_seconds=1, now=started + 2, expected_task_id=old["task_id"]
    )

    assert expired is None
    assert db.current_task("s")["task_id"] == replacement["task_id"]


def test_state_cli_touch_invalidates_fresh_verification(tmp_path: Path):
    cli = str(ROOT / "scripts" / "state_cli.py")
    base = [sys.executable, cli, "--home", str(tmp_path)]
    init = subprocess.run(
        [*base, "init", "--scope", "s", "--task", "t-touch", "--classification", "L1-bug"],
        capture_output=True, text=True, check=False,
    )
    assert init.returncode == 0, init.stderr
    evidence = subprocess.run(
        [*base,
            "evidence", "--task", "t-touch", "--type", "test", "--command-text", "pytest",
            "--exit-code", "0", "--tests-collected", "2", "--tests-passed", "2",
        ],
        capture_output=True, text=True, check=False,
    )
    assert evidence.returncode == 0, evidence.stderr

    touched = subprocess.run(
        [*base, "touch", "--task", "t-touch", "--path", "src/app.py"],
        capture_output=True, text=True, check=False,
    )

    assert touched.returncode == 0, touched.stderr
    projection = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projection["verified"] is False
    assert projection["status"] == "active"
    assert projection["code_revision"] == 1

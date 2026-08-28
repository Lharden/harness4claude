import importlib.util
import os
import json
import subprocess
import sys
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
        db.transition(task["task_id"], "missing", expected_revision=db.task(task["task_id"])["revision"], owner_epoch=lease["owner_epoch"])
    assert takeover["owner_epoch"] == 2


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

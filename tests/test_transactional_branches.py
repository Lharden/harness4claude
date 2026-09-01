import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location(
    "transactional_branch_state", ROOT / "scripts" / "transactional_state.py"
)
state = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(state)


def _task(database):
    return database.start_task(
        scope_id="session|repo|worktree",
        legacy_level="L2-feature",
        tier="L2",
        kind="feature",
        pipeline=["discuss", "tdd"],
        prompt="branch work",
    )


def _create(database, task_id: str, branch_id: str, turn: int, **limits):
    return database.create_branch(
        task_id,
        branch_id=branch_id,
        slug=branch_id,
        name=branch_id.title(),
        topic=f"topic {branch_id}",
        topic_hash=f"hash-{branch_id}",
        offered_turn=turn,
        max_offers=limits.get("max_offers", 3),
        cooldown_turns=limits.get("cooldown_turns", 0),
    )


def test_each_branch_approval_resolves_only_its_subject_gate(tmp_path: Path):
    database = state.HarnessDatabase(tmp_path)
    task = _task(database)
    first = _create(database, task["task_id"], "b-first", 10)
    second = _create(database, task["task_id"], "b-second", 11)

    database.approve_branch(first["branch_id"])

    with sqlite3.connect(tmp_path / "harness.db") as connection:
        rows = connection.execute(
            "SELECT subject_id, status FROM gates WHERE task_id = ? AND gate_type = 'branch-open'",
            (task["task_id"],),
        ).fetchall()
    assert dict(rows) == {first["branch_id"]: "resolved", second["branch_id"]: "pending"}
    current = database.task(task["task_id"])
    assert current["status"] == "awaiting_gate"
    assert current["pending_gate"] == f"branch-open:{second['branch_id']}"

    database.approve_branch(second["branch_id"])
    assert database.task(task["task_id"])["status"] == "active"


def test_offer_cooldown_and_limits_are_database_invariants(tmp_path: Path):
    database = state.HarnessDatabase(tmp_path)
    task = _task(database)
    _create(database, task["task_id"], "b-one", 10, max_offers=2, cooldown_turns=8)

    with pytest.raises(state.StateTransitionError, match="cooldown"):
        _create(database, task["task_id"], "b-two", 12, max_offers=2, cooldown_turns=8)
    _create(database, task["task_id"], "b-two", 18, max_offers=2, cooldown_turns=8)
    with pytest.raises(state.StateTransitionError, match="offer limit"):
        _create(database, task["task_id"], "b-three", 30, max_offers=2, cooldown_turns=0)


def test_open_branch_requires_its_approval_and_enforces_open_limit(tmp_path: Path):
    database = state.HarnessDatabase(tmp_path)
    task = _task(database)
    first = _create(database, task["task_id"], "b-one", 10)
    second = _create(database, task["task_id"], "b-two", 11)

    with pytest.raises(state.StateTransitionError, match="approval"):
        database.open_branch(first["branch_id"], seed_path="one.md", max_open=1)
    database.approve_branch(first["branch_id"])
    opened = database.open_branch(first["branch_id"], seed_path="one.md", max_open=1)
    assert opened["status"] == "open"
    assert opened["seed_path"] == "one.md"

    database.approve_branch(second["branch_id"])
    with pytest.raises(state.StateTransitionError, match="open branch limit"):
        database.open_branch(second["branch_id"], seed_path="two.md", max_open=1)


def test_legacy_gate_table_is_migrated_with_subject_identity(tmp_path: Path):
    database_path = tmp_path / "harness.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE gates (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
            "gate_type TEXT NOT NULL, status TEXT NOT NULL, decision TEXT, created_at TEXT NOT NULL, "
            "resolved_at TEXT)"
        )

    state.HarnessDatabase(tmp_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(gates)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(gates)")}
    assert "subject_id" in columns
    assert "one_pending_gate_per_subject" in indexes

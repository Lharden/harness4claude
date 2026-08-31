from __future__ import annotations

import importlib.util
import os
from pathlib import Path


ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "continuation_policy",
    ROOT / "scripts" / "continuation_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


def test_active_and_awaiting_gate_pipelines_continue():
    assert policy.should_continue({"status": "active", "pipeline": ["tdd"]}) is True
    assert policy.should_continue({"status": "awaiting_gate", "pipeline": ["approve-plan"]}) is True


def test_terminal_or_empty_pipeline_does_not_continue():
    assert policy.should_continue({"status": "done", "pipeline": ["verify"]}) is False
    assert policy.should_continue({"status": "active", "pipeline": []}) is False


def test_session_start_uses_the_same_pending_gate_continuation_policy():
    script = (ROOT / "hooks" / "harness-session-start.sh").read_text(encoding="utf-8")

    assert "from continuation_policy import should_continue" in script
    assert "if should_continue(state):" in script
    assert "Pending human gate" in script

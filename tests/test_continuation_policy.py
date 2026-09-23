from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "continuation_policy",
    ROOT / "scripts" / "continuation_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
# `@dataclass` resolve o modulo por `sys.modules`; sem registrar, a coleta
# dependia de outro arquivo ter importado o modulo antes.
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)


def test_active_and_awaiting_gate_pipelines_continue():
    assert policy.continua({"status": "active", "pipeline": ["tdd"]}) is True
    assert policy.continua({"status": "awaiting_gate", "pipeline": ["approve-plan"]}) is True


def test_terminal_or_empty_pipeline_does_not_continue():
    assert policy.continua({"status": "done", "pipeline": ["verify"]}) is False
    assert policy.continua({"status": "superseded", "pipeline": ["verify"]}) is False
    assert policy.continua({"status": "active", "pipeline": []}) is False


def test_the_continuation_set_is_the_database_live_set():
    """R1 (HC-00h): dois conjuntos de "viva" que discordavam mataram uma task L2."""
    from transactional_state import ACTIVE_STATUSES

    for status in ACTIVE_STATUSES:
        assert policy.continua({"status": status, "pipeline": ["tdd"]}) is True


def test_session_start_and_classify_ask_the_same_question():
    for hook in ("harness-session-start.sh", "harness-classify.sh"):
        script = (ROOT / "hooks" / hook).read_text(encoding="utf-8")
        assert "from continuation_policy import" in script, hook
        assert "task_viva(" in script, hook
        assert "should_continue" not in script, hook

    assert "Pending human gate" in (ROOT / "hooks" / "harness-session-start.sh").read_text(encoding="utf-8")

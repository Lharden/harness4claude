from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
SPEC = importlib.util.spec_from_file_location(
    "reclassification_policy",
    ROOT / "scripts" / "reclassification_policy.py",
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


def _state(*, source: str, agreed: bool | None, status: str = "done") -> dict:
    return {
        "classification": "L0-question",
        "status": status,
        "classification_meta": {"source": source, "agreed": agreed},
    }


def test_regex_l0_can_promote_after_three_files_even_when_regex_agreed():
    assert policy.should_promote(_state(source="regex", agreed=True), 3) is True


def test_semantic_or_human_confirmation_locks_classification():
    assert policy.should_promote(_state(source="semantic", agreed=True), 3) is False
    assert policy.should_promote(_state(source="human_override", agreed=True), 3) is False


def test_active_pipeline_or_insufficient_file_count_does_not_promote():
    assert policy.should_promote(_state(source="regex", agreed=True, status="active"), 3) is False
    assert policy.should_promote(_state(source="regex", agreed=True), 2) is False

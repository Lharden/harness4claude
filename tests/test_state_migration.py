"""Testes para scripts/migrate_state.py (Harness v3 — Bloco B).

Garante:
- migrate_state adiciona schema_version 3 e preserva campos base
- migrate_signals leva v2 -> v3 com bloco classify e sdd_usage
- recompute_aggregates exclui tasks abandonadas e calcula accuracy correta
- idempotencia (migrar 2x = mesmo resultado)
- state/signals migrados validam contra os JSON Schemas
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
MIGRATE_PATH = ROOT / "scripts" / "migrate_state.py"
SCHEMAS_DIR = ROOT / "schemas"


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("migrate_state", MIGRATE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_state"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_migrate_state_adds_schema_version(mig):
    out = mig.migrate_state({"task_id": None, "classification": None, "status": "idle",
                             "pipeline": [], "current_step": None,
                             "artifacts_so_far": [], "started_at": None})
    assert out["schema_version"] == 3


def test_migrate_state_fills_missing_base_fields(mig):
    out = mig.migrate_state({})
    for field in ("task_id", "classification", "status", "pipeline",
                  "current_step", "artifacts_so_far", "started_at"):
        assert field in out
    assert out["status"] == "idle"


def test_migrate_signals_v2_to_v3(mig):
    v2 = {"version": 2, "tasks": [], "aggregates": {"total_tasks": 0}}
    out = mig.migrate_signals(v2)
    assert out["version"] == 3
    assert out["harness_version"] == "v3"
    assert "classify" in out["aggregates"]
    assert "sdd_usage" in out["aggregates"]


def test_recompute_excludes_abandoned(mig):
    tasks = [
        {"actual_level": "L1", "pipeline_completed": True, "files_modified": 2},
        {"actual_level": "unknown", "pipeline_completed": False, "files_modified": 0,
         "abandoned_at": "2026-01-01T00:00:00+00:00"},
    ]
    agg = mig.recompute_aggregates(tasks)
    assert agg["total_tasks"] == 1  # abandonada excluida
    assert agg["l1_count"] == 1


def test_recompute_accuracy(mig):
    tasks = [
        {"actual_level": "L1", "pipeline_completed": True, "files_modified": 2,
         "classification_meta": {"agreed": True, "source": "regex"}},
        {"actual_level": "L1", "pipeline_completed": True, "files_modified": 2,
         "classification_meta": {"agreed": False, "source": "semantic"}},
        {"actual_level": "L1", "pipeline_completed": True, "files_modified": 2,
         "classification_meta": {"agreed": None, "source": "regex"}},  # nao conta
    ]
    c = mig.recompute_aggregates(tasks)["classify"]
    assert c["total_classified"] == 2  # so os com agreed != None
    assert c["avg_classify_accuracy"] == 0.5  # 1 de 2 agreed


def test_recompute_human_override(mig):
    tasks = [
        {"actual_level": "L2", "pipeline_completed": True, "files_modified": 5,
         "classification_meta": {"agreed": False, "source": "human_override"}},
    ]
    assert mig.recompute_aggregates(tasks)["classify"]["human_override_count"] == 1


def test_migrate_idempotent(mig):
    v2 = {"version": 2, "tasks": [
        {"actual_level": "L1", "pipeline_completed": True, "files_modified": 3},
    ], "aggregates": {}}
    once = mig.migrate_signals(v2)
    twice = mig.migrate_signals(once)
    assert once == twice


def test_migrated_validates_against_schema(mig):
    jsonschema = pytest.importorskip("jsonschema")
    state = mig.migrate_state({})
    signals = mig.migrate_signals({"version": 2, "tasks": [], "aggregates": {}})
    for data, name in [(state, "state.schema.json"), (signals, "signals.schema.json")]:
        schema = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(data))
        assert not errors, f"{name}: {[e.message for e in errors]}"

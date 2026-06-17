"""Testes para scripts/record_signal.py (Harness v3 — Bloco C).

Garante:
- actual_level deriva corretamente do nº de arquivos
- build_task monta o registro (completed/abandoned) com classification_meta
- record é idempotente por task_id (atualiza, nao duplica)
- record recalcula o bloco classify (avg_classify_accuracy)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
RECORD_PATH = ROOT / "scripts" / "record_signal.py"


@pytest.fixture(scope="module")
def rec():
    # garante que scripts/ esteja importavel (record_signal importa migrate_state)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("record_signal", RECORD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["record_signal"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_actual_level(rec):
    assert rec.actual_level(1) == "L0"
    assert rec.actual_level(2) == "L1"
    assert rec.actual_level(3) == "L1"
    assert rec.actual_level(4) == "L2"


def test_build_task_completed(rec):
    state = {"task_id": "t-x", "classification": "L1-feature",
             "classification_meta": {"suggested": "L2-feature", "final": "L1-feature",
                                     "source": "semantic", "agreed": False}}
    counter = {"count": 2}
    task = rec.build_task(state, counter, completed=True, steps=["tdd"],
                          reason=None, timestamp="2026-06-02T00:00:00+00:00")
    assert task["task_id"] == "t-x"
    assert task["actual_level"] == "L1"
    assert task["pipeline_completed"] is True
    assert task["completed_at"] == "2026-06-02T00:00:00+00:00"
    assert task["classification_meta"]["agreed"] is False


def test_build_task_abandoned(rec):
    task = rec.build_task({"task_id": "t-y"}, {"count": 0}, completed=False,
                          steps=[], reason="switch", timestamp="2026-06-02T00:00:00+00:00")
    assert task["pipeline_completed"] is False
    assert task["abandoned_at"] == "2026-06-02T00:00:00+00:00"
    assert task["reason"] == "switch"


def test_record_idempotent(rec, tmp_path):
    signals = {"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}
    (tmp_path / "signals.json").write_text(json.dumps(signals), encoding="utf-8")
    task = {"task_id": "t-dup", "classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True}}
    rec.record(tmp_path, task)
    rec.record(tmp_path, dict(task))  # mesma task_id de novo
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert len(out["tasks"]) == 1  # nao duplicou


def test_record_distinct_tasks_coexist(rec, tmp_path):
    """P1.2: task_ids distintos coexistem (microssegundo evita colisao/sobrescrita)."""
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}),
        encoding="utf-8")
    base = {"classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True}}
    rec.record(tmp_path, {**base, "task_id": "t-20260616-2147381234"})
    rec.record(tmp_path, {**base, "task_id": "t-20260616-2147389876"})
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert {t["task_id"] for t in out["tasks"]} == {
        "t-20260616-2147381234", "t-20260616-2147389876"}  # ambas preservadas


def test_record_atomic_leaves_no_tmp(rec, tmp_path):
    """P1.1: escrita atomica nao deixa arquivo .tmp e produz JSON valido."""
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "harness_version": "v3", "tasks": [], "aggregates": {}}),
        encoding="utf-8")
    rec.record(tmp_path, {"task_id": "t-atomic", "classification": "L1-feature",
                          "actual_level": "L1", "pipeline_completed": True,
                          "steps_executed": [], "files_modified": 1,
                          "classification_meta": {"agreed": True}})
    assert list(tmp_path.glob("signals.json.tmp-*")) == []  # nenhum temporario
    json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))  # parseavel


def test_record_updates_accuracy(rec, tmp_path):
    (tmp_path / "signals.json").write_text(
        json.dumps({"version": 3, "tasks": [], "aggregates": {}}), encoding="utf-8")
    task = {"task_id": "t-acc", "classification": "L1-feature", "actual_level": "L1",
            "pipeline_completed": True, "steps_executed": [], "files_modified": 2,
            "classification_meta": {"agreed": True, "source": "regex"}}
    out = rec.record(tmp_path, task)
    assert out["aggregates"]["classify"]["avg_classify_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# --expect-task: proteção contra state.json sobrescrito por sessão paralela.
# Incidente 2026-06-12: o workflow fechou a task t-20260612-033900, mas o
# state global já tinha sido trocado pela task fantasma t-20260612-034438
# (criada pela sessão headless do remember) — e o signal foi gravado errado.
# ---------------------------------------------------------------------------

def _setup_harness_dir(tmp_path, task_id: str) -> None:
    (tmp_path / "state.json").write_text(json.dumps({
        "task_id": task_id,
        "classification": "L1-feature",
        "classification_meta": {"suggested": "L1-feature", "final": "L1-feature",
                                "source": "semantic", "agreed": True},
    }), encoding="utf-8")
    (tmp_path / ".session-files-count").write_text(
        json.dumps({"count": 2, "files": ["a.py", "b.py"], "task_id": task_id}),
        encoding="utf-8")


def test_main_expect_task_match_records(rec, tmp_path, monkeypatch):
    _setup_harness_dir(tmp_path, "t-real")
    monkeypatch.setattr(sys, "argv", [
        "record_signal.py", "--harness-dir", str(tmp_path),
        "--completed", "--steps", "tdd", "--expect-task", "t-real",
    ])
    assert rec.main() == 0
    out = json.loads((tmp_path / "signals.json").read_text(encoding="utf-8"))
    assert [t["task_id"] for t in out["tasks"]] == ["t-real"]


def test_main_expect_task_mismatch_aborts(rec, tmp_path, monkeypatch):
    """State com task diferente da esperada → não grava nada, exit 2."""
    _setup_harness_dir(tmp_path, "t-fantasma")
    monkeypatch.setattr(sys, "argv", [
        "record_signal.py", "--harness-dir", str(tmp_path),
        "--completed", "--expect-task", "t-real",
    ])
    assert rec.main() == 2
    assert not (tmp_path / "signals.json").exists(), \
        "mismatch de task_id não pode gravar signal (task fantasma)"

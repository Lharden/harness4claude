"""Portao `escalation` do Stop, aprovado pelo humano, tem de fechar.

Incidente 2026-09-29, task `t-20260929-130710180778` (plugin 4.0.0): o Stop
cobrou evidencia duas vezes, abriu `escalation`, o usuario aprovou, e
`state_cli.py gate --type escalation --decision approve` devolveu exit 2 com
`gate is not at an advanceable phase: escalation`. A task ficou em
`awaiting_gate` sem saida.

Causa: `resolve_gate` tratava todo portao como fase do pipeline e so avancava
quando `pipeline[phase_index] == gate_type`. `escalation` nao e fase de pipeline
nenhum — nasce de `register_stop_continuation`, em qualquer fase. Diagnostico em
`docs/specs/portao-escalation-inaprovavel-diagnostico.md`.

Metade 1 (o defeito): os tres primeiros testes reprovam no codigo anterior.
Metade 2 (o que nao pode mudar): `approve-spec` segue avancando a fase, e
`branch-open` segue fora do `resolve_gate`.
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location("transactional_state", ROOT / "scripts" / "transactional_state.py")
state = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(state)

PIPELINE_BUG = ["systematic-debugging", "tdd", "verify"]


def _task_bug(db):
    return db.start_task(
        scope_id="s", legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=PIPELINE_BUG, prompt="fix",
    )


def _escalar_pelo_stop(db, task_id):
    """O mesmo caminho do hook de Stop: duas continuacoes e o portao."""
    for _ in range(3):
        task = db.register_stop_continuation(task_id, limit=2)
    assert task["status"] == "awaiting_gate"
    assert task["pending_gate"] == "escalation"
    assert task["stop_continuations"] == 2
    return task


def _linhas(db, sql, *params):
    with sqlite3.connect(db.path) as raw:
        return raw.execute(sql, params).fetchall()


def test_aprovar_escalation_do_stop_limpa_o_portao_sem_avancar_fase(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    transicoes_antes = _linhas(db, "SELECT COUNT(*) FROM transitions WHERE task_id = ?", task["task_id"])

    aprovada = db.resolve_gate(
        task["task_id"], "escalation", "approve", expected_revision=task["revision"],
    )

    assert aprovada["status"] == "active"
    assert aprovada["pending_gate"] is None
    assert aprovada["stop_continuations"] == 0
    assert aprovada["phase"] == "systematic-debugging"
    assert aprovada["revision"] == task["revision"] + 1
    assert _linhas(
        db, "SELECT status, decision FROM gates WHERE task_id = ? AND gate_type = 'escalation'",
        task["task_id"],
    ) == [("resolved", "approve")]
    # `escalation` nao e fase: aprova-lo nao e uma transicao do pipeline.
    assert _linhas(
        db, "SELECT COUNT(*) FROM transitions WHERE task_id = ?", task["task_id"],
    ) == transicoes_antes


def test_cli_gate_aprova_escalation_como_o_usuario_rodou(tmp_path: Path):
    """A superficie do incidente: `state_cli.py gate` em subprocesso."""
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])

    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "state_cli.py"),
            "--home", str(tmp_path), "gate", "--task", task["task_id"],
            "--type", "escalation", "--decision", "approve",
            "--expect-revision", str(task["revision"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    projection = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert projection["task_id"] == task["task_id"]
    assert projection["status"] == "active"
    assert projection["pending_gate"] is None
    assert projection["current_step"] == "systematic-debugging"


def test_escalation_aprovado_com_outro_portao_pendente_segue_aguardando(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    db.create_branch(
        task["task_id"], branch_id="b-1", slug="ramo", name="Ramo", topic="outro assunto",
        topic_hash="h1", offered_turn=1, explicito=True,
    )
    task = db.task(task["task_id"])

    aprovada = db.resolve_gate(
        task["task_id"], "escalation", "approve", expected_revision=task["revision"],
    )

    assert aprovada["status"] == "awaiting_gate"
    assert aprovada["pending_gate"] == "branch-open:b-1"
    assert aprovada["stop_continuations"] == 0


def test_depois_da_aprovacao_o_stop_ganha_de_novo_duas_continuacoes(tmp_path: Path):
    """Aprovar e "continue": o contador volta a zero, nao fica no limite."""
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    db.resolve_gate(task["task_id"], "escalation", "approve", expected_revision=task["revision"])

    primeira = db.register_stop_continuation(task["task_id"], limit=2)
    segunda = db.register_stop_continuation(task["task_id"], limit=2)
    terceira = db.register_stop_continuation(task["task_id"], limit=2)

    assert (primeira["status"], primeira["stop_continuations"]) == ("active", 1)
    assert (segunda["status"], segunda["stop_continuations"]) == ("active", 2)
    assert (terceira["status"], terceira["pending_gate"]) == ("awaiting_gate", "escalation")


def test_approve_spec_continua_avancando_a_fase(tmp_path: Path):
    """Metade 2: portao que E fase segue o caminho de antes."""
    db = state.HarnessDatabase(tmp_path)
    task = db.start_task(
        scope_id="s", legacy_level="L2-feature", tier="L2", kind="feature",
        pipeline=["write-spec-light", "approve-spec", "tdd"], prompt="build",
    )
    db.record_artifact(task["task_id"], "spec-light", "spec.md", "abc")
    task = db.task(task["task_id"])
    task = db.transition(task["task_id"], "approve-spec", expected_revision=task["revision"])
    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "approve-spec")

    aprovada = db.resolve_gate(
        task["task_id"], "approve-spec", "approve", expected_revision=task["revision"],
    )

    assert aprovada["phase"] == "tdd"
    assert aprovada["status"] == "active"
    assert aprovada["pending_gate"] is None
    assert _linhas(
        db, "SELECT from_phase, to_phase FROM transitions WHERE task_id = ? ORDER BY rowid DESC LIMIT 1",
        task["task_id"],
    ) == [("approve-spec", "tdd")]


def test_branch_open_segue_fora_do_resolve_gate(tmp_path: Path):
    """Aprovar ramo por aqui pularia `branches.approved_at`; o dono e `resolve_branch_decision`."""
    db = state.HarnessDatabase(tmp_path)
    task = _task_bug(db)
    db.create_branch(
        task["task_id"], branch_id="b-1", slug="ramo", name="Ramo", topic="outro assunto",
        topic_hash="h1", offered_turn=1, explicito=True,
    )
    task = db.task(task["task_id"])

    with pytest.raises(state.StateTransitionError):
        db.resolve_gate(task["task_id"], "branch-open", "approve", expected_revision=task["revision"])

    assert db.branch("b-1")["approved_at"] is None

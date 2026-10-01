"""O registro em signals.json diz o desfecho que o banco gravou.

`signals.json` so tinha dois desfechos: `pipeline_completed` true (com
`completed_at`) ou false (com `abandoned_at`). Uma task `superseded` — verificada
e substituida pelo prompt seguinte antes do `complete` — e registrada como
concluida de proposito (`fix/sinal-da-task-substituida`, S1): o trabalho foi
verificado. Mas a linha ficava igual a de uma task fechada por `complete`, e a
diferenca so existia no `harness.db` do balde. Incidente de origem:
t-20260928-203110092956, devolvida a `superseded` no banco em 2026-09-30 e ainda
"concluida" em signals.json sem como dizer o contrario.

Agora o registro montado do banco leva `desfecho` (o status da task no
`harness.db` quando o sinal foi gravado), e os agregados contam
`superseded_count` a parte. `pipeline_completed` nao muda de sentido.

Metade 1 reprova no codigo anterior (campo e contagem ausentes). Metade 2:
registro sem banco nao inventa desfecho, e linha antiga sem o campo nao conta.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_state import recompute_aggregates  # noqa: E402
from transactional_state import HarnessDatabase  # noqa: E402

ENV_UTF8 = {**os.environ, "PYTHONUTF8": "1"}
BUG = ["systematic-debugging", "tdd", "verify"]


def _run(balde: Path, *argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "record_signal.py"), *argumentos,
         "--harness-dir", str(balde), "--signals-dir", str(balde / "sinais")],
        capture_output=True, text=True, timeout=60, encoding="utf-8", env=ENV_UTF8,
    )


def _sinais(balde: Path) -> dict:
    return json.loads((balde / "sinais" / "signals.json").read_text(encoding="utf-8"))


def _bug(db: HarnessDatabase, balde: Path, task_id: str) -> dict:
    task = db.start_task(scope_id=str(balde), legacy_level="L1-bug", tier="L1", kind="bug",
                         pipeline=BUG, prompt=task_id, task_id=task_id)
    for fase in BUG[1:]:
        task = db.transition(task_id, fase, expected_revision=task["revision"])
    return task


def _verificar(db: HarnessDatabase, task_id: str) -> dict:
    return db.record_evidence(task_id, evidence_type="test", command="python -m pytest -q",
                              exit_code=0, tests_collected=3, tests_passed=3, output_hash="h")


def _projecao_da(balde: Path, task: dict) -> None:
    (balde / "sinais").mkdir(exist_ok=True)
    (balde / "state.json").write_text(json.dumps({
        "task_id": task["task_id"], "classification": task["legacy_level"],
        "status": task["status"], "pipeline": task["pipeline"],
    }), encoding="utf-8")


# --- Metade 1 ----------------------------------------------------------------


def test_substituida_registra_desfecho_superseded(tmp_path: Path):
    db = HarnessDatabase(tmp_path)
    a = _verificar(db, _bug(db, tmp_path, "t-1")["task_id"])
    b = _bug(db, tmp_path, "t-2")
    assert db.task("t-1")["status"] == "superseded"
    _projecao_da(tmp_path, db.task(b["task_id"]))

    res = _run(tmp_path, "--completed", "--expect-task", a["task_id"])

    assert res.returncode == 0, res.stdout + res.stderr
    sinais = _sinais(tmp_path)
    [linha] = sinais["tasks"]
    assert linha["desfecho"] == "superseded"
    assert linha["pipeline_completed"] is True, "verificada: segue contando como concluida (S1)"
    assert sinais["aggregates"]["superseded_count"] == 1


def test_concluida_por_complete_registra_desfecho_done(tmp_path: Path):
    db = HarnessDatabase(tmp_path)
    task = _verificar(db, _bug(db, tmp_path, "t-1")["task_id"])
    task = db.complete("t-1", expected_revision=task["revision"])
    _projecao_da(tmp_path, task)

    res = _run(tmp_path, "--completed", "--expect-task", "t-1")

    assert res.returncode == 0, res.stdout + res.stderr
    sinais = _sinais(tmp_path)
    assert sinais["tasks"][0]["desfecho"] == "done"
    assert sinais["aggregates"]["superseded_count"] == 0


def test_abandono_registra_o_desfecho_que_ele_mesmo_gravou(tmp_path: Path):
    """`--abandoned` encerra a task no banco; o registro diz `abandoned`, nao o
    `active` de antes da escrita."""
    db = HarnessDatabase(tmp_path)
    task = _bug(db, tmp_path, "t-1")
    _projecao_da(tmp_path, task)

    res = _run(tmp_path, "--abandoned", "--reason", "troca", "--expect-task", "t-1")

    assert res.returncode == 0, res.stdout + res.stderr
    assert _sinais(tmp_path)["tasks"][0]["desfecho"] == "abandoned"


# --- Metade 2 ----------------------------------------------------------------


def test_linha_sem_desfecho_nao_conta_como_substituida():
    """Linhas gravadas antes deste campo nao dizem nada; nao viram zero nem um."""
    antigas = [
        {"task_id": "t-1", "classification": "L1-bug", "actual_level": "L1",
         "pipeline_completed": True, "completed_at": "x"},
        {"task_id": "t-2", "classification": "L1-bug", "actual_level": "L1",
         "pipeline_completed": True, "completed_at": "x", "desfecho": "superseded"},
    ]

    agregados = recompute_aggregates(antigas)

    assert agregados["superseded_count"] == 1
    assert agregados["pipeline_completion_rate"] == 1.0, "contagem de concluidas nao muda"


def test_registro_sem_banco_nao_inventa_desfecho(tmp_path: Path):
    """Sem harness.db no balde o registro vem da projecao, como antes, e a
    projecao nao e autoridade de desfecho."""
    _projecao_da(tmp_path, {"task_id": "t-1", "legacy_level": "L1-bug", "status": "done",
                            "pipeline": BUG})

    res = _run(tmp_path, "--completed", "--expect-task", "t-1")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "desfecho" not in _sinais(tmp_path)["tasks"][0]

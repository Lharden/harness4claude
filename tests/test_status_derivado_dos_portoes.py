"""`tasks.status` derivado de `gates` num lugar so.

`status` guarda dois fatos: ciclo de vida (viva ou terminal) e, na task viva,
se ha portao pendente (`awaiting_gate`) ou nao (`active`). Dezessete caminhos
de `HarnessDatabase` escreviam a coluna, cada um pela propria visao parcial:
quem nao lia `gates` deixava `active` com portao pendente ou fechava a task por
cima dele; quem lia `gates` mas nao o ciclo de vida ressuscitava task terminal
e estourava `one_active_task_per_scope`.

Diagnostico e decisoes (D1-D3, migracao) em
`docs/specs/status-derivado-dos-portoes-diagnostico.md`.

Metade 1 (o defeito): cada caso abaixo reprova no codigo de `18ec682`.
Metade 2 (o que nao pode mudar): a promocao L0 -> L1 continua, o ramo numa
conversa L0 continua, e toda recusa imprime uma linha que roda e destrava.
"""

import importlib.util
import json
import os
import shlex
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

CLI = ROOT / "scripts" / "state_cli.py"
CONFIRM = ROOT / "scripts" / "confirm_classification.py"
BUG = ["systematic-debugging", "tdd", "verify"]
FEATURE = ["write-spec-light", "tdd", "verify-against-spec"]
SPEC = ["write-spec-light", "approve-spec", "tdd"]
VIVOS = ("suggested", "active", "awaiting_gate", "verified")


def _task(db, pipeline=BUG, *, task_id="t-1", tier="L1", kind="bug"):
    return db.start_task(
        scope_id="s", legacy_level=f"{tier}-{kind}", tier=tier, kind=kind,
        pipeline=pipeline, prompt=task_id, task_id=task_id,
    )


def _na_fase_portao(db):
    task = _task(db, SPEC, kind="feature")
    db.record_artifact(task["task_id"], "spec-light", "spec.md", "abc")
    return db.transition(task["task_id"], "approve-spec", expected_revision=db.task(task["task_id"])["revision"])


def _ramo(db, task_id="t-1", branch_id="b-1"):
    db.create_branch(
        task_id, branch_id=branch_id, slug=f"ramo-{branch_id}", name="Ramo", topic=f"assunto {branch_id}",
        topic_hash=branch_id, offered_turn=1, explicito=True,
    )
    return db.task(task_id)


def _evidencia(db, task_id="t-1"):
    return db.record_evidence(
        task_id, evidence_type="test", command="pytest", exit_code=0,
        tests_collected=1, tests_passed=1, output_hash="x",
    )


def _portoes(db, task_id="t-1"):
    with sqlite3.connect(db.path) as raw:
        return raw.execute(
            "SELECT gate_type, subject_id, status, decision FROM gates WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()


def _invariante(db):
    """Task viva: `awaiting_gate` sse ha portao pendente. Task terminal com
    pipeline: nenhum portao pendente."""
    with sqlite3.connect(db.path) as raw:
        linhas = raw.execute(
            "SELECT t.task_id, t.status, t.pipeline_json, "
            "EXISTS(SELECT 1 FROM gates g WHERE g.task_id = t.task_id AND g.status = 'pending') "
            "FROM tasks t"
        ).fetchall()
    for task_id, status, pipeline, pendente in linhas:
        if status in VIVOS:
            assert (status == "awaiting_gate") == bool(pendente), (task_id, status, pendente)
        elif pipeline != "[]":
            assert not pendente, (task_id, status, "terminal com portao pendente")


def _linhas_de_comando(mensagem: str) -> list[str]:
    return [linha for linha in mensagem.splitlines() if linha.startswith("python ")]


def _rodar(linha: str) -> subprocess.CompletedProcess:
    argv = [a.strip('"') for a in shlex.split(linha, posix=False)]
    assert argv[0] == "python", linha
    return subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, timeout=60)


# --- Metade 1: quem nao lia `gates` -------------------------------------------


def test_confirmar_classificacao_preserva_escalation_e_fica_awaiting_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    db.open_gate("t-1", "escalation")

    task = db.confirm_classification(
        "t-1", tier="L2", kind="bug", pipeline=["systematic-debugging", "graph-context", "tdd", "verify"],
        source="semantic", confidence=0.7,
    )

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")
    _invariante(db)


def test_reclassify_preserva_escalation_e_fica_awaiting_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    db.open_gate("t-1", "escalation")

    task = db.reclassify("t-1", legacy_level="L1-feature", tier="L1", kind="feature", pipeline=FEATURE)

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")
    _invariante(db)


def test_reclassify_preserva_branch_open(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    _ramo(db)

    task = db.reclassify("t-1", legacy_level="L1-feature", tier="L1", kind="feature", pipeline=FEATURE)

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "branch-open:b-1")
    _invariante(db)


def test_aprovar_fase_portao_com_ramo_pendente_avanca_e_fica_awaiting_gate(tmp_path: Path):
    """Aprovar `approve-spec` e decisao explicita e avanca; o `branch-open` e
    independente e segura a proxima transicao."""
    db = state.HarnessDatabase(tmp_path)
    _na_fase_portao(db)
    task = _ramo(db)

    task = db.resolve_gate("t-1", "approve-spec", "approve", expected_revision=task["revision"])

    assert (task["phase"], task["status"], task["pending_gate"]) == ("tdd", "awaiting_gate", "branch-open:b-1")
    _invariante(db)


def test_reclassificar_cancela_portao_de_fase_e_nao_trava(tmp_path: Path):
    """O deadlock do caso 4: `approve-spec` preservado numa reclassificacao que
    zera a fase bloqueava a entrada na propria fase que o resolveria."""
    db = state.HarnessDatabase(tmp_path)
    _na_fase_portao(db)

    task = db.confirm_classification(
        "t-1", tier="L1", kind="feature", pipeline=SPEC, source="human_override", confidence=None,
    )

    assert (task["phase"], task["status"], task["pending_gate"]) == ("write-spec-light", "active", None)
    assert _portoes(db) == [("approve-spec", None, "cancelled", "reclassified")]
    db.record_artifact("t-1", "spec-light", "spec.md", "abc")
    task = db.transition("t-1", "approve-spec", expected_revision=db.task("t-1")["revision"])
    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "approve-spec")
    _invariante(db)


def test_reclassificar_com_portoes_misturados_decide_por_linha(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _na_fase_portao(db)
    db.open_gate("t-1", "escalation")

    task = db.reclassify("t-1", legacy_level="L1-feature", tier="L1", kind="feature", pipeline=FEATURE)

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")
    assert _portoes(db) == [
        ("approve-spec", None, "cancelled", "reclassified"),
        ("escalation", None, "pending", None),
    ]
    _invariante(db)


def test_touch_em_linha_verified_legada_com_portao_fica_awaiting_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    db.open_gate("t-1", "escalation")
    with sqlite3.connect(db.path) as raw:
        raw.execute("UPDATE tasks SET status = 'verified' WHERE task_id = 't-1'")

    task = db.touch_file("t-1", "a.py")

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")


def test_evidencia_em_linha_verified_legada_com_portao_fica_awaiting_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    db.open_gate("t-1", "escalation")
    with sqlite3.connect(db.path) as raw:
        raw.execute("UPDATE tasks SET status = 'verified' WHERE task_id = 't-1'")

    task = _evidencia(db)

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")


# --- D2: fechar com portao pendente recusa, com a linha que resolve -----------


def test_complete_recusa_com_escalation_pendente_e_a_linha_destrava(tmp_path: Path):
    """Medido real: 3 tasks fechadas por cima de um `escalation` aberto."""
    db = state.HarnessDatabase(tmp_path)
    _task(db, ["verify"])
    db.open_gate("t-1", "escalation")
    task = _evidencia(db)
    base = [sys.executable, str(CLI), "--home", str(tmp_path)]

    recusada = subprocess.run(
        [*base, "complete", "--task", "t-1", "--expect-revision", str(task["revision"])],
        capture_output=True, text=True, timeout=60,
    )

    assert recusada.returncode == 2, recusada.stdout + recusada.stderr
    assert "escalation" in recusada.stdout
    assert db.task("t-1")["revision"] == task["revision"], "recusar nao pode escrever"
    linhas = _linhas_de_comando(recusada.stdout)
    assert len(linhas) == 1, recusada.stdout
    resolvida = _rodar(linhas[0])
    assert resolvida.returncode == 0, resolvida.stdout + resolvida.stderr
    assert "complete" in recusada.stdout.splitlines()[-1]

    feita = db.complete("t-1", expected_revision=db.task("t-1")["revision"])
    assert (feita["status"], feita["pending_gate"]) == ("done", None)
    _invariante(db)


def test_complete_recusa_com_ramo_pendente(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db, ["verify"])
    _ramo(db)
    task = _evidencia(db)

    with pytest.raises(state.StateTransitionError) as erro:
        db.complete("t-1", expected_revision=task["revision"])

    assert "branch-open:b-1" in str(erro.value)
    assert "branch_state.py" in str(erro.value)
    assert db.task("t-1")["status"] == "awaiting_gate"


def test_confirmar_para_l0_recusa_com_escalation_e_a_linha_destrava(tmp_path: Path):
    """Medido real: 1 task `done` L0 com `escalation` pendente para sempre."""
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    antes = db.open_gate("t-1", "escalation")

    with pytest.raises(state.StateTransitionError) as erro:
        db.confirm_classification("t-1", tier="L0", kind="question", pipeline=[], source="semantic", confidence=0.9)

    assert db.task("t-1") == antes, "recusar nao pode escrever"
    assert db.classification("t-1")["final"] is None
    linhas = _linhas_de_comando(str(erro.value))
    assert len(linhas) == 1, str(erro.value)
    resolvida = _rodar(linhas[0])
    assert resolvida.returncode == 0, resolvida.stdout + resolvida.stderr

    task = db.confirm_classification("t-1", tier="L0", kind="question", pipeline=[], source="semantic", confidence=0.9)
    assert (task["status"], task["pending_gate"]) == ("done", None)


def test_confirmar_para_l0_cancela_portao_de_fase_sem_recusar(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _na_fase_portao(db)

    task = db.confirm_classification("t-1", tier="L0", kind="question", pipeline=[], source="semantic", confidence=0.9)

    assert (task["status"], task["pending_gate"]) == ("done", None)
    assert _portoes(db) == [("approve-spec", None, "cancelled", "reclassified")]


# --- D3: portao sobre task terminal nao ressuscita ----------------------------


def test_decidir_ramo_de_task_l0_nao_ressuscita_nem_estoura_o_indice(tmp_path: Path):
    """O caminho real do science-harness e o `IntegrityError` do caso 6."""
    db = state.HarnessDatabase(tmp_path)
    _task(db, [], tier="L0", kind="question")

    oferecida = _ramo(db)
    assert (oferecida["status"], oferecida["pending_gate"]) == ("done", "branch-open:b-1")
    _task(db, BUG, task_id="t-2")

    db.resolve_branch_decision("b-1", "park")

    assert (db.task("t-1")["status"], db.task("t-1")["pending_gate"]) == ("done", None)
    assert db.task("t-2")["status"] == "active"
    _invariante(db)


def test_aprovar_ramo_em_l0_nao_vira_task_viva(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db, [], tier="L0", kind="question")
    _ramo(db)

    db.approve_branch("b-1")

    assert db.task("t-1")["status"] == "done"
    assert db.current_task("s") is None


def test_open_gate_em_task_done_nao_ressuscita(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db, [], tier="L0", kind="question")

    task = db.open_gate("t-1", "escalation")

    assert task["status"] == "done"


def test_resolver_escalation_legado_de_task_done_nao_ressuscita(tmp_path: Path):
    """Os 3 `escalation` reais em task `done`: resolve-los nao pode reabrir."""
    db = state.HarnessDatabase(tmp_path)
    _task(db, ["verify"])
    task = _evidencia(db)
    task = db.complete("t-1", expected_revision=task["revision"])
    with sqlite3.connect(db.path) as raw:
        raw.execute(
            "INSERT INTO gates(task_id, gate_type, status, created_at) VALUES ('t-1', 'escalation', 'pending', 'x')"
        )
    _task(db, BUG, task_id="t-2")

    task = db.resolve_gate("t-1", "escalation", "approve", expected_revision=db.task("t-1")["revision"])

    assert (task["status"], task["pending_gate"]) == ("done", None)
    assert db.task("t-2")["status"] == "active"


def test_transition_em_task_abandonada_nao_ressuscita(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db)
    _task(db, BUG, task_id="t-2")

    task = db.transition("t-1", "tdd", expected_revision=db.task("t-1")["revision"])

    assert task["status"] == "abandoned"
    assert db.task("t-2")["status"] == "active"


# --- Metade 2: o que nao pode mudar -------------------------------------------


def test_promocao_l0_para_l1_continua_reabrindo(tmp_path: Path):
    """`harness-reclassify.sh` so promove task L0 `done`: e o ciclo de vida
    escolhido pelo reclassificador, nao ressurreicao."""
    db = state.HarnessDatabase(tmp_path)
    _task(db, [], tier="L0", kind="question")

    task = db.reclassify("t-1", legacy_level="L1-feature", tier="L1", kind="feature", pipeline=FEATURE)

    assert (task["status"], task["phase"]) == ("active", "write-spec-light")


def test_promocao_de_l0_com_ramo_pendente_fica_awaiting_gate(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    _task(db, [], tier="L0", kind="question")
    _ramo(db)

    task = db.reclassify("t-1", legacy_level="L1-feature", tier="L1", kind="feature", pipeline=FEATURE)

    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "branch-open:b-1")


# --- Projecao: `confirm_classification.py` copia `status` do banco ------------


def test_cli_de_confirmacao_projeta_o_status_do_banco(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _task(db, ["source-selection", "documentation", "verify"], tier="L1", kind="docs")
    db.open_gate("t-1", "escalation")
    (tmp_path / "state.json").write_text(json.dumps({
        "task_id": "t-1", "classification": "L1-docs", "status": "awaiting_gate",
        "pipeline": task["pipeline"],
        "classification_meta": {"suggested": "L1-docs", "final": None, "source": "regex"},
    }), encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(CONFIRM), "--final", "L1-bug", "--expect-task", "t-1", "--harness-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )

    assert r.returncode == 0, r.stdout + r.stderr
    projecao = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    banco = db.task("t-1")
    assert banco["status"] == "awaiting_gate"
    assert (projecao["status"], projecao["pending_gate"]) == (banco["status"], banco["pending_gate"])


# --- Migracao: o dado ja gravado ----------------------------------------------


def _banco_legado(home: Path) -> None:
    """Os quatro estados medidos nos bancos reais, gravados como o codigo
    antigo os deixava, e um L0 com ramo pendente que e estado valido."""
    db = state.HarnessDatabase(home)
    for task_id, pipeline in (("t-done", ["verify"]), ("t-l0viva", []), ("t-active", BUG), ("t-l0ramo", [])):
        db.start_task(scope_id=task_id, legacy_level="L1-bug", tier="L1", kind="bug",
                      pipeline=pipeline, prompt=task_id, task_id=task_id)
    with sqlite3.connect(db.path) as raw:
        raw.executescript(
            """
            UPDATE tasks SET status = 'done' WHERE task_id = 't-done';
            INSERT INTO gates(task_id, gate_type, status, created_at) VALUES ('t-done', 'escalation', 'pending', 'x');
            INSERT INTO gates(task_id, gate_type, status, created_at) VALUES ('t-done', 'approve-spec', 'pending', 'x');
            UPDATE tasks SET status = 'active' WHERE task_id = 't-l0viva';
            INSERT INTO gates(task_id, gate_type, status, created_at) VALUES ('t-active', 'escalation', 'pending', 'x');
            UPDATE tasks SET status = 'active' WHERE task_id = 't-active';
            INSERT INTO gates(task_id, gate_type, subject_id, status, created_at)
                VALUES ('t-l0ramo', 'branch-open', 'b-9', 'pending', 'x');
            """
        )


def _eventos(home: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(home / "harness.db") as raw:
        return raw.execute(
            "SELECT task_id, event_type FROM events WHERE event_type LIKE 'migracao-%' ORDER BY id"
        ).fetchall()


def test_migracao_repara_os_estados_medidos_e_deixa_evento(tmp_path: Path):
    _banco_legado(tmp_path)

    db = state.HarnessDatabase(tmp_path)

    assert [(g, s, d) for g, _, s, d in _portoes(db, "t-done")] == [
        ("escalation", "cancelled", "terminal-migration"),
        ("approve-spec", "cancelled", "terminal-migration"),
    ]
    assert db.task("t-done")["status"] == "done"
    assert db.task("t-l0viva")["status"] == "done"
    assert (db.task("t-active")["status"], db.task("t-active")["pending_gate"]) == ("awaiting_gate", "escalation")
    assert (db.task("t-l0ramo")["status"], db.task("t-l0ramo")["pending_gate"]) == ("done", "branch-open:b-9")
    assert sorted(_eventos(tmp_path)) == sorted([
        ("t-done", "migracao-portao-terminal"),
        ("t-l0viva", "migracao-l0-viva"),
        ("t-active", "migracao-status-derivado"),
    ])
    _invariante(db)


def test_migracao_e_idempotente(tmp_path: Path):
    _banco_legado(tmp_path)
    state.HarnessDatabase(tmp_path)
    antes = _eventos(tmp_path)

    state.HarnessDatabase(tmp_path)

    assert _eventos(tmp_path) == antes

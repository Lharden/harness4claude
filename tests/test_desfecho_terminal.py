"""Desfecho terminal nao muda: nenhum escritor de `tasks.status` o reescreve.

`TERMINAL_STATUSES` diz, desde 2026-09-03, que um desfecho ja registrado nao
muda mais, e `abandon_task`, `record_evidence` e `touch_files` respeitam. Os
outros escritores de `tasks.status` nao olhavam o status de partida:

- `complete` levava `superseded` a `done`. Incidente real: balde
  `master-harness-5a8ec6a2/.../5a45e264-...-cf26e8ce`, task
  t-20260928-203110092956, `superseded` as 21:17:31Z e `done` as 21:33:58Z por
  `state_cli complete`;
- `transition` e `resolve_gate` devolviam a task a `active`/`awaiting_gate`; com
  outra task viva no escopo, estouravam `one_active_task_per_scope` com
  `sqlite3.IntegrityError`, que o `state_cli` nao captura;
- `reclassify`, `confirm_classification`, `open_gate` e `create_branch` faziam o
  mesmo por outros caminhos;
- `request_branch_approval` e `resolve_branch_decision` reescreviam o status da
  task dona do ramo.

Diagnostico em `docs/specs/desfecho-terminal-diagnostico.md`.

Decisoes do usuario (2026-09-30): D1 `complete` em task terminal recusa,
`superseded` inclusive; D2 ramo de task terminal se resolve sem tocar o status
dela; D3 uma regra so para os nove escritores, com uma excecao nomeada — task
`done` sem pipeline (L0) nao e desfecho de trabalho.

Metade 1 (o defeito): cada recusa reprova no codigo anterior, onde a escrita
passa ou estoura IntegrityError. Metade 2 (o que nao pode mudar): o ramo segue
fechando depois da task dona, e a task L0 segue promovivel e aceitando oferta de
ramo.
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

CLI = ROOT / "scripts" / "state_cli.py"
PIPELINE_BUG = ["systematic-debugging", "tdd", "verify"]
ESCOPO = "s"


# --- Como uma task chega a cada desfecho, pelos caminhos de producao ---------


def _bug(db, prompt="conserta o bug"):
    return db.start_task(
        scope_id=ESCOPO, legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=PIPELINE_BUG, prompt=prompt,
    )


def _l0(db, prompt="sim"):
    """Prompt L0: `start_task` sem fases grava `done` na hora."""
    return db.start_task(
        scope_id=ESCOPO, legacy_level="L0-question", tier="L0", kind="question",
        pipeline=[], prompt=prompt,
    )


def _verificar(db, task_id):
    return db.record_evidence(
        task_id, evidence_type="test", command="python -m pytest -q",
        exit_code=0, tests_collected=5, tests_passed=5, output_hash="h",
    )


def _avancar(db, task, ate):
    inicio = PIPELINE_BUG.index(task["phase"]) + 1
    for fase in PIPELINE_BUG[inicio:PIPELINE_BUG.index(ate) + 1]:
        task = db.transition(task["task_id"], fase, expected_revision=task["revision"])
    return task


def _superseded(db, *, fase):
    """Verificada e substituida pelo prompt seguinte, que abre trabalho vivo."""
    task = _avancar(db, _bug(db), fase)
    _verificar(db, task["task_id"])
    viva = _bug(db, prompt="agora outro pedido")
    morta = db.task(task["task_id"])
    assert (morta["status"], morta["verified"]) == ("superseded", True)
    return morta, db.task(viva["task_id"])


def _done(db, *, fase="verify"):
    """Concluida por `complete`; depois o prompt seguinte abre trabalho vivo."""
    assert fase == "verify", "`complete` so fecha na fase final"
    task = _verificar(db, _avancar(db, _bug(db), "verify")["task_id"])
    morta = db.complete(task["task_id"], expected_revision=task["revision"])
    viva = _bug(db, prompt="agora outro pedido")
    assert morta["status"] == "done"
    return morta, db.task(viva["task_id"])


def _abandoned(db, *, fase):
    """Encerrada por `record_signal --abandoned` (-> `abandon_task`) com a
    verificacao feita — o caso em que `complete` ainda acharia evidencia —, e
    depois o prompt seguinte abre trabalho vivo."""
    task = _verificar(db, _avancar(db, _bug(db), fase)["task_id"])
    morta = db.abandon_task(task["task_id"], reason="troca de assunto")
    viva = _bug(db, prompt="agora outro pedido")
    assert (morta["status"], morta["verified"]) == ("abandoned", True)
    return morta, db.task(viva["task_id"])


DESFECHOS = {"superseded": _superseded, "done": _done, "abandoned": _abandoned}


# --- Instrumento: "recusar nao escreve", medido no banco inteiro -------------


def _foto(db, task_id):
    """Tudo o que o banco guarda sobre a task: a linha e as linhas-filhas."""
    with sqlite3.connect(db.path) as raw:
        raw.row_factory = sqlite3.Row
        foto = {"tasks": [dict(r) for r in raw.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))]}
        for tabela in ("gates", "transitions", "branches", "classifications", "artifacts"):
            foto[tabela] = [
                dict(r) for r in raw.execute(f"SELECT * FROM {tabela} WHERE task_id = ?", (task_id,))
            ]
    return foto


def _recusa(db, morta, viva, operacao) -> str:
    antes = (_foto(db, morta["task_id"]), _foto(db, viva["task_id"]))
    with pytest.raises(state.StateTransitionError) as erro:
        operacao()
    depois = (_foto(db, morta["task_id"]), _foto(db, viva["task_id"]))
    assert depois == antes, "recusar nao pode escrever, nem na task morta nem na viva"
    return str(erro.value)


def _proxima_fase(task):
    return PIPELINE_BUG[PIPELINE_BUG.index(task["phase"]) + 1]


OPERACOES = {
    "complete": lambda db, t: db.complete(t["task_id"], expected_revision=t["revision"]),
    "transition": lambda db, t: db.transition(t["task_id"], _proxima_fase(t), expected_revision=t["revision"]),
    "reclassify": lambda db, t: db.reclassify(
        t["task_id"], legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"],
    ),
    "confirm_classification": lambda db, t: db.confirm_classification(
        t["task_id"], tier="L1", kind="bug", pipeline=PIPELINE_BUG, source="semantic", confidence=0.9,
    ),
    "open_gate": lambda db, t: db.open_gate(t["task_id"], "answer-clarifications"),
    "create_branch": lambda db, t: db.create_branch(
        t["task_id"], branch_id="b-novo", slug="novo", name="Novo", topic="assunto novo",
        topic_hash="h-novo", offered_turn=50, explicito=True,
    ),
}

# (operacao, desfecho, fase em que a task terminou). `complete` precisa da fase
# final, onde ele fecharia; `transition` precisa de uma fase com seguinte, senao
# a recusa seria "next phase must be <terminal>" e o teste passaria no codigo
# antigo pelo motivo errado. `done` so existe na fase final.
MATRIZ = [
    ("complete", "superseded", "verify"),
    ("complete", "done", "verify"),
    ("complete", "abandoned", "verify"),
    ("transition", "superseded", "tdd"),
    ("transition", "abandoned", "tdd"),
    *[
        (operacao, desfecho, "verify" if desfecho == "done" else "tdd")
        for operacao in ("reclassify", "confirm_classification", "open_gate", "create_branch")
        for desfecho in ("superseded", "done", "abandoned")
    ],
]


# --- Metade 1: todo escritor de status recusa desfecho registrado -------------


@pytest.mark.parametrize(("operacao", "desfecho", "fase"), MATRIZ, ids=[f"{o}-{d}" for o, d, _ in MATRIZ])
def test_escritor_de_status_recusa_desfecho_registrado(tmp_path: Path, operacao, desfecho, fase):
    db = state.HarnessDatabase(tmp_path)
    morta, viva = DESFECHOS[desfecho](db, fase=fase)

    mensagem = _recusa(db, morta, viva, lambda: OPERACOES[operacao](db, morta))

    assert mensagem.startswith(f"{operacao} recusada"), mensagem
    assert f"ja terminou como `{desfecho}`" in mensagem
    assert "desfecho registrado nao muda" in mensagem
    assert viva["task_id"] in mensagem, "a recusa aponta para onde o trabalho continua"


def test_incidente_complete_em_superseded_pela_linha_de_comando(tmp_path: Path):
    """A forma exata do incidente: task L1-bug verificada na fase final, o `sim`
    L0 a substitui, e o `state_cli complete` a levava a `done` com exit 0."""
    db = state.HarnessDatabase(tmp_path)
    task = _verificar(db, _avancar(db, _bug(db), "verify")["task_id"])
    sim = _l0(db)
    morta = db.task(task["task_id"])
    assert morta["status"] == "superseded"
    projecao = tmp_path / "state.json"
    projecao.write_text(json.dumps({"task_id": sim["task_id"], "status": "done"}), encoding="utf-8")
    antes = (_foto(db, morta["task_id"]), projecao.read_bytes())

    saida = subprocess.run(
        [sys.executable, str(CLI), "--home", str(tmp_path), "complete",
         "--task", morta["task_id"], "--expect-revision", str(morta["revision"])],
        capture_output=True, text=True, timeout=60,
    )

    assert saida.returncode == 2, saida.stdout + saida.stderr
    assert saida.stdout.startswith("erro: complete recusada"), saida.stdout
    assert "ja terminou como `superseded`" in saida.stdout
    assert "Nao ha task viva neste escopo" in saida.stdout, "o `sim` L0 nasceu `done`: nada vivo"
    assert (_foto(db, morta["task_id"]), projecao.read_bytes()) == antes, (
        "nem o banco nem a projecao mudam: a task morta nao e projetada por cima da do `sim`"
    )


def test_transition_em_superseded_com_task_viva_sai_2_e_nao_estoura(tmp_path: Path):
    """Com outra task viva, a ressurreicao violava `one_active_task_per_scope`: o
    `state_cli` so captura StateTransitionError/KeyError/ValueError, entao o
    IntegrityError saia como traceback com exit 1."""
    db = state.HarnessDatabase(tmp_path)
    morta, viva = _superseded(db, fase="tdd")

    saida = subprocess.run(
        [sys.executable, str(CLI), "--home", str(tmp_path), "transition",
         "--task", morta["task_id"], "--to", "verify", "--expect-revision", str(morta["revision"])],
        capture_output=True, text=True, timeout=60,
    )

    assert saida.returncode == 2, saida.stdout + saida.stderr
    assert "Traceback" not in saida.stderr, saida.stderr
    assert saida.stdout.startswith("erro: transition recusada"), saida.stdout
    assert db.task(morta["task_id"])["status"] == "superseded"
    assert db.task(viva["task_id"]) == viva


def test_resolve_gate_de_escalation_em_task_concluida_recusa(tmp_path: Path):
    """`complete` nao olha portao pendente, entao um `escalation` aberto pelo Stop
    sobrevive ao fecho. Aprova-lo depois passava por `_resolve_escalation`, que
    devolvia a task `done` a `active`."""
    db = state.HarnessDatabase(tmp_path)
    task = _avancar(db, _bug(db), "verify")
    for _ in range(3):
        task = db.register_stop_continuation(task["task_id"], limit=2)
    assert task["pending_gate"] == "escalation"
    task = _verificar(db, task["task_id"])
    morta = db.complete(task["task_id"], expected_revision=task["revision"])
    assert (morta["status"], morta["pending_gate"]) == ("done", "escalation")
    viva = _bug(db, prompt="agora outro pedido")

    mensagem = _recusa(
        db, morta, viva,
        lambda: db.resolve_gate(morta["task_id"], "escalation", "approve", expected_revision=morta["revision"]),
    )

    assert mensagem.startswith("resolve_gate recusada"), mensagem
    assert "ja terminou como `done`" in mensagem


def test_recusa_por_desfecho_vem_antes_da_revisao(tmp_path: Path):
    """Quem tenta fechar uma task substituida carrega a revisao de antes da troca.
    "revision mismatch" o mandaria reler e tentar de novo, para so entao ouvir o
    motivo real; o motivo real vem primeiro."""
    db = state.HarnessDatabase(tmp_path)
    morta, viva = _superseded(db, fase="verify")

    mensagem = _recusa(db, morta, viva, lambda: db.complete(morta["task_id"], expected_revision=0))

    assert "ja terminou como `superseded`" in mensagem
    assert "revision mismatch" not in mensagem


# --- Metade 2a: o ramo sobrevive a task dona (D2) ----------------------------


def _ramo_oferecido(db, task):
    db.create_branch(
        task["task_id"], branch_id="b-1", slug="ramo", name="Ramo", topic="assunto paralelo",
        topic_hash="h-ramo", offered_turn=1, explicito=True,
    )
    return db.task(task["task_id"])


def _dona_terminal(db, desfecho):
    """Ramo oferecido na task viva; depois a task termina pelo caminho do desfecho."""
    task = _ramo_oferecido(db, _avancar(db, _bug(db), "verify"))
    task = _verificar(db, task["task_id"])
    if desfecho == "done":
        # `complete` nao cancela o portao do ramo: ele segue pendente.
        db.complete(task["task_id"], expected_revision=task["revision"])
    elif desfecho == "abandoned":
        db.abandon_task(task["task_id"], reason="troca de assunto")
    viva = _bug(db, prompt="agora outro pedido")  # superseded, se ainda viva
    dona = db.task(task["task_id"])
    assert dona["status"] == desfecho
    return dona, db.task(viva["task_id"])


@pytest.mark.parametrize("desfecho", ["superseded", "done", "abandoned"])
def test_abrir_ramo_de_task_terminal_nao_ressuscita_a_dona(tmp_path: Path, desfecho):
    """A sequencia de `branch_state.set_status(..., "open")` num ramo ainda sem
    aprovacao: pede o portao, aprova, abre. Antes, `request_branch_approval`
    punha a dona em `awaiting_gate` e `approve_branch` em `active`."""
    db = state.HarnessDatabase(tmp_path)
    dona, viva = _dona_terminal(db, desfecho)

    db.request_branch_approval("b-1")
    db.approve_branch("b-1")
    ramo = db.open_branch("b-1", seed_path="seed.md")

    assert ramo["status"] == "open" and ramo["approved_at"]
    depois = db.task(dona["task_id"])
    assert (depois["status"], depois["revision"]) == (dona["status"], dona["revision"])
    assert depois["pending_gate"] is None, "o portao do ramo foi pedido e resolvido"
    assert db.task(viva["task_id"]) == viva


def test_parkear_ramo_de_task_concluida_nao_a_reabre(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    dona, viva = _dona_terminal(db, "done")
    assert dona["pending_gate"] == "branch-open:b-1"

    db.resolve_branch_decision("b-1", "park")

    depois = db.task(dona["task_id"])
    assert (depois["status"], depois["revision"]) == ("done", dona["revision"])
    assert depois["pending_gate"] is None, "a decisao do usuario foi gravada"
    assert db.task(viva["task_id"]) == viva


# --- Metade 2b: task L0 nao e desfecho de trabalho (excecao de D3) ------------


def test_task_l0_segue_promovivel_pelo_reclassify(tmp_path: Path):
    """O hook de reclassificacao promove a task L0 depois de 3 arquivos."""
    db = state.HarnessDatabase(tmp_path)
    task = _l0(db, prompt="renomeie a funcao")
    assert task["status"] == "done"

    promovida = db.reclassify(
        task["task_id"], legacy_level="L1-feature", tier="L1", kind="feature",
        pipeline=["write-spec-light", "tdd", "verify-against-spec"],
    )

    assert (promovida["status"], promovida["phase"]) == ("active", "write-spec-light")


def test_task_l0_segue_corrigivel_pela_confirmacao(tmp_path: Path):
    """`confirm_classification.py --final L1-bug` sobre o palpite L0 do regex."""
    db = state.HarnessDatabase(tmp_path)
    task = _l0(db, prompt="o login quebra quando a senha tem acento?")

    corrigida = db.confirm_classification(
        task["task_id"], tier="L1", kind="bug", pipeline=PIPELINE_BUG, source="semantic", confidence=0.8,
    )

    assert (corrigida["status"], corrigida["phase"]) == ("active", "systematic-debugging")


def test_task_l0_aceita_oferta_de_ramo(tmp_path: Path):
    """Numa conversa L0 a projecao aponta a task L0, e e nela que
    `branch_state.add` registra a oferta (`_projected_task` nao filtra status)."""
    db = state.HarnessDatabase(tmp_path)
    task = _l0(db)

    ramo = db.create_branch(
        task["task_id"], branch_id="b-l0", slug="l0", name="L0", topic="ideia com vida propria",
        topic_hash="h-l0", offered_turn=3, explicito=True,
    )

    assert ramo["status"] == "pending"
    assert db.task(task["task_id"])["pending_gate"] == "branch-open:b-l0"

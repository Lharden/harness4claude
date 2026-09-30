"""Portao de Stop antes da fase de implementacao das pipelines de codigo.

Incidente 2026-09-30, sessao `e0209356` (projeto `moneytree_farmer`, sem codigo
nenhum), task `t-20260930-053116733897`, `L2-architecture`. Na fase `discuss`
(1 de 11) o Stop bloqueou pedindo evidencia de pytest com `evidence` vazia: a
unica saida "verde" seria fabricar teste. `cobra_evidencia_nesta_fase` devolvia
`True` em toda fase para o tipo `test`; so docs tinha a excecao D1.

Diagnostico, decisoes do grill (D-G1 a D-G5) e ACs em
`docs/specs/portao-stop-pre-implementacao-diagnostico.md`.
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


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load("pre_impl_state", "scripts/transactional_state.py")
paths = _load("pre_impl_paths", "scripts/harness_paths.py")
hook = _load("pre_impl_hook", "hooks/harness-transactional.py")

PIPELINES = json.loads((ROOT / "contract" / "pipelines.json").read_text(encoding="utf-8"))["pipelines"]
SESSAO = "sessao-pre-impl"


def _task(tmp_path: Path, nome: str):
    """Task viva com a pipeline `nome` do contrato, num repo sem codigo."""
    cwd = tmp_path / "repo"
    cwd.mkdir(parents=True, exist_ok=True)
    raiz = tmp_path / "harness"
    bucket = paths.ensure_state_dir(raiz, cwd, session_id=SESSAO)
    database = state.HarnessDatabase(bucket)
    tier, kind = nome.split("-", 1)
    task = database.start_task(
        scope_id=str(bucket), legacy_level=nome, tier=tier, kind=kind,
        pipeline=PIPELINES[nome], prompt="planejar",
    )
    (bucket / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}), encoding="utf-8"
    )
    return cwd, raiz, bucket, database, task


def _ate(database, task_id: str, destino: str) -> dict:
    """Anda o pipeline ate `destino` pelos caminhos de producao.

    Cumpre as obrigacoes de artefato e aprova as fases-portao com
    `resolve_gate`, que e o que avanca delas.
    """
    atual = database.task(task_id)
    while atual["phase"] != destino:
        fase = atual["phase"]
        if atual["pending_gate"] == fase:
            atual = database.resolve_gate(task_id, fase, "approve", expected_revision=atual["revision"])
            continue
        if fase in state.ARTIFACT_OBLIGATIONS:
            atual = database.record_artifact(task_id, state.ARTIFACT_OBLIGATIONS[fase], "docs/x.md", None)
        proxima = atual["pipeline"][atual["pipeline"].index(fase) + 1]
        atual = database.transition(task_id, proxima, expected_revision=atual["revision"])
    return atual


def _stop(cwd: Path, raiz: Path) -> str:
    return hook.handle_payload(
        {"hook_event_name": "Stop", "cwd": str(cwd), "session_id": SESSAO}, harness_root=raiz
    )


def _bash(cwd: Path, raiz: Path, comando: str) -> str:
    return hook.handle_payload(
        {"hook_event_name": "PostToolUse", "cwd": str(cwd), "session_id": SESSAO,
         "tool_name": "Bash", "tool_input": {"command": comando},
         "tool_response": {"exit_code": 0, "output": ""}},
        harness_root=raiz,
    )


def _nao_bloqueia(cwd, raiz, database, task_id):
    assert _stop(cwd, raiz) == "", f"o Stop cobrou teste em {database.task(task_id)['phase']}"
    assert database.task(task_id)["stop_continuations"] == 0
    with pytest.raises(state.StateTransitionError):
        database.register_stop_continuation(task_id, limit=2)


def _bloqueia(cwd, raiz, database, task_id) -> str:
    saida = _stop(cwd, raiz)
    assert saida, f"o Stop nao cobrou teste em {database.task(task_id)['phase']}"
    bloqueio = json.loads(saida)
    assert bloqueio["decision"] == "block"
    assert "--type test" in bloqueio["reason"]
    return bloqueio["reason"]


# ---------------------------------------------------------------------------
# Metade 1 — antes da implementacao o Stop nao cobra
# ---------------------------------------------------------------------------


def test_AC1_reproducao_L2_architecture_em_discuss_nao_bloqueia(tmp_path: Path):
    """O incidente: `discuss`, repo sem codigo, `evidence` vazia."""
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L2-architecture")
    assert database.task(task["task_id"])["phase"] == "discuss"

    _nao_bloqueia(cwd, raiz, database, task["task_id"])


@pytest.mark.parametrize(
    ("pipeline", "fase"),
    [("L2-architecture", "write-spec"), ("L2-architecture", "design-doc"),
     ("L1-feature", "write-spec-light")],
)
def test_AC2_fases_de_planejamento_nao_bloqueiam(tmp_path: Path, pipeline: str, fase: str):
    cwd, raiz, _bucket, database, task = _task(tmp_path, pipeline)
    _ate(database, task["task_id"], fase)

    _nao_bloqueia(cwd, raiz, database, task["task_id"])


# ---------------------------------------------------------------------------
# Metade 2 — da implementacao em diante o guarda continua pegando
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fase", ["tdd", "verify-multimodel"])
def test_AC3_CONTROLE_implementacao_sem_evidencia_bloqueia_e_conta(tmp_path: Path, fase: str):
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L2-architecture")
    _ate(database, task["task_id"], fase)

    _bloqueia(cwd, raiz, database, task["task_id"])

    assert database.task(task["task_id"])["stop_continuations"] == 1


def _linha_da_receita(motivo: str) -> str:
    return next(linha for linha in motivo.splitlines() if "state_cli.py" in linha)


def _rodar(linha: str) -> subprocess.CompletedProcess:
    """Roda a linha como quem a copiou da mensagem: tokens do shell, sem shell."""
    argv = [a.strip('"') for a in shlex.split(linha, posix=False)]
    assert argv[0] == "python", linha
    return subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("fase", ["tdd", "verify-multimodel"])
def test_AC4_CONTROLE_evidencia_valida_da_receita_libera(tmp_path: Path, fase: str):
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L2-architecture")
    _ate(database, task["task_id"], fase)
    motivo = _bloqueia(cwd, raiz, database, task["task_id"])

    receita = _linha_da_receita(motivo)
    for marcador, valor in {"<N>": "12", "<P>": "11", "<S>": "1"}.items():
        receita = receita.replace(marcador, valor)
    cli = _rodar(receita)
    assert cli.returncode == 0, f"a receita impressa nao roda:\n{receita}\n{cli.stdout}{cli.stderr}"
    _bash(cwd, raiz, receita)

    assert database.task(task["task_id"])["verified"] is True
    assert _stop(cwd, raiz) == ""


def test_AC5_L2_bug_cobra_nas_cinco_fases(tmp_path: Path):
    """D-G1: regra posicional; `systematic-debugging` e a fase 1 de bug."""
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L2-bug")
    for fase in PIPELINES["L2-bug"]:
        _ate(database, task["task_id"], fase)
        _bloqueia(cwd, raiz, database, task["task_id"])
        # O Stop seguinte ao bloqueio passa (`stop_hook_active`); zera para
        # medir a fase seguinte sem escalar.
        with sqlite3.connect(database.path) as raw:
            raw.execute("UPDATE tasks SET stop_continuations = 0")


@pytest.mark.parametrize("pipeline", ["L1-review", "L2-review"])
def test_AC6_review_sem_fase_de_implementacao_cobra_em_toda_fase(pipeline: str):
    """D-G2: falha fechada, como antes."""
    fases = PIPELINES[pipeline]
    assert all(state.cobra_evidencia_nesta_fase("review", fases, fase) for fase in fases)


def test_AC7_reclassificada_depois_do_tdd_continua_cobrada(tmp_path: Path):
    """D-G3: voltar ao indice 0 nao e atalho para sair do portao."""
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L1-feature")
    _ate(database, task["task_id"], "tdd")

    database.confirm_classification(
        task["task_id"], tier="L2", kind="feature", pipeline=PIPELINES["L2-feature"],
        source="human_override", confidence=None,
    )

    assert database.task(task["task_id"])["phase"] == "discuss"
    _bloqueia(cwd, raiz, database, task["task_id"])


def test_AC8_correcao_de_classificacao_no_inicio_nao_marca(tmp_path: Path):
    """D-G4: o regex chutou bug; a correcao semantica nao e avanco."""
    cwd, raiz, _bucket, database, task = _task(tmp_path, "L1-bug")
    assert database.task(task["task_id"])["phase"] == "systematic-debugging"

    database.confirm_classification(
        task["task_id"], tier="L2", kind="feature", pipeline=PIPELINES["L2-feature"],
        source="semantic", confidence=0.8,
    )

    assert database.task(task["task_id"])["passou_pela_implementacao"] is False
    _nao_bloqueia(cwd, raiz, database, task["task_id"])


def test_AC9_so_o_avanco_grava_a_marca(tmp_path: Path):
    """`transition` e `resolve_gate` marcam; `confirm_classification` nao."""
    _cwd, _raiz, _bucket, database, bug = _task(tmp_path / "bug", "L1-bug")
    assert database.task(bug["task_id"])["passou_pela_implementacao"] is False
    saiu = database.transition(bug["task_id"], "tdd", expected_revision=bug["revision"])
    assert saiu["passou_pela_implementacao"] is True, "sair de systematic-debugging nao marcou"

    _cwd, _raiz, _bucket, database, feat = _task(tmp_path / "feat", "L1-feature")
    feat = database.record_artifact(feat["task_id"], "spec-light", "docs/x.md", None)
    entrou = database.transition(feat["task_id"], "tdd", expected_revision=feat["revision"])
    assert entrou["passou_pela_implementacao"] is True, "entrar em tdd nao marcou"

    _cwd, _raiz, _bucket, database, arq = _task(tmp_path / "arq", "L2-architecture")
    antes = _ate(database, arq["task_id"], "approve-plan")
    assert antes["passou_pela_implementacao"] is False
    aprovada = database.resolve_gate(arq["task_id"], "approve-plan", "approve",
                                     expected_revision=antes["revision"])
    assert aprovada["phase"] == "tdd"
    assert aprovada["passou_pela_implementacao"] is True, "resolve_gate para tdd nao marcou"


#: Pipelines de tipo `test` sem fase de implementacao, e por que cobram em toda
#: fase. Pipeline nova assim reprova o AC-10 ate alguem decidir.
SEM_IMPLEMENTACAO_DECLARADOS = {
    "review": "D-G2: cobra em toda fase, como antes; dono da evidencia propria: sessao pai",
}


def test_AC10_fases_de_implementacao_amarradas_ao_contrato():
    fases_do_contrato = {fase for fases in PIPELINES.values() for fase in fases}
    fantasmas = sorted(state.FASES_DE_IMPLEMENTACAO - fases_do_contrato)
    assert not fantasmas, f"FASES_DE_IMPLEMENTACAO cita fase que o contrato nao tem: {fantasmas}"

    sem_implementacao = sorted({
        nome for nome, fases in PIPELINES.items()
        if fases
        and state.tipo_de_evidencia(nome.split("-", 1)[1]) == "test"
        and not state.FASES_DE_IMPLEMENTACAO & set(fases)
    })
    orfas = [n for n in sem_implementacao if n.split("-", 1)[1] not in SEM_IMPLEMENTACAO_DECLARADOS]
    assert not orfas, (
        f"pipeline de tipo test sem fase de implementacao e sem decisao: {orfas}. "
        "Nomeie a fase em FASES_DE_IMPLEMENTACAO ou declare em SEM_IMPLEMENTACAO_DECLARADOS."
    )


def test_AC10_censo_do_contrato_25_de_58():
    """O censo da funcao de producao sobre o contrato inteiro (diagnostico)."""
    pares = [(nome, fase) for nome, fases in PIPELINES.items() for fase in fases]
    cobrados = [
        (nome, fase) for nome, fase in pares
        if state.cobra_evidencia_nesta_fase(nome.split("-", 1)[1], PIPELINES[nome], fase)
    ]
    assert (len(cobrados), len(pares)) == (25, 58), cobrados


def test_AC11_fase_fora_da_pipeline_cobra():
    fases = PIPELINES["L2-feature"]
    assert state.cobra_evidencia_nesta_fase("feature", fases, "fase-que-nao-existe") is True
    assert state.cobra_evidencia_nesta_fase("feature", fases, None) is True

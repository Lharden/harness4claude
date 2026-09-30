"""`transition` nao avanca fase com portao humano pendente.

`transition` conferia revisao, `owner_epoch`, ordem das fases e artefato, e nao
lia a tabela `gates`. Escrevia `status` so a partir da fase de destino, entao
sobrescrevia o `awaiting_gate` que o portao pendente tinha posto:

- `escalation` do Stop: a task ia para `active` com o portao pendente, e o Stop
  seguinte a devolvia ao portao, porque `stop_continuations` seguia no limite;
- `branch-open`: o ramo oferecido perdia a espera;
- `approve-spec` / `approve-plan`: pedir a fase seguinte PULAVA a decisao
  humana, e a linha de `gates` ficava `pending` para sempre.

Diagnostico em `docs/specs/transition-ignora-portao-diagnostico.md`.

Metade 1 (o defeito): as recusas reprovam no codigo anterior, onde a transicao
passa. Metade 2 (o que nao pode mudar): a linha que a recusa imprime roda, e
depois dela a mesma transicao passa — portao resolvido nao bloqueia.
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
    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "escalation")
    return task


def _na_fase_portao(db):
    """Task parada em `approve-spec`, com o portao aberto pela propria transicao."""
    task = db.start_task(
        scope_id="s", legacy_level="L2-feature", tier="L2", kind="feature",
        pipeline=["write-spec-light", "approve-spec", "tdd"], prompt="build",
    )
    db.record_artifact(task["task_id"], "spec-light", "spec.md", "abc")
    task = db.transition(task["task_id"], "approve-spec", expected_revision=db.task(task["task_id"])["revision"])
    assert (task["status"], task["pending_gate"]) == ("awaiting_gate", "approve-spec")
    return task


def _transicoes(db, task_id):
    with sqlite3.connect(db.path) as raw:
        return raw.execute("SELECT COUNT(*) FROM transitions WHERE task_id = ?", (task_id,)).fetchone()[0]


def _recusa(db, task, to_phase) -> str:
    with pytest.raises(state.StateTransitionError) as erro:
        db.transition(task["task_id"], to_phase, expected_revision=task["revision"])
    return str(erro.value)


def _linhas_de_comando(mensagem: str) -> list[str]:
    return [linha for linha in mensagem.splitlines() if linha.startswith("python ")]


def _rodar(linha: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Roda a linha como quem a copiou da mensagem: tokens do shell, sem shell."""
    argv = [a.strip('"') for a in shlex.split(linha, posix=False)]
    assert argv[0] == "python", linha
    return subprocess.run(
        [sys.executable, *argv[1:]], capture_output=True, text=True, timeout=60, cwd=cwd,
    )


def _nada_escrito(db, antes, transicoes_antes):
    depois = db.task(antes["task_id"])
    assert depois["revision"] == antes["revision"], "recusar nao pode escrever"
    assert depois["phase"] == antes["phase"]
    assert depois["status"] == "awaiting_gate"
    assert depois["pending_gate"] == antes["pending_gate"]
    assert _transicoes(db, antes["task_id"]) == transicoes_antes


# --- Metade 1: a recusa -------------------------------------------------------


def test_transition_recusa_com_escalation_pendente(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    transicoes = _transicoes(db, task["task_id"])

    mensagem = _recusa(db, task, "tdd")

    assert "escalation" in mensagem
    _nada_escrito(db, task, transicoes)


def test_transition_recusa_com_branch_open_pendente(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _task_bug(db)
    db.create_branch(
        task["task_id"], branch_id="b-1", slug="ramo", name="Ramo", topic="outro assunto",
        topic_hash="h1", offered_turn=1, explicito=True,
    )
    task = db.task(task["task_id"])
    transicoes = _transicoes(db, task["task_id"])

    mensagem = _recusa(db, task, "tdd")

    assert "branch-open:b-1" in mensagem
    _nada_escrito(db, task, transicoes)


def test_transition_nao_pula_a_fase_portao(tmp_path: Path):
    """O caso mais grave: pedir a fase seguinte saia do `approve-spec` sem decisao."""
    db = state.HarnessDatabase(tmp_path)
    task = _na_fase_portao(db)
    transicoes = _transicoes(db, task["task_id"])

    mensagem = _recusa(db, task, "tdd")

    assert "approve-spec" in mensagem
    _nada_escrito(db, task, transicoes)


# --- Metade 1b + 2: a linha impressa roda e destrava --------------------------


def test_cli_recusa_e_a_linha_impressa_destrava_o_escalation(tmp_path: Path):
    """A superficie do usuario, ponta a ponta, pelo `state_cli` em subprocesso."""
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    base = [sys.executable, str(CLI), "--home", str(tmp_path)]

    recusada = subprocess.run(
        [*base, "transition", "--task", task["task_id"], "--to", "tdd",
         "--expect-revision", str(task["revision"])],
        capture_output=True, text=True, timeout=60,
    )
    assert recusada.returncode == 2, recusada.stdout + recusada.stderr
    linhas = _linhas_de_comando(recusada.stdout)
    assert len(linhas) == 1, recusada.stdout

    resolvida = _rodar(linhas[0])
    assert resolvida.returncode == 0, resolvida.stdout + resolvida.stderr

    revisao = db.task(task["task_id"])["revision"]
    avancada = subprocess.run(
        [*base, "transition", "--task", task["task_id"], "--to", "tdd",
         "--expect-revision", str(revisao)],
        capture_output=True, text=True, timeout=60,
    )
    assert avancada.returncode == 0, avancada.stdout + avancada.stderr
    projecao = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert (projecao["current_step"], projecao["status"], projecao["pending_gate"]) == ("tdd", "active", None)


def test_linha_impressa_na_fase_portao_aprova_e_avanca(tmp_path: Path):
    db = state.HarnessDatabase(tmp_path)
    task = _na_fase_portao(db)

    mensagem = _recusa(db, task, "tdd")
    linhas = _linhas_de_comando(mensagem)
    assert len(linhas) == 1
    assert "--type approve-spec" in linhas[0]

    resolvida = _rodar(linhas[0])

    assert resolvida.returncode == 0, resolvida.stdout + resolvida.stderr
    depois = db.task(task["task_id"])
    assert (depois["phase"], depois["status"], depois["pending_gate"]) == ("tdd", "active", None)
    # A aprovacao da fase-portao ja avanca. Mandar repetir a transicao seria
    # instrucao impossivel: `tdd` de novo sai com "next phase must be verify".
    assert "rode a transicao de novo" not in mensagem
    assert "`tdd`" in mensagem


def test_linha_impressa_para_branch_open_parkeia_e_destrava(tmp_path: Path, monkeypatch):
    """`branch-open` nao passa pelo `state_cli gate`: o dono e `branch_state.py`.

    O ramo nasce pelo caminho de producao (`branch_state.add`), que grava o
    registro em `branches.json` e o portao no banco. A linha impressa roda na
    pasta do projeto, como o modelo a rodaria.
    """
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    bs_spec = importlib.util.spec_from_file_location("branch_state", ROOT / "scripts" / "branch_state.py")
    assert bs_spec is not None and bs_spec.loader is not None
    bs = importlib.util.module_from_spec(bs_spec)
    monkeypatch.setitem(sys.modules, "branch_state", bs)
    bs_spec.loader.exec_module(bs)

    projeto = tmp_path / "proj"
    projeto.mkdir()
    sessao = "sessao-mae"
    home = bs.harness_paths.ensure_state_dir(cwd=str(projeto))
    (home / "branch-sensor.json").write_text(json.dumps({"session_id": sessao, "turn": 10}), encoding="utf-8")
    balde = bs.harness_paths.ensure_state_dir(cwd=str(projeto), session_id=sessao)
    db = bs.HarnessDatabase(balde)
    task = db.start_task(
        scope_id=str(balde), legacy_level="L1-bug", tier="L1", kind="bug",
        pipeline=PIPELINE_BUG, prompt="fix",
    )
    (balde / "state.json").write_text(
        json.dumps({"task_id": task["task_id"], "scope_id": task["scope_id"]}), encoding="utf-8",
    )
    ramo = bs.add(cwd=str(projeto), name="Ramo", topic="outro assunto", parent_session=sessao)
    task = db.task(task["task_id"])
    assert task["pending_gate"] == f"branch-open:{ramo['session_id']}"

    with pytest.raises(bs.StateTransitionError) as erro:
        db.transition(task["task_id"], "tdd", expected_revision=task["revision"])
    linhas = _linhas_de_comando(str(erro.value))
    assert len(linhas) == 1, str(erro.value)
    assert "branch_state.py" in linhas[0] and "--decision park" in linhas[0]
    assert f"--slug {ramo['slug']}" in linhas[0]

    resolvida = _rodar(linhas[0], cwd=projeto)
    assert resolvida.returncode == 0, resolvida.stdout + resolvida.stderr

    task = db.task(task["task_id"])
    assert (task["status"], task["pending_gate"]) == ("active", None)
    avancada = db.transition(task["task_id"], "tdd", expected_revision=task["revision"])
    assert avancada["phase"] == "tdd"
    # "Agora nao" parkeia: o ramo continua no registro.
    assert [r["slug"] for r in bs.load(cwd=str(projeto))["branches"]] == [ramo["slug"]]


# --- Bordas: a mensagem nao promete mais do que entrega -----------------------


def test_dois_portoes_pendentes_nomeia_os_dois_e_imprime_um_comando(tmp_path: Path):
    """Cada resolucao sobe a revisao: um segundo comando com a mesma revisao falharia."""
    db = state.HarnessDatabase(tmp_path)
    task = _escalar_pelo_stop(db, _task_bug(db)["task_id"])
    db.create_branch(
        task["task_id"], branch_id="b-1", slug="ramo", name="Ramo", topic="outro assunto",
        topic_hash="h1", offered_turn=1, explicito=True,
    )
    task = db.task(task["task_id"])

    mensagem = _recusa(db, task, "tdd")

    assert "escalation" in mensagem
    assert "branch-open:b-1" in mensagem
    assert len(_linhas_de_comando(mensagem)) == 1


def test_portao_sem_resolvedor_nao_ganha_linha(tmp_path: Path):
    """`resolve_gate` recusa `answer-clarifications` fora de fase: imprimir a linha
    entregaria um comando que sai com exit 2."""
    db = state.HarnessDatabase(tmp_path)
    task = db.open_gate(_task_bug(db)["task_id"], "answer-clarifications")

    mensagem = _recusa(db, task, "tdd")

    assert "answer-clarifications" in mensagem
    assert _linhas_de_comando(mensagem) == []

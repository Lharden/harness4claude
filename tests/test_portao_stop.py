"""Portao de sessao no Stop (item 1.6 do Programa de Confiabilidade Estrutural; L-75 do master-harness).

A norma: o Stop cobra uma vez por HEAD novo, so nos tres repositorios e so por commits criados nesta sessao; quem
barra e o `mh integrar`. O instrumento quebrado nunca prende a sessao.

Repositorio git temporario e a consulta ao mh injetada (`consultar_pronto`): nenhum teste aqui chama o mh de verdade.
Cada regra tem as duas metades, e a regra "criado nesta sessao" tem a falsificacao: desligada, o caso da sessao que
so le reprova.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ["HARNESS_PLUGIN_ROOT"])


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


paths = _load("portao_paths", "scripts/harness_paths.py")
portao = _load("portao_stop_mod", "scripts/portao_stop.py")
hook = _load("portao_transactional_hook", "hooks/harness-transactional.py")

SESSAO = "sessao-portao"


def _git(raiz: Path, *args: str, data: int | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if data is not None:
        env.update(GIT_AUTHOR_DATE=f"{data} +0000", GIT_COMMITTER_DATE=f"{data} +0000")
    r = subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(raiz), *args], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
                       check=True, timeout=60)
    return r.stdout.strip()


def _commit(raiz: Path, nome: str, data: int | None = None) -> str:
    (raiz / nome).write_text(nome, encoding="utf-8")
    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-q", "-m", f"feat: {nome}", data=data)
    return _git(raiz, "rev-parse", "HEAD")


class Consulta:
    """A consulta ao mh, falsa: conta as chamadas e responde o estado pedido."""

    def __init__(self, estado: str = "ausente", repo: str | None = "hc", erro: Exception | None = None) -> None:
        self.estado, self.repo, self.erro = estado, repo, erro
        self.chamadas: list[tuple[str, str]] = []

    def __call__(self, cwd: str, sha: str) -> dict:
        self.chamadas.append((cwd, sha))
        if self.erro is not None:
            raise self.erro
        return {"repo": self.repo, "sha": sha, "estado": self.estado, "motivo": ""}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """`origin/main` num commit antigo, e o ramo `pce/x` com um commit anterior a sessao."""
    raiz = tmp_path / "r"
    raiz.mkdir()
    _git(raiz, "init", "-q", "-b", "main")
    antigo = int(time.time()) - 3600
    _commit(raiz, "base.txt", data=antigo)
    _git(raiz, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(raiz, "checkout", "-q", "-b", "pce/x")
    _commit(raiz, "antes-da-sessao.txt", data=antigo + 60)
    return raiz


def _harness(tmp_path: Path) -> Path:
    return tmp_path / "harness"


def _balde(tmp_path: Path, cwd: Path) -> Path:
    return paths.ensure_state_dir(_harness(tmp_path), cwd, session_id=SESSAO)


def _iniciar(tmp_path: Path, cwd: Path, agora: float | None = None) -> Path:
    balde = _balde(tmp_path, cwd)
    portao.registrar_inicio(balde, str(cwd), agora=time.time() - 5 if agora is None else agora)
    return balde


def _stop(tmp_path: Path, cwd: Path, consulta: Consulta, **extra) -> str:
    payload = {"hook_event_name": "Stop", "cwd": str(cwd), "session_id": SESSAO, **extra}
    return hook.handle_payload(payload, harness_root=_harness(tmp_path), consultar_pronto=consulta)


def _bloqueou(saida: str) -> bool:
    return bool(saida) and json.loads(saida)["decision"] == "block"


# ---------------------------------------------------------------------------------------------- uma vez por HEAD ----
def test_commit_novo_sem_pronto_bloqueia_uma_vez_e_o_mesmo_head_passa(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    sha = _commit(repo, "da-sessao.txt")
    consulta = Consulta("ausente")
    saida = _stop(tmp_path, repo, consulta)
    assert _bloqueou(saida)
    motivo = json.loads(saida)["reason"]
    assert "mh pronto" in motivo and "--pendente" in motivo and sha[:10] in motivo and "1 commit" in motivo
    assert consulta.chamadas == [(str(repo), sha)]
    # o turno seguinte com o mesmo HEAD passa, mesmo sem pronto, e nem pergunta ao mh
    assert _stop(tmp_path, repo, consulta) == ""
    assert len(consulta.chamadas) == 1


def test_novo_commit_bloqueia_de_novo(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "um.txt")
    consulta = Consulta("ausente")
    assert _bloqueou(_stop(tmp_path, repo, consulta))
    assert _stop(tmp_path, repo, consulta) == ""
    sha2 = _commit(repo, "dois.txt")
    saida = _stop(tmp_path, repo, consulta)
    assert _bloqueou(saida) and sha2[:10] in json.loads(saida)["reason"] and "2 commit" in json.loads(saida)["reason"]


def test_pronto_vermelho_tambem_cobra(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "x.txt")
    saida = _stop(tmp_path, repo, Consulta("vermelho"))
    assert _bloqueou(saida) and "vermelho" in json.loads(saida)["reason"]


@pytest.mark.parametrize("estado", ["verde", "pendente"])
def test_com_pronto_verde_ou_pendente_passa(repo: Path, tmp_path: Path, estado: str) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "x.txt")
    consulta = Consulta(estado)
    assert _stop(tmp_path, repo, consulta) == ""
    assert len(consulta.chamadas) == 1
    assert _stop(tmp_path, repo, consulta) == "" and len(consulta.chamadas) == 1   # decidido: nao pergunta de novo


# --------------------------------------------------------------------------------------------------- fora do Stop ----
def test_cwd_fora_de_um_repositorio_passa_sem_chamar_o_mh_nem_o_git(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    fora = tmp_path / "fora"
    fora.mkdir()
    _iniciar(tmp_path, fora)
    consulta = Consulta("ausente")
    assert _stop(tmp_path, fora, consulta) == "" and consulta.chamadas == []

    chamadas_git: list = []
    monkeypatch.setattr(portao, "_git", lambda *a, **k: chamadas_git.append(a))   # espia: avaliar engole excecao
    assert portao.avaliar(_balde(tmp_path, fora), str(fora), consultar=consulta) is None
    assert chamadas_git == [] and consulta.chamadas == []


def test_repositorio_fora_dos_tres_pergunta_uma_vez_e_depois_nao_chama_o_mh(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "a.txt")
    consulta = Consulta("fora_do_escopo", repo=None)
    assert _stop(tmp_path, repo, consulta) == "" and len(consulta.chamadas) == 1
    _commit(repo, "b.txt")
    assert _stop(tmp_path, repo, consulta) == "" and len(consulta.chamadas) == 1   # escopo do diretorio comum guardado


def test_sessao_sem_commit_passa_sem_git_nem_mh(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iniciar(tmp_path, repo)
    consulta = Consulta("ausente")
    chamadas_git: list = []
    monkeypatch.setattr(portao, "_git", lambda *a, **k: chamadas_git.append(a))   # espia: avaliar engole excecao
    assert portao.avaliar(_balde(tmp_path, repo), str(repo), consultar=consulta) is None
    assert chamadas_git == [] and consulta.chamadas == []


def test_main_passa(repo: Path, tmp_path: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    _iniciar(tmp_path, repo)
    _commit(repo, "no-main.txt")
    consulta = Consulta("ausente")
    assert _stop(tmp_path, repo, consulta) == "" and consulta.chamadas == []


def test_sessao_sem_inicio_registrado_nunca_cobra(repo: Path, tmp_path: Path) -> None:
    _commit(repo, "x.txt")                     # sessao aberta antes do portao existir: sem SessionStart registrado
    consulta = Consulta("ausente")
    assert _stop(tmp_path, repo, consulta) == "" and consulta.chamadas == []


# ------------------------------------------------------------------------------------------- instrumento quebrado ----
def test_consulta_que_lanca_passa_e_registra_nao_verificado(repo: Path, tmp_path: Path) -> None:
    balde = _iniciar(tmp_path, repo)
    sha = _commit(repo, "x.txt")
    consulta = Consulta(erro=portao.ConsultaFalhou("mh ausente"))
    assert _stop(tmp_path, repo, consulta) == ""
    dados = json.loads((balde / portao.ARQUIVO).read_text(encoding="utf-8"))
    assert dados["heads"][sha]["decisao"] == "nao_verificado"
    assert dados["nao_verificado"][-1]["sha"] == sha and "mh ausente" in dados["nao_verificado"][-1]["motivo"]


def test_excecao_qualquer_na_consulta_tambem_passa(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "x.txt")
    assert _stop(tmp_path, repo, Consulta(erro=RuntimeError("boom"))) == ""


def test_consulta_que_responde_nao_verificado_passa(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "x.txt")
    assert _stop(tmp_path, repo, Consulta("nao_verificado")) == ""


def test_consulta_real_sem_marcador_do_mh_e_falha_de_consulta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_HARNESS_HOME", str(tmp_path / "casa-vazia"))
    with pytest.raises(portao.ConsultaFalhou):
        portao._consultar_mh(str(tmp_path), "a" * 40)


# ---------------------------------------------------------------------------------------- criado nesta sessao ----
def test_commit_anterior_ao_inicio_da_sessao_nao_conta(repo: Path, tmp_path: Path) -> None:
    """A sessao que so le, num ramo que ja tinha commit a frente de origin/main: nao cobra."""
    _iniciar(tmp_path, repo)
    consulta = Consulta("ausente")
    assert _stop(tmp_path, repo, consulta) == "" and consulta.chamadas == []


def test_trocar_para_worktree_com_commits_de_outra_sessao_nao_conta(repo: Path, tmp_path: Path) -> None:
    """A sessao comeca no main e vai para um worktree cujo ramo traz commits de antes dela."""
    principal = tmp_path / "principal"
    _git(repo, "worktree", "add", "-q", str(principal), "main")
    _iniciar(tmp_path, principal)
    consulta = Consulta("ausente")
    assert _stop(tmp_path, repo, consulta) == "" and consulta.chamadas == []     # pce/x so tem commit antigo
    sha = _commit(repo, "agora.txt")
    saida = _stop(tmp_path, repo, consulta)
    assert _bloqueou(saida) and "1 commit" in json.loads(saida)["reason"] and consulta.chamadas[-1][1] == sha


def test_falsificacao_sem_a_checagem_de_sessao_a_sessao_que_so_le_reprova(repo: Path, tmp_path: Path) -> None:
    """Desligada a regra "criado nesta sessao", o caso de `test_commit_anterior_ao_inicio_da_sessao_nao_conta`
    passaria a cobrar: o guarda pega o defeito que existe para pegar."""
    balde = _iniciar(tmp_path, repo)
    consulta = Consulta("ausente")
    assert portao.avaliar(balde, str(repo), consultar=consulta, checar_sessao=True) is None
    motivo = portao.avaliar(balde, str(repo), consultar=consulta, checar_sessao=False)
    assert motivo is not None and "mh pronto" in motivo


# ---------------------------------------------------------------------------------------- junto do gate e inicio ----
def test_inicio_nao_e_regravado_no_resume(repo: Path, tmp_path: Path) -> None:
    balde = _iniciar(tmp_path, repo, agora=1000.0)
    _commit(repo, "x.txt")
    portao.registrar_inicio(balde, str(repo), agora=2000.0)       # SessionStart de resume/compact
    dados = json.loads((balde / portao.ARQUIVO).read_text(encoding="utf-8"))
    assert dados["inicio"]["ts"] == 1000.0


def test_head_rapido_concorda_com_o_git(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "pce/y", str(wt), "main")
    _git(repo, "pack-refs", "--all")                               # refs so no packed-refs
    for lugar in (repo, wt):
        rapido = portao._head_rapido(str(lugar))
        assert rapido is not None
        assert rapido["sha"] == _git(lugar, "rev-parse", "HEAD")
        assert rapido["ramo"] == _git(lugar, "rev-parse", "--abbrev-ref", "HEAD")
        comum = Path(_git(lugar, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        assert Path(rapido["comum"]) == comum


def test_bloqueio_do_portao_se_junta_ao_do_gate(repo: Path, tmp_path: Path) -> None:
    assert json.loads(hook._com_portao(json.dumps({"decision": "block", "reason": "gate"}), "portao"))["reason"] == (
        "gate\n\nportao")
    assert hook._com_portao("", None) == ""
    assert json.loads(hook._com_portao("", "portao")) == {"decision": "block", "reason": "portao"}


def test_portao_cobra_mesmo_com_stop_hook_active(repo: Path, tmp_path: Path) -> None:
    _iniciar(tmp_path, repo)
    _commit(repo, "x.txt")
    assert _bloqueou(_stop(tmp_path, repo, Consulta("ausente"), stop_hook_active=True))


def test_session_start_grava_o_head_de_inicio(repo: Path, tmp_path: Path) -> None:
    hook_sh = ROOT / "hooks" / "harness-session-start.sh"
    home = tmp_path / "home"
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(ROOT),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "HARNESS_DIR": str(_harness(tmp_path)),
        "HARNESS_SKIP_DEPCHECK": "1",
        "MASTER_HARNESS_HOME": str(tmp_path / "casa"),
    }
    for chave in ("AI_BRAIN_PATH", "VAULT_PATH", "PYTHONPATH"):
        env.pop(chave, None)
    r = subprocess.run(
        ["bash", str(hook_sh)],
        input=json.dumps({"session_id": SESSAO, "cwd": str(repo), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        check=False, env=env, cwd=str(repo), timeout=120,
    )
    assert r.returncode == 0, r.stderr[-500:]
    dados = json.loads((_balde(tmp_path, repo) / portao.ARQUIVO).read_text(encoding="utf-8"))
    assert dados["inicio"]["sha"] == _git(repo, "rev-parse", "HEAD")

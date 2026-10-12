#!/usr/bin/env python
"""Portao de sessao no Stop (item 1.6 do Programa de Confiabilidade Estrutural, decisao L-75 do master-harness).

## A norma: o Stop cobra, o integrar barra

O Stop bloqueia UMA vez por HEAD novo quando valem as tres condicoes ao mesmo tempo:

- o cwd e um checkout ou worktree de um dos tres repositorios (MH, HC, HX);
- ha commits a frente de `origin/main` criados nesta sessao;
- nao existe pronto verde ou pendente para esse HEAD.

A mensagem manda rodar `mh pronto` ou `mh pronto --pendente "<motivo>"`. O Stop seguinte com o mesmo HEAD passa, com
ou sem pronto: e uma cobranca so. Quem barra de verdade e o `mh integrar`. Ficam fora: o `main` (o orquestrador), o
cwd fora dos tres repositorios e as sessoes do Codex (que nao rodam os hooks deste plugin).

O instrumento quebrado nunca prende a sessao. Se a consulta ao mh falha (interpretador ausente, excecao, prazo), o
Stop passa, registra `nao_verificado` aqui e, quando o mh chegou a rodar, a propria consulta ja gravou
`nao_verificado` na vigia `portao-stop` do mh.

## Sem regra duplicada

Qual repositorio e o cwd (o diretorio comum do git contra a topologia) e onde mora o registro do pronto sao
perguntas do mh: o hook pergunta `<interpretador> -I -m mh pronto --consultar --sha <sha>` e le o JSON. `-I` faz o
`mh` vir do pacote instalado (o checkout principal), nunca do cwd: "a versao do pronto que valida e a do checkout
principal" (grill, "Portao de sessao").

## Latencia: o barato primeiro

- SessionStart: grava o HEAD de inicio lendo `.git` direto (sem processo); so cai no `git` se a leitura nao der.
- Stop sem registro de inicio, ou fora de um repositorio git: so leitura de arquivo.
- Stop com o HEAD igual ao de inicio, ou a um HEAD ja decidido: so leitura de arquivo (o HEAD sai do `.git`).
- HEAD novo: um `git log` para contar os commits da sessao; o mh so e chamado quando ha commit da sessao, e uma
  vez por HEAD (a decisao fica em `heads`). O escopo de um diretorio comum fica em `escopo`: um repositorio fora dos
  tres pergunta ao mh uma vez por sessao.

## Estado

`<balde da sessao>/portao-stop.json`, ao lado do `harness.db` e do `state.json` (nao toca no `state.json`, que o
classify le). Arquivo por sessao: o balde e da sessao, entao nao ha escritor concorrente.

## "Criado nesta sessao"

A partir do HEAD de inicio (`^inicio`), e com data de commit nao anterior ao inicio da sessao. A data cobre a sessao
que troca de worktree no meio: o ramo de outro worktree pode ter commits de outras sessoes, que o `^inicio` (de outro
repositorio ou de outro ramo) nao exclui.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

ARQUIVO = "portao-stop.json"
#: Prazo da consulta ao mh. O Stop do `harness-transactional.py` tem 15 s no hooks.json; a consulta mede ~1 s ociosa.
PRAZO_CONSULTA_S = 8.0
PRAZO_GIT_S = 5.0
#: Quantos registros de `nao_verificado` ficam no arquivo (os mais recentes).
MAX_NAO_VERIFICADO = 20

COBRADO = "cobrado"
NAO_VERIFICADO = "nao_verificado"
FORA_DO_ESCOPO = "fora_do_escopo"
#: Estados do pronto que liberam o HEAD sem cobranca.
LIBERAM = ("verde", "pendente")
#: Estados do pronto que levam a cobranca (nao existe pronto verde nem pendente).
COBRAM = ("ausente", "vermelho")

_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")

#: (cwd, sha) -> o JSON da consulta (`repo`, `estado`, `motivo`). Levanta quando nao conseguiu perguntar.
Consulta = Callable[[str, str], dict]


class ConsultaFalhou(RuntimeError):
    """A consulta ao mh nao respondeu (mh ausente, saida estranha, prazo)."""


# ------------------------------------------------------------------------------------------------------- estado ----
def _ler(balde: Path) -> dict[str, Any]:
    try:
        dados = json.loads((Path(balde) / ARQUIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar(balde: Path, dados: dict[str, Any]) -> None:
    from projecao import gravar_json_atomico  # type: ignore[import-not-found]

    try:
        Path(balde).mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    gravar_json_atomico(Path(balde) / ARQUIVO, dados)


# ---------------------------------------------------------------------------------------------------------- git ----
def _git_dir(cwd: str) -> tuple[Path, Path] | None:
    """(gitdir, diretorio comum) do repositorio que contem `cwd`, lendo `.git` direto. `None` fora de um repositorio."""
    try:
        atual = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return None
    for d in (atual, *atual.parents):
        marca = d / ".git"
        try:
            if marca.is_dir():
                return marca, marca
            if marca.is_file():
                texto = marca.read_text(encoding="utf-8").strip()
                if not texto.startswith("gitdir:"):
                    return None
                gitdir = Path(texto[len("gitdir:"):].strip())
                gitdir = gitdir if gitdir.is_absolute() else (d / gitdir)
                comum = gitdir
                arq_comum = gitdir / "commondir"
                if arq_comum.is_file():
                    rel = Path(arq_comum.read_text(encoding="utf-8").strip())
                    comum = rel if rel.is_absolute() else gitdir / rel
                return gitdir.resolve(), comum.resolve()
        except OSError:
            return None
    return None


def _ref_em_arquivo(comum: Path, gitdir: Path, ref: str) -> str | None:
    for base in (gitdir, comum):
        try:
            valor = (base / ref).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _SHA.fullmatch(valor):
            return valor
    try:
        for linha in (comum / "packed-refs").read_text(encoding="utf-8").splitlines():
            partes = linha.split(" ", 1)
            if len(partes) == 2 and partes[1].strip() == ref and _SHA.fullmatch(partes[0]):
                return partes[0]
    except OSError:
        pass
    return None


def _head_rapido(cwd: str) -> dict[str, Any] | None:
    """`{"sha", "ramo", "comum"}` lidos dos arquivos do `.git`, sem processo. `None` se nao deu (cai no git)."""
    achado = _git_dir(cwd)
    if achado is None:
        return None
    gitdir, comum = achado
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        sha = _ref_em_arquivo(comum, gitdir, ref)
        if sha is None:
            return None
        ramo = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        return {"sha": sha, "ramo": ramo, "comum": str(comum)}
    if _SHA.fullmatch(head):
        return {"sha": head, "ramo": None, "comum": str(comum)}
    return None


def _env_git() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_") or k.upper() == "GIT_EXEC_PATH"}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=_env_git(),
                           timeout=PRAZO_GIT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _head(cwd: str) -> dict[str, Any] | None:
    rapido = _head_rapido(cwd)
    if rapido is not None:
        return rapido
    if _git_dir(cwd) is None:
        return None  # nenhum `.git` do cwd ate a raiz: fora de repositorio, sem pagar um processo do git
    saida = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", "HEAD", "--abbrev-ref", "HEAD")
    if saida is None:
        return None
    linhas = [ln.strip() for ln in saida.splitlines() if ln.strip()]
    if len(linhas) != 3 or not _SHA.fullmatch(linhas[1]):
        return None
    ramo = None if linhas[2] == "HEAD" else linhas[2]
    return {"sha": linhas[1], "ramo": ramo, "comum": str(Path(linhas[0]).resolve())}


def _commits_da_sessao(cwd: str, sha: str, inicio: dict[str, Any]) -> list[str] | None:
    """Os commits de `origin/main..sha` criados nesta sessao. `None` se o git nao respondeu ou nao ha `origin/main`."""
    saida = None
    sha_inicio = inicio.get("sha")
    if isinstance(sha_inicio, str) and _SHA.fullmatch(sha_inicio):
        saida = _git(cwd, "log", "--format=%H %ct", sha, "^origin/main", f"^{sha_inicio}", "--")
    if saida is None:  # sem inicio, ou o inicio e de outro repositorio (o git recusa o `^` desconhecido)
        saida = _git(cwd, "log", "--format=%H %ct", sha, "^origin/main", "--")
    if saida is None:
        return None  # git sem resposta, ou sem origin/main
    corte = int(float(inicio.get("ts") or 0))
    novos = []
    for linha in saida.splitlines():
        partes = linha.split()
        if len(partes) == 2 and partes[1].isdigit() and int(partes[1]) >= corte:
            novos.append(partes[0])
    return novos


# ------------------------------------------------------------------------------------------------------ consulta ----
def _interpretador_do_mh() -> str | None:
    casa = os.environ.get("MASTER_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".master-harness")
    try:
        with open(os.path.join(casa, "interpretador"), encoding="utf-8-sig") as fh:
            candidato = fh.readline().strip()
    except OSError:
        return None
    return candidato if candidato and os.path.isfile(candidato) else None


def _consultar_mh(cwd: str, sha: str) -> dict:
    """A consulta real: `<interpretador> -I -m mh pronto --consultar --sha <sha>` no cwd da sessao."""
    py = _interpretador_do_mh()
    if py is None:
        raise ConsultaFalhou("mh ausente: sem o marcador ~/.master-harness/interpretador")
    try:
        r = subprocess.run([py, "-I", "-m", "mh", "pronto", "--consultar", "--sha", sha], cwd=cwd,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace", env=_env_git(), timeout=PRAZO_CONSULTA_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ConsultaFalhou(f"a consulta ao mh passou de {PRAZO_CONSULTA_S:g} s") from exc
    except OSError as exc:
        raise ConsultaFalhou(f"a consulta ao mh nao abriu: {exc}") from exc
    linhas = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        resp = json.loads(linhas[-1]) if linhas else None
    except ValueError:
        resp = None
    if r.returncode not in (0, 2) or not isinstance(resp, dict) or "estado" not in resp:
        cauda = (r.stderr or r.stdout or "").strip()[-200:]
        raise ConsultaFalhou(f"a consulta ao mh saiu {r.returncode}: {cauda}")
    return resp


# -------------------------------------------------------------------------------------------------------- inicio ----
def registrar_inicio(balde: str | Path, cwd: str | None, *, agora: float | None = None) -> None:
    """SessionStart: grava o HEAD e o instante de inicio da sessao, se ainda nao ha. Nunca levanta, nunca imprime.

    So a primeira vez: o SessionStart de `resume` e de `compact` traz a mesma sessao, e regravar o inicio esqueceria
    os commits feitos antes dele.
    """
    try:
        dados = _ler(Path(balde))
        if isinstance(dados.get("inicio"), dict):
            return
        cabeca = _head(cwd) if cwd else None
        dados["versao"] = 1
        dados["inicio"] = {
            "ts": time.time() if agora is None else agora,
            "sha": cabeca["sha"] if cabeca else None,
            "comum": cabeca["comum"] if cabeca else None,
            "cwd": cwd,
        }
        _gravar(Path(balde), dados)
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------------------- Stop ----
def _decidir(dados: dict[str, Any], sha: str, decisao: str, **extra: Any) -> None:
    dados.setdefault("heads", {})[sha] = {"decisao": decisao, "ts": time.time(), **extra}


def _nao_verificado(dados: dict[str, Any], sha: str, motivo: str) -> None:
    lista = dados.setdefault("nao_verificado", [])
    lista.append({"ts": time.time(), "sha": sha, "motivo": motivo[:300]})
    del lista[:-MAX_NAO_VERIFICADO]
    _decidir(dados, sha, NAO_VERIFICADO, motivo=motivo[:300])


def _mensagem(ramo: str | None, sha: str, commits: int, estado: str, cwd: str) -> str:
    qual = "o pronto deste HEAD esta vermelho" if estado == "vermelho" else "nao ha `mh pronto` para este HEAD"
    return (
        f"HARNESS v3 portao de sessao (item 1.6): {commits} commit(s) desta sessao a frente de origin/main em "
        f"{ramo or 'HEAD destacado'} ({sha[:10]}), e {qual}. Antes de dizer que terminou, rode no worktree "
        f"({cwd}): `mh pronto`; ou, se o pronto fica para depois, `mh pronto --pendente \"<motivo>\"`, que fica "
        f"visivel ao integrador. Esta cobranca e uma so: o proximo Stop com o mesmo HEAD passa. Quem barra e o "
        f"`mh integrar`."
    )


def avaliar(balde: str | Path, cwd: str | None, *, consultar: Consulta | None = None,
            checar_sessao: bool = True) -> str | None:
    """O motivo do bloqueio, ou `None` para deixar o Stop passar. Nunca levanta.

    `consultar` so para teste (o padrao pergunta ao mh). `checar_sessao=False` desliga a regra "criado nesta
    sessao": so a falsificacao a usa.
    """
    try:
        return _avaliar(Path(balde), cwd, consultar or _consultar_mh, checar_sessao)
    except Exception:
        return None


def _avaliar(balde: Path, cwd: str | None, consultar: Consulta, checar_sessao: bool) -> str | None:
    dados = _ler(balde)
    inicio = dados.get("inicio")
    if not cwd or not isinstance(inicio, dict):
        return None  # sessao sem SessionStart registrado (anterior ao portao): nunca cobra retroativo
    cabeca = _head(cwd)
    if cabeca is None:
        return None  # fora de um repositorio git
    sha, ramo, comum = cabeca["sha"], cabeca["ramo"], cabeca["comum"]
    if checar_sessao and sha == inicio.get("sha"):
        return None
    if sha in dados.get("heads", {}):
        return None  # ja decidido: cobrado uma vez, liberado, ou fora
    if ramo == "main":
        _decidir(dados, sha, "main")
        _gravar(balde, dados)
        return None
    if dados.get("escopo", {}).get(comum, "?") is None:
        _decidir(dados, sha, FORA_DO_ESCOPO)
        _gravar(balde, dados)
        return None
    commits = _commits_da_sessao(cwd, sha, inicio if checar_sessao else {})
    if commits is None:
        _nao_verificado(dados, sha, "git sem resposta ou sem origin/main")
        _gravar(balde, dados)
        return None
    if not commits:
        _decidir(dados, sha, "sem_commits_da_sessao")
        _gravar(balde, dados)
        return None
    try:
        resp = consultar(cwd, sha)
    except Exception as exc:  # o instrumento quebrado nunca prende a sessao
        _nao_verificado(dados, sha, f"{type(exc).__name__}: {exc}")
        _gravar(balde, dados)
        return None
    estado = str(resp.get("estado"))
    if estado == FORA_DO_ESCOPO or (resp.get("repo") is None and estado != NAO_VERIFICADO):
        dados.setdefault("escopo", {})[comum] = None
        _decidir(dados, sha, FORA_DO_ESCOPO)
        _gravar(balde, dados)
        return None
    if resp.get("repo"):
        dados.setdefault("escopo", {})[comum] = resp["repo"]
    if estado in LIBERAM:
        _decidir(dados, sha, estado)
        _gravar(balde, dados)
        return None
    if estado in COBRAM:
        _decidir(dados, sha, COBRADO, estado=estado, commits=len(commits))
        _gravar(balde, dados)
        return _mensagem(ramo, sha, len(commits), estado, cwd)
    _nao_verificado(dados, sha, f"consulta respondeu {estado}: {resp.get('motivo') or ''}".strip())
    _gravar(balde, dados)
    return None

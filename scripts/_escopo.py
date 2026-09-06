# DERIVADO - nao edite este arquivo.
#
# A fonte e `master-harness/mh/escopo.py`, e ela e a unica
# dona da identidade de escopo. Este arquivo e uma copia entregue ao host
# `claude` porque os hooks nao podem depender do pacote `mh` estar importavel.
#
# Para mudar o que esta aqui: edite a fonte e rode `mh identidade semear`.
# Para saber se derivou: `mh identidade check` (sai 1 se divergir).
#
# ---- fim do cabecalho vendorizado; daqui para baixo e copia exata ----
"""O que e um escopo — e por que a especie precisa estar no proprio valor.

## A pergunta que o revert de 2026-09-05 deixou aberta

A tentativa `sem-repositorio` fez diretorio sem repositorio parar de cunhar
projeto: todos caiam num balde comum. Foi revertida porque quebrou o
isolamento — dois diretorios sem relacao passaram a compartilhar estado, e uma
branch registrada num aparecia no outro. Exatamente a contaminacao cruzada que
o incidente de 2026-07-28 motivou impedir.

O erro nao foi o diagnostico. Era verdade que 45 baldes existiam para muito
menos projetos reais. O erro foi a resposta: **juntar** o que so precisava ser
**rotulado**.

## A resposta

Um escopo tem **especie**, e a especie fica no proprio valor:

| especie | quando | exemplo |
|---|---|---|
| `repo` | ha repositorio; worktrees do mesmo repo colapsam no dono | `repo:harness4claude-adfb74ad` |
| `dir` | diretorio sem repositorio — isolado, mas marcado | `dir:Temp-abc-1e4d90aa` |
| `global` | `HARNESS_SCOPE=global` | `global:maquina` |

Hoje `harness4claude-adfb74ad` e `Temp-abc-1e4d90aa` tem a mesma **forma**. Nada
no valor diz que um e um projeto de verdade e o outro um diretorio temporario, e
por isso o `mh gc` teve de **adivinhar** a diferenca pelo peso do balde. Com a
especie no valor, a politica para de adivinhar: `dir` e recolhivel, `repo` nao.

E o mesmo desenho que a topologia-alvo ja exige para sessao (`claude:<uuid>`,
`codex:s-<hash>`, "nunca ambos no mesmo campo sem marca"). Escopo tinha ficado
de fora.

## O id nu e byte-identico ao de hoje

`Escopo.id` — a parte depois dos dois-pontos — e o mesmo texto que
`harness4claude/scripts/harness_paths.py::project_slug` produz. Isso nao e
coincidencia: e o que torna a escada `identidade` comparavel. No degrau `dupla`,
`divergencia()` roda os dois e reprova se discordarem, entao deriva de um lado
vira teste vermelho em vez de balde orfao.

Os dois-pontos so existem no **valor**, que vai para uma coluna do `state.db`.
Em caminho de disco ele seria ilegal no Windows — e por isso o degrau `cunhada`
guarda `valor` como chave, nao como nome de diretorio.
"""

from __future__ import annotations

import hashlib
import os
import re

__all__ = [
    "ESPECIES",
    "Escopo",
    "HOSTS",
    "HostDesconhecido",
    "de_caminho",
    "de_sessao",
    "divergencia",
    "raiz_do_repo",
]

#: A especie e o discriminante. Ordem = do mais duravel ao mais efemero.
ESPECIES = ("repo", "dir", "global")

#: Hosts que cunham sessao. Lista fechada: host novo sem prefixo proprio voltaria
#: a por dois espacos de id no mesmo campo, que e o defeito que a marca resolve.
HOSTS = ("claude", "codex")


class HostDesconhecido(ValueError):
    """Sessao de host fora de `HOSTS`. Falha alto — prefixo errado e silencioso."""


def _limpo(bruto: str | os.PathLike | None) -> str:
    r"""Tira espaco e controle das pontas.

    Mesma razao do `_clean` de `harness_paths`: `print()` no Windows emite
    `\r\n`, o hook fatia por `\n`, e sobra um `\r` grudado. Caminho com `\r`
    nao existe, e a resolucao caia no cwd cru — a raiz de um repo e um
    subdiretorio dele geravam ids DIFERENTES.
    """
    return str(bruto).strip().strip("\r\n\t ") if bruto else ""


def _dono_do_worktree(dir_com_git: str) -> str | None:
    """Num worktree, `.git` e arquivo: `gitdir: <repo>/.git/worktrees/<nome>`.

    Ler um arquivo e mais barato que abrir um processo, e por isso a deteccao
    cabe aqui sem violar a regra de nao chamar `git` (ver `raiz_do_repo`).

    **Submodulo nao colapsa.** Ele tambem tem `.git` como arquivo, mas aponta
    para `<pai>/.git/modules/<nome>`, e submodulo e outro repositorio de verdade.
    Colapsa-lo no pai misturaria dois projetos — o oposto do que se quer.

    Devolve None em qualquer duvida. Quem chama cai no diretorio, que e o
    comportamento anterior.
    """
    marcador = os.path.join(dir_com_git, ".git")
    try:
        if not os.path.isfile(marcador):
            return None
        with open(marcador, encoding="utf-8", errors="replace") as fh:
            linha = fh.readline(4096).strip()
    except OSError:
        return None
    if not linha.startswith("gitdir:"):
        return None
    alvo = linha[len("gitdir:") :].strip()
    if not alvo:
        return None
    try:
        if not os.path.isabs(alvo):
            alvo = os.path.join(dir_com_git, alvo)
        alvo = os.path.normpath(alvo)
    except (OSError, ValueError):
        return None
    partes = alvo.replace("\\", "/").rstrip("/").split("/")
    if len(partes) < 3 or partes[-2] != "worktrees":
        return None
    repo = os.path.dirname(os.path.dirname(os.path.dirname(alvo)))
    return repo if os.path.isdir(repo) else None


def _canonico(caminho: str) -> str:
    """Resolve junction e symlink, para que dois caminhos nao virem dois escopos.

    Sem isto, o MESMO repositorio alcancado por uma junction produz dois escopos
    — reproduzido nesta maquina:

        real: repo:real-49a2172a
        link: repo:link-43b54d04

    Duas sessoes "no mesmo projeto" nao se veriam, e o estado fragmentaria. E a
    classe do incidente de 2026-07-28, chegando por outra porta.

    **Custa 94,5 us contra 0,5 do `abspath`** — 190x mais, medido. Entra assim
    mesmo porque e UMA chamada por resolucao (na raiz achada, e nao a cada passo
    da subida), o que da 0,2% do corpo python do hook.

    E foi medido que nao renomeia nada: em 22 diretorios reais desta maquina,
    ZERO slugs mudariam. Se algum mudasse, aplicar isto fragmentaria os baldes
    existentes — que e exatamente o defeito que ele conserta.

    Degrada para o proprio caminho em qualquer erro: identidade pior e melhor
    que hook morto.
    """
    try:
        return os.path.realpath(caminho)
    except (OSError, ValueError):
        return caminho


def raiz_do_repo(inicio: str | os.PathLike | None) -> str | None:
    """Sobe a arvore procurando `.git`. None se nao houver repositorio.

    **Sem subprocess, deliberadamente.** Isto roda em todo `UserPromptSubmit`, e
    um `git rev-parse` por prompt custaria mais que a resolucao inteira. A
    topologia-alvo prescrevia `rev-parse --git-common-dir` e foi corrigida em
    2026-09-05: o autor do codigo original ja tinha medido e recusado esse custo.
    """
    inicio = _limpo(inicio)
    if not inicio:
        return None
    try:
        p = os.path.abspath(inicio)
    except (OSError, ValueError):
        return None
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return _canonico(_dono_do_worktree(p) or p)
        pai = os.path.dirname(p)
        if pai == p:
            return None
        p = pai


def _slug(raiz: str) -> str:
    """`<basename>-<hash8>`, byte-identico ao `project_slug` do harness4claude.

    O basename sozinho colidiria entre dois checkouts de mesmo nome; o hash
    sozinho seria ilegivel ao inspecionar o disco. `normcase` porque no Windows
    o mesmo diretorio aparece com caixas diferentes conforme quem chama.
    """
    base = os.path.basename(raiz.rstrip("/\\")) or "root"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "root"
    digest = hashlib.sha256(os.path.normcase(raiz).encode("utf-8")).hexdigest()[:8]
    return f"{base[:40]}-{digest}"


class Escopo:
    """Um escopo cunhado: especie, id e o caminho que o originou.

    **Sem `dataclass`, e a razao e medida.** `python -X importtime` nesta
    maquina, 2026-09-06:

        mh.escopo (com dataclass)   17,02 ms cumulativo
        dataclasses                  8,99 ms
        harness_paths                6,50 ms

    Metade do custo do modulo era o `dataclasses`, e este modulo passa a ser
    **vendorizado no caminho quente dos dois hosts** — um hook por prompt. Nove
    milissegundos por prompt para nao escrever quatro dunders e um preco que
    ninguem escolheria sabendo dele.

    (Cronometrar `subprocess` em volta do interpretador nao mostra isso: o ruido
    desta maquina vai de 102 a 159 ms e engole a diferenca inteira. Duas
    medicoes assim se contradisseram antes de o `-X importtime` decidir.)

    Imutavel na pratica: `__slots__` sem setter publico, `__eq__` e `__hash__`
    por valor.
    """

    __slots__ = ("especie", "id", "raiz")

    def __init__(self, especie: str, id: str, raiz: str = "") -> None:
        if especie not in ESPECIES:
            raise ValueError(f"especie {especie!r} nao existe")
        object.__setattr__(self, "especie", especie)
        #: Id nu, sem especie. Byte-identico ao `project_slug` de hoje — e o que
        #: torna a escada `identidade` comparavel.
        object.__setattr__(self, "id", id)
        #: O caminho que originou. Vazio para `global`, que nao tem um.
        object.__setattr__(self, "raiz", raiz)

    def __setattr__(self, nome: str, valor: object) -> None:
        raise AttributeError(f"Escopo e imutavel: nao da para atribuir {nome!r}")

    def __eq__(self, outro: object) -> bool:
        if not isinstance(outro, Escopo):
            return NotImplemented
        return (self.especie, self.id, self.raiz) == (outro.especie, outro.id, outro.raiz)

    def __hash__(self) -> int:
        return hash((self.especie, self.id, self.raiz))

    def __repr__(self) -> str:
        return f"Escopo(especie={self.especie!r}, id={self.id!r}, raiz={self.raiz!r})"

    @property
    def valor(self) -> str:
        """A chave que vai para a coluna `scope_id` do `state.db`."""
        return f"{self.especie}:{self.id}"

    @property
    def coletavel(self) -> bool:
        """O `mh gc` pode recolher este escopo?

        Aqui a politica para de adivinhar. O GC de 2026-09-05 inferia
        "descartavel" do peso do balde — criterio que acerta por correlacao e
        erra em silencio quando um projeto real passa uma semana sem sessao.
        `dir` e recolhivel porque diretorio sem repositorio nao tem historia a
        preservar; `repo` nao e, por vazio que esteja hoje.
        """
        return self.especie == "dir"


def de_caminho(cwd: str | os.PathLike | None, *, escopo_env: str | None = None) -> Escopo:
    """Cunha o escopo de um diretorio de trabalho.

    `escopo_env` sobrepoe: e o `HARNESS_SCOPE` do harness4claude, que restaura o
    state unico da maquina por opt-in.
    """
    valor = escopo_env if escopo_env is not None else os.environ.get("HARNESS_SCOPE", "")
    if valor.strip().lower() == "global":
        return Escopo(especie="global", id="maquina")
    limpo = _limpo(cwd)
    raiz = raiz_do_repo(limpo)
    if raiz:
        return Escopo(especie="repo", id=_slug(raiz), raiz=raiz)
    if not limpo:
        # Sem cwd nao ha o que isolar, e inventar um id seria pior: dois
        # chamadores sem cwd cairiam no mesmo balde sem que ninguem tenha
        # decidido isso. `unknown` e o mesmo texto que o harness4claude usa.
        return Escopo(especie="dir", id="unknown")
    # Diretorio sem repositorio tambem canoniza: dois caminhos para a mesma
    # pasta sao a mesma pasta, com `.git` ou sem.
    raiz = _canonico(os.path.abspath(limpo))
    return Escopo(especie="dir", id=_slug(raiz), raiz=raiz)


def de_sessao(host: str, session_id: str | None) -> str | None:
    """`claude:<uuid>` ou `codex:s-<hash>`. None quando nao ha sessao.

    O prefixo existe porque os dois hosts geram id com formas diferentes e ja
    escreveram no mesmo campo. Sem marca, `s-4f2a` e um uuid truncado sao
    indistinguiveis depois.
    """
    if host not in HOSTS:
        raise HostDesconhecido(host)
    limpo = _limpo(session_id)
    if not limpo:
        return None
    legivel = re.sub(r"[^A-Za-z0-9._-]+", "-", limpo).strip("-._") or "session"
    digest = hashlib.sha256(limpo.encode("utf-8")).hexdigest()[:8]
    return f"{host}:{legivel[:40]}-{digest}"


def divergencia(cwd: str | os.PathLike | None, project_slug) -> str | None:
    """O portao do degrau `dupla`: os dois cunhadores concordam?

    `project_slug` e a funcao do harness4claude, injetada em vez de importada —
    o `mh` nao pode depender do harness que ele vai substituir, e injetar deixa
    o teste escolher a versao real em disco.

    Devolve None quando concordam, ou a descricao da diferenca. `global` nao tem
    equivalente do outro lado e nunca diverge.
    """
    e = de_caminho(cwd)
    if e.especie == "global":
        return None
    deles = project_slug(cwd)
    if deles == e.id:
        return None
    return f"{cwd}: mh cunhou {e.id!r}, harness4claude cunhou {deles!r}"

#!/usr/bin/env python
"""Resolucao de paths do harness: raiz global vs bucket por projeto.

O problema (auditoria 2026-07-28): os 8 hooks resolviam `HARNESS_DIR` para
`$HOME/.claude/harness` e nenhum lia o diretorio de trabalho. Como o plugin e
user-scope e dispara em todo projeto da maquina, havia UM state para TODOS:

- `.session-files-count` tinha 130 arquivos sob um unico `task_id` — 41 de
  `master_project`, 39 de `harness4claude`, o resto de temporarios. Como
  `harness-reclassify.sh` usa esse contador para promover L0 -> L1, editar
  arquivos num projeto escalava a classificacao de outro.
- `harness-session-start.sh` oferecia retomar a mesma task em toda sessao de
  todo projeto, porque a checagem era so `status == "active"`.

O README documentava isso como decisao ("State is per-machine to allow
cross-project pipeline continuity"). A continuidade continua disponivel, mas
agora por opt-in: `HARNESS_SCOPE=global` restaura o comportamento antigo.

O que e por projeto: `state.json`, `.session-files-count`, `trace-current.md`,
`traces/` — tudo que descreve a TAREFA corrente.
O que continua na raiz: `signals.json` (telemetria e agregada de proposito, e
os registros sao chaveados por `task_id`, entao nao ha contaminacao),
`plugin-root`, `.bootstrap-done`, `skills-index/`, `router/`.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

PROJECTS_SUBDIR = "projects"
SESSIONS_SUBDIR = "sessions"


def default_root() -> Path:
    """Raiz do harness: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "harness"


def _clean(raw: str | os.PathLike | None) -> str:
    """Remove espacos e controles das pontas de um caminho vindo do shell.

    Necessario porque o `print()` do Python no Windows emite `\\r\\n`, e os hooks
    fatiam a saida por `\\n` — sobra um `\\r` grudado no fim. Um caminho com `\\r`
    nao existe, entao `find_repo_root` falhava e caia no cwd cru: a raiz de um
    repo e um subdiretorio dele geravam buckets DIFERENTES, fragmentando o
    estado de um mesmo projeto. Os hooks tambem limpam do lado do bash; esta e a
    segunda barreira, no unico ponto por onde todo caminho passa.
    """
    return str(raw).strip().strip("\r\n\t ") if raw else ""


def _repo_dono_do_worktree(dir_com_git: str) -> str | None:
    """Se `.git` for o arquivo de um worktree, devolve o repositorio dono.

    Num worktree, `.git` e um arquivo de uma linha: `gitdir: <repo>/.git/
    worktrees/<nome>`. Subir dois niveis dali da o repositorio. Ler um arquivo e
    mais barato que abrir um processo, e e por isso que a correcao cabe aqui sem
    violar a regra de nao chamar `git` (ver o docstring de `find_repo_root`).

    **Submodulo nao colapsa.** Ele tambem tem `.git` como arquivo, mas apontando
    para `<pai>/.git/modules/<nome>` — e submodulo e outro repositorio de
    verdade. Colapsa-lo no pai misturaria dois projetos, que e o oposto do que
    se quer. So `worktrees/` colapsa.

    Devolve None em qualquer duvida: arquivo ilegivel, formato inesperado, ou
    alvo que nao existe. Quem chama cai no diretorio, que e o comportamento
    anterior — degradar aqui e melhor que derrubar o hook.
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


def find_repo_root(start: str | os.PathLike | None) -> str | None:
    """Sobe a arvore procurando `.git`. None se nao houver repo.

    Deliberadamente sem subprocess: isto roda em UserPromptSubmit, e um
    `git rev-parse` por prompt custaria mais que a resolucao inteira. Detecta
    worktree tambem, porque nela `.git` e um arquivo, nao um diretorio.

    Desde 2026-09-05 o worktree nao so e detectado: ele **colapsa no repositorio
    dono**. Antes, `harness4codex-worktrees/equipotence-v1` virava o projeto
    `equipotence-v1-bf70fe21` e o repo dele virava `harness4codex-adfb74ad` —
    dois projetos onde ha um, cada um com bucket de estado proprio, e uma tarefa
    reivindicada de um lado invisivel do outro. O plano original pede o
    contrario com todas as letras (aceite E1: "reconhecer worktrees como partes
    do mesmo projeto").
    """
    start = _clean(start)
    if not start:
        return None
    try:
        p = os.path.abspath(start)
    except (OSError, ValueError):
        return None
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return _canonico(_repo_dono_do_worktree(p) or p)
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def project_slug(cwd: str | os.PathLike | None) -> str:
    """Identificador estavel e legivel do projeto: `<basename>-<hash8>`.

    O basename sozinho colidiria entre dois checkouts de mesmo nome; o hash
    sozinho seria ilegivel ao inspecionar `~/.claude/harness/projects/`.
    `normcase` porque no Windows o mesmo diretorio aparece com caixas
    diferentes conforme quem chama.
    """
    cleaned = _clean(cwd)
    root = find_repo_root(cleaned) or (_canonico(os.path.abspath(cleaned)) if cleaned else "")
    if not root:
        return "unknown"
    base = os.path.basename(root.rstrip("/\\")) or "root"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "root"
    digest = hashlib.sha256(os.path.normcase(root).encode("utf-8")).hexdigest()[:8]
    return f"{base[:40]}-{digest}"


def session_slug(session_id: str | None) -> str | None:
    """Identificador seguro e estavel para isolar threads no mesmo worktree."""
    cleaned = _clean(session_id)
    if not cleaned:
        return None
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip("-._") or "session"
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"{readable[:40]}-{digest}"


def is_global_scope(scope: str | None = None) -> bool:
    """HARNESS_SCOPE=global restaura o state unico da maquina."""
    value = scope if scope is not None else os.environ.get("HARNESS_SCOPE", "")
    return value.strip().lower() == "global"


def state_dir(
    root: str | os.PathLike | None = None,
    cwd: str | os.PathLike | None = None,
    scope: str | None = None,
    session_id: str | None = None,
) -> Path:
    """Estado por worktree e, quando conhecido, por sessao do host."""
    base = Path(root) if root is not None else default_root()
    if is_global_scope(scope):
        return base
    project = base / PROJECTS_SUBDIR / project_slug(cwd or os.getcwd())
    session = session_slug(session_id)
    return project / SESSIONS_SUBDIR / session if session else project


def signals_dir(root: str | os.PathLike | None = None) -> Path:
    """signals.json fica sempre na raiz: telemetria e agregada de proposito."""
    return Path(root) if root is not None else default_root()


def ensure_state_dir(
    root: str | os.PathLike | None = None,
    cwd: str | os.PathLike | None = None,
    scope: str | None = None,
    session_id: str | None = None,
) -> Path:
    """Resolve e cria o diretorio de estado. Nunca levanta — hook nao pode falhar."""
    d = state_dir(root, cwd, scope, session_id)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def main() -> int:
    """CLI para os hooks em bash: imprime o diretorio de estado resolvido.

    Uso: python harness_paths.py [--cwd DIR] [--root DIR] [--signals]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Resolve diretorios do harness.")
    parser.add_argument("--cwd", default=None, help="diretorio de trabalho da sessao")
    parser.add_argument("--root", default=None, help="raiz do harness (default: HARNESS_DIR)")
    parser.add_argument("--signals", action="store_true", help="imprime a raiz de signals.json")
    parser.add_argument("--slug", action="store_true", help="imprime so o slug do projeto")
    parser.add_argument("--session-id", default=None, help="id da sessao do host para isolamento de thread")
    args = parser.parse_args()

    if args.slug:
        print(project_slug(args.cwd or os.getcwd()))
        return 0
    if args.signals:
        print(signals_dir(args.root))
        return 0
    print(ensure_state_dir(args.root, args.cwd, session_id=args.session_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

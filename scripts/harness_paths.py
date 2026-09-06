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
import sys
from pathlib import Path

try:
    import _escopo
except ImportError:  # pragma: no cover - so quando importado por caminho de arquivo
    # `_escopo.py` mora ao lado deste arquivo. Importar `harness_paths` por
    # `importlib.spec_from_file_location` nao poe o diretorio no `sys.path`, e
    # sem este resgate a identidade morreria por um detalhe de como quem chama
    # carregou o modulo.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _escopo

PROJECTS_SUBDIR = "projects"
SESSIONS_SUBDIR = "sessions"


def default_root() -> Path:
    """Raiz do harness: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "harness"


def _clean(raw: str | os.PathLike | None) -> str:
    """Delega para a fonte. Ver `_escopo._limpo`."""
    return _escopo._limpo(raw)


def find_repo_root(start: str | os.PathLike | None) -> str | None:
    """Delega para a fonte. Ver `_escopo.raiz_do_repo`."""
    return _escopo.raiz_do_repo(start)


def project_slug(cwd: str | os.PathLike | None) -> str:
    """Delega para a fonte. Ver `_escopo.de_caminho`.

    **`escopo_env=""` e deliberado, e nao e um detalhe.** Nesta camada o slug
    identifica um DIRETORIO; quem aplica `HARNESS_SCOPE=global` e `state_dir`,
    escolhendo o balde. Deixar o override entrar aqui faria o slug virar
    "maquina" e `state_dir` aninhar um balde global dentro de `projects/`.

    Passar a string vazia diz isso em codigo, em vez de depender de esta funcao
    nunca ser chamada com a variavel ligada.
    """
    return _escopo.de_caminho(cwd, escopo_env="").id


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

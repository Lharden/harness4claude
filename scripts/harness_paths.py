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

## O pin de sessao (incidente 2026-09-12)

O balde acima e `projects/<slug do cwd>/sessions/<slug da sessao>`: projeto
ANTES de sessao. So que o `cwd` MUDA no meio de uma sessao, e o slug muda com
ele. A sessao `86459dbf` trabalhou em `master_project/slb-mestrado-projeto` e em
`science-harness`, e o estado dela se partiu em dois bancos:

    02:00:07  classify cria t-20260912-020007618177   -> balde science
    02:07-02:40  o agente grava 3x evidence           -> balde slb
    02:41:18  o Stop le o balde science: 0 evidence   -> gate escalation

A task era dada por fantasma porque `select task_id from tasks` era rodado no
balde errado. O gate estava certo o tempo todo: a task que ele leu de fato nunca
recebeu evidencia.

Ninguem erra sozinho aqui. `harness-classify.sh` e `harness-transactional.py`
leem `payload["cwd"]`; o agente, seguindo o protocolo da skill, le o `$PWD` do
shell. Sao tres amostragens de um valor mutavel, em momentos diferentes, e cada
lado fica internamente consistente enquanto o conjunto se parte.

A correcao inverte a hierarquia na pratica: **a sessao e a unidade de trabalho, o
projeto e rotulo dela**. O primeiro slug que uma sessao cunha fica fixado em
`pins/<slug da sessao>.json`, e as resolucoes seguintes o reusam mesmo com outro
`cwd`. Um arquivo POR SESSAO, nunca um indice compartilhado: e a exclusividade do
nome que dispensa lock, o mesmo argumento do spool em `harness-classify.sh`.

Tres limites deliberados:

- O pin vive aqui, em `state_dir`, e NAO em `project_slug`. `_escopo.divergencia`
  compara os dois cunhadores e reprova se discordarem; pin dentro do slug viraria
  teste vermelho permanente. `_escopo.py` e derivado e nao se edita.
- Sem `session_id` nao ha chave, entao nao ha pin (9,4% das emissoes desta
  maquina caem nesse caso, todas de `session_start`).
- Toda falha de leitura ou escrita do pin degrada para a resolucao por `cwd`.
  `ensure_state_dir` promete nao levantar, e o pin nao pode ser quem quebra isso.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
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
PINS_SUBDIR = "pins"

#: Quantas derivas distintas o pin guarda. Existe so para limitar o arquivo: a
#: deduplicacao por slug ja impede que um `cd` repetido em 200 prompts vire 200
#: linhas, e alguem que circule por mais de 20 projetos numa sessao tem um
#: problema que nao e este.
MAX_DRIFTS = 20


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


def _pin_file(base: Path, session: str) -> Path:
    return base / PINS_SUBDIR / f"{session}.json"


def _read_pin(path: Path) -> dict | None:
    """O pin gravado, ou None se ele nao existe, nao le ou nao serve.

    Um pin sem `project_slug` utilizavel vale tanto quanto pin nenhum, e tratar
    os dois casos igual e o que mantem a promessa de degradacao.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    slug = data.get("project_slug")
    return data if isinstance(slug, str) and slug else None


def _write_pin(path: Path, data: dict) -> None:
    """Grava atomico e em silencio. Falhar aqui custa o pin, nunca o hook."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _adopt(base: Path, session: str) -> str | None:
    """Sessao anterior ao pin: fixa no balde que ela ja tem, se houver.

    Sem isto, o pin de uma sessao viva nasceria apontando para o `cwd` do
    momento — que pode ser justamente o balde orfao, deixando o historico para
    tras. Empate resolve pelo mtime porque o balde mais recente e onde o
    trabalho esta: para `86459dbf` isso escolhe `science-harness` (02:49) sobre
    `slb` (02:40), que e onde a task viva estava.
    """
    found: list[tuple[float, str]] = []
    try:
        for project in (base / PROJECTS_SUBDIR).iterdir():
            bucket = project / SESSIONS_SUBDIR / session
            try:
                if bucket.is_dir():
                    found.append((bucket.stat().st_mtime, project.name))
            except OSError:
                continue
    except OSError:
        return None
    return max(found)[1] if found else None


def _pinned_slug(base: Path, session: str, slug: str) -> str:
    """O slug que esta sessao usa, fixado na primeira resolucao.

    Registrar a deriva e o que separa isto de um pin mudo: quem trocou de
    projeto de proposito ve por que o balde nao acompanhou, em vez de descobrir
    depois que o estado ficou onde nao devia.
    """
    path = _pin_file(base, session)
    pin = _read_pin(path)
    if pin is None:
        pinned = _adopt(base, session) or slug
        _write_pin(path, {
            "project_slug": pinned,
            "pinned_at": datetime.now(timezone.utc).isoformat(),
            "drifts": [],
        })
        return pinned

    pinned = pin["project_slug"]
    if slug != pinned:
        drifts = [d for d in pin.get("drifts", []) if isinstance(d, dict)]
        if slug not in [d.get("project_slug") for d in drifts]:
            drifts.append({
                "project_slug": slug,
                "seen_at": datetime.now(timezone.utc).isoformat(),
            })
            pin["drifts"] = drifts[-MAX_DRIFTS:]
            _write_pin(path, pin)
    return pinned


def state_dir(
    root: str | os.PathLike | None = None,
    cwd: str | os.PathLike | None = None,
    scope: str | None = None,
    session_id: str | None = None,
) -> Path:
    """Estado por sessao do host e, quando ela e desconhecida, por worktree.

    **Escreve**, ao contrario do que o nome sugere: a primeira resolucao de uma
    sessao grava o pin. Deixar a escrita so em `ensure_state_dir` faria dois
    chamadores de `state_dir` divergirem antes de qualquer diretorio existir —
    que e exatamente o defeito sendo consertado.
    """
    base = Path(root) if root is not None else default_root()
    if is_global_scope(scope):
        return base
    slug = project_slug(cwd or os.getcwd())
    session = session_slug(session_id)
    if not session:
        return base / PROJECTS_SUBDIR / slug
    try:
        slug = _pinned_slug(base, session, slug)
    except Exception:
        # O pin e correcao, nao dependencia. Qualquer surpresa cai na resolucao
        # por cwd — o comportamento de antes, que e ruim mas nao e quebrado.
        pass
    return base / PROJECTS_SUBDIR / slug / SESSIONS_SUBDIR / session


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

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

## A validade do pin (incidente 2026-09-21)

O pin acima consertou 2026-09-12 e criou este. Medido no pin real de
`86459dbf`:

    pinned_at  2026-09-12T08:12:52   science-harness-f34c6792
    deriva     2026-09-12T16:03:22   slb-mestrado-projeto
    deriva     2026-09-15T18:59:18   mainframe
    deriva     2026-09-17T17:35:56   harness4claude
    atividade  2026-09-21T09:03-15:40, toda em slb-mestrado-projeto

Nove dias e quatro projetos depois, o estado seguia indo para o balde cunhado
no primeiro dia — 110 tasks em `science-harness` contra 27 em `slb`, e o portao
que bloqueou a sessao lia a task no banco de um repositorio que ela nao tocou.
**O balde nao era de outro projeto: era do projeto de nove dias atras.** O pin
nao tinha validade, e o id da sessao sobrevive ao trecho de trabalho: o host
retoma a sessao, o `cwd` e outro, e o pin nao tem como saber disso.

A correcao poe validade no pin, e a regua exige as DUAS condicoes: ele so cede
quando ficou **parado alem do TTL** *e* o projeto corrente e **outro**. Ceder
so por tempo repinaria uma sessao que voltou ao mesmo lugar; ceder so por
projeto e exatamente o defeito de 2026-09-12 de volta. `last_seen_at` avanca a
cada resolucao — inclusive em deriva — porque o que vence e a INATIVIDADE da
sessao, nao a do projeto: quem alterna entre dois repositorios a tarde inteira
mantem um balde so.

`HARNESS_PIN_TTL_H` (default 24, `0` desliga) e o botao. O balde anterior fica
escrito em `repins`, com as datas: trocar de balde em silencio e como perder o
trabalho guardado no lugar errado.
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


#: Horas de INATIVIDADE da sessao depois das quais o pin deixa de mandar, se o
#: projeto corrente for outro. `0` desliga a validade e volta ao pin permanente.
DEFAULT_PIN_TTL_H = 24.0

#: Segundos de folga antes de reescrever `last_seen_at`. Sem isto o pin seria
#: regravado a cada hook, varias vezes por prompt, para mover um carimbo em
#: milissegundos.
RENOVACAO_MINIMA_S = 60.0


def _pin_ttl_horas() -> float:
    bruto = os.environ.get("HARNESS_PIN_TTL_H", "")
    if not bruto.strip():
        return DEFAULT_PIN_TTL_H
    try:
        return max(float(bruto), 0.0)
    except ValueError:
        return DEFAULT_PIN_TTL_H


def _quando(texto: object) -> datetime | None:
    """A data do pin, ou None se ela nao existe ou nao le."""
    if not isinstance(texto, str) or not texto:
        return None
    try:
        momento = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def _parado_ha(pin: dict, agora: datetime) -> float | None:
    """Segundos desde a ultima resolucao desta sessao. None se nao da para saber.

    `last_seen_at` nasceu em 2026-09-21; pin anterior a isso so tem `pinned_at`,
    e para um pin recem-criado os dois valem a mesma coisa.
    """
    visto = _quando(pin.get("last_seen_at")) or _quando(pin.get("pinned_at"))
    if visto is None:
        return None
    return (agora - visto).total_seconds()


def _pin_venceu(pin: dict, agora: datetime) -> bool:
    """O pin ficou parado alem do TTL.

    **Data ilegivel NAO vence.** A direcao do erro e a razao: repinar por engano
    parte o estado da sessao em dois bancos, que e o incidente de 2026-09-12 que
    o pin existe para impedir. Manter um pin velho demais custa menos, e ainda
    aparece em `drifts`.
    """
    ttl = _pin_ttl_horas()
    if ttl <= 0:
        return False
    parado = _parado_ha(pin, agora)
    return parado is not None and parado > ttl * 3600


def _renovar(pin: dict, agora: datetime) -> bool:
    parado = _parado_ha(pin, agora)
    return parado is None or parado > RENOVACAO_MINIMA_S


def _pinned_slug(base: Path, session: str, slug: str, grava: bool = True) -> str:
    """O slug que esta sessao usa, fixado na primeira resolucao.

    `grava=False` resolve igual e nao toca o pin: e para quem so LE o estado
    (o skill-router). Cunhar pin e trabalho dos hooks que escrevem no balde.

    Registrar a deriva e o que separa isto de um pin mudo: quem trocou de
    projeto de proposito ve por que o balde nao acompanhou, em vez de descobrir
    depois que o estado ficou onde nao devia.

    Tres saidas, nesta ordem, e a ordem importa:

    1. **sem pin** — cunha, adotando o balde existente mais recente se houver;
    2. **pin parado alem do TTL e projeto outro** — repina, guardando o
       anterior em `repins`. Ver a secao "A validade do pin" no topo do modulo;
    3. **qualquer outro caso** — o pin manda. Deriva vira registro, nunca
       mudanca de balde, e `last_seen_at` avanca.

    Trocar (2) e (3) de lugar reabre o incidente de 2026-09-12, porque toda
    deriva viraria repin.
    """
    agora = datetime.now(timezone.utc)
    path = _pin_file(base, session)
    pin = _read_pin(path)
    if pin is None:
        pinned = _adopt(base, session) or slug
        if not grava:
            return pinned
        _write_pin(path, {
            "project_slug": pinned,
            "pinned_at": agora.isoformat(),
            "last_seen_at": agora.isoformat(),
            "drifts": [],
        })
        return pinned

    pinned = pin["project_slug"]

    if slug != pinned and _pin_venceu(pin, agora):
        if not grava:
            return slug
        repins = [r for r in pin.get("repins", []) if isinstance(r, dict)]
        repins.append({
            "project_slug": pinned,
            "pinned_at": pin.get("pinned_at"),
            "last_seen_at": pin.get("last_seen_at"),
            "repinned_at": agora.isoformat(),
            # As derivas sao do pin ANTERIOR e nao querem dizer nada sobre o
            # novo, entao a lista viva zera. Mas elas sao o unico registro dos
            # projetos por onde a sessao passou, e apagar isso no repin seria
            # perder de vez a resposta para "onde mais procurar o estado".
            "drifts": [d for d in pin.get("drifts", []) if isinstance(d, dict)],
        })
        pin["repins"] = repins[-MAX_DRIFTS:]
        pin["project_slug"] = slug
        pin["pinned_at"] = agora.isoformat()
        pin["last_seen_at"] = agora.isoformat()
        pin["drifts"] = []
        _write_pin(path, pin)
        return slug

    if not grava:
        return pinned
    escrever = False
    if slug != pinned:
        drifts = [d for d in pin.get("drifts", []) if isinstance(d, dict)]
        if slug not in [d.get("project_slug") for d in drifts]:
            drifts.append({"project_slug": slug, "seen_at": agora.isoformat()})
            pin["drifts"] = drifts[-MAX_DRIFTS:]
            escrever = True
    if _renovar(pin, agora):
        pin["last_seen_at"] = agora.isoformat()
        escrever = True
    if escrever:
        _write_pin(path, pin)
    return pinned


def state_dir(
    root: str | os.PathLike | None = None,
    cwd: str | os.PathLike | None = None,
    scope: str | None = None,
    session_id: str | None = None,
    grava_pin: bool = True,
) -> Path:
    """Estado por sessao do host e, quando ela e desconhecida, por worktree.

    **Escreve**, ao contrario do que o nome sugere: a primeira resolucao de uma
    sessao grava o pin. Deixar a escrita so em `ensure_state_dir` faria dois
    chamadores de `state_dir` divergirem antes de qualquer diretorio existir —
    que e exatamente o defeito sendo consertado.

    `grava_pin=False` e para leitor puro: resolve o mesmo balde que um escritor
    resolveria agora, sem cunhar nem renovar o pin.
    """
    base = Path(root) if root is not None else default_root()
    if is_global_scope(scope):
        return base
    slug = project_slug(cwd or os.getcwd())
    session = session_slug(session_id)
    if not session:
        return base / PROJECTS_SUBDIR / slug
    try:
        slug = _pinned_slug(base, session, slug, grava=grava_pin)
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

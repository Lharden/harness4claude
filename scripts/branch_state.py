#!/usr/bin/env python
"""branch_state.py — registro de ramos do Branch Keeper.

O problema que isto atende: numa conversa longa nascem ideias-ramo legitimas.
Sem registro, uma e desenvolvida e as outras evaporam; o assunto abandonado
ainda ocupa janela de contexto. Este modulo e a memoria dos ramos — quem nasceu,
qual o endereco dele, e o que esta parkeado na conversa pai.

Tres decisoes travadas em codigo, nao em convencao:

1. **Por projeto, nunca global.** O bucket vem de `harness_paths.state_dir`, o
   mesmo que a auditoria de 2026-07-28 introduziu para `state.json` depois de
   um contador global promover L0->L1 entre projetos diferentes. Um registro
   global de ramos reproduziria a falha com outro nome.

2. **`session_id` nasce com o ramo, nao com a janela.** O CLI aceita
   `--session-id <uuid>`, entao o pai pode gravar o endereco do filho antes de
   o filho existir. Um ramo `pending` ja e enderecavel: quando voce abrir, seis
   dias depois, `claude --resume <uuid>` continua valendo.

3. **Recusar parkeia; so `discard()` apaga.** Foi a decisao explicita do
   usuario. "Agora nao" e o momento exato em que uma ideia costuma se perder —
   o default aqui e o oposto do esquecimento.

O automato de status e fechado de proposito:

    pending --> open --> closed
                  |
                  +----> recalled

`closed` e `recalled` sao terminais. Sem isso, um ramo fechado poderia reabrir
e o bloco de parking passaria a mentir sobre o que ainda esta suspenso.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_config
import harness_paths
from transactional_state import HarnessDatabase, StateTransitionError

SCHEMA_VERSION = 1
FILENAME = "branches.json"

#: Status suspensos: sao estes, e so estes, que aparecem no bloco de parking.
LIVE_STATUSES = ("pending", "open")
TERMINAL_STATUSES = ("closed", "recalled")
ALL_STATUSES = LIVE_STATUSES + TERMINAL_STATUSES

#: `pending` pode ir direto a `closed` (ramo resolvido sem abrir janela).
TRANSITIONS = {
    "pending": {"open", "closed", "recalled"},
    "open": {"closed", "recalled"},
    "closed": set(),
    "recalled": set(),
}

MAX_PARKED_LINES = 5
TOPIC_TRUNC = 80

LOCK_TIMEOUT_S = float(os.environ.get("STATE_LOCK_TIMEOUT_SECS", "5"))
LOCK_STALE_S = float(os.environ.get("STATE_LOCK_STALE_SECS", "30"))
LOCK_POLL_S = float(os.environ.get("STATE_LOCK_POLL_MS", "50")) / 1000.0


# ---------------------------------------------------------------------------
# Paths e lock
# ---------------------------------------------------------------------------


def branches_path(cwd: str | os.PathLike | None = None) -> str:
    """Caminho do registro de ramos deste projeto."""
    return str(harness_paths.state_dir(cwd=cwd) / FILENAME)


def branches_dir(cwd: str | os.PathLike | None = None) -> str:
    """Diretorio das sementes e launchers deste projeto."""
    return str(harness_paths.state_dir(cwd=cwd) / "branches")


def _transaction_context(cwd: str | os.PathLike | None):
    project_home = harness_paths.state_dir(cwd=cwd)
    session_id = None
    try:
        sensor = json.loads((project_home / "branch-sensor.json").read_text(encoding="utf-8"))
        session_id = sensor.get("session_id") if isinstance(sensor, dict) else None
    except (OSError, ValueError):
        pass
    candidates = []
    if session_id:
        candidates.append(harness_paths.state_dir(cwd=cwd, session_id=str(session_id)))
    candidates.append(project_home)
    for home in candidates:
        try:
            projection = json.loads((home / "state.json").read_text(encoding="utf-8"))
            task_id = projection.get("task_id") if isinstance(projection, dict) else None
            if not task_id or not (home / "harness.db").is_file():
                continue
            database = HarnessDatabase(home)
            task = database.task(str(task_id))
            return home, database, task
        except (OSError, ValueError, StateTransitionError):
            continue
    return None


def _sync_task(home: Path, task: dict) -> None:
    path = home / "state.json"
    try:
        projection = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(projection, dict):
            projection = {}
    except (OSError, ValueError):
        projection = {}
    projection.update(
        {
            "task_id": task["task_id"],
            "status": task["status"],
            "current_step": task["phase"],
            "revision": task["revision"],
            "code_revision": task["code_revision"],
            "verified": task["verified"],
            "pending_gate": task["pending_gate"],
            "scope_id": task["scope_id"],
        }
    )
    temporary = path.with_suffix(".json.branch.tmp")
    temporary.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _integer_setting(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _sensor_turn(cwd: str | os.PathLike | None) -> int:
    try:
        payload = json.loads(
            (harness_paths.state_dir(cwd=cwd) / "branch-sensor.json").read_text(encoding="utf-8")
        )
        return int(payload.get("turn", 0)) if isinstance(payload, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


class _Lock:
    """Lock por diretorio, mesmo protocolo de `scripts/state-lock.sh`.

    `mkdir` e atomico em todo filesystem que nos importa, e o bash ja usa esse
    protocolo para `state.json`. Reimplementar com outro mecanismo (flock,
    arquivo .lock) criaria dois locks que nao se enxergam — pior que nenhum.
    """

    def __init__(self, target: str):
        self.dir = target + ".lockdir"

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                os.mkdir(self.dir)
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.stat(self.dir).st_mtime
                    if age >= LOCK_STALE_S:
                        os.rmdir(self.dir)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    # Fail-open: um ramo perdido e menos grave que um hook travado.
                    return self
                time.sleep(LOCK_POLL_S)
            except OSError:
                return self

    def __exit__(self, *exc):
        try:
            os.rmdir(self.dir)
        except OSError:
            pass
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "parent_session": None, "branches": []}


# ---------------------------------------------------------------------------
# Leitura e escrita
# ---------------------------------------------------------------------------


def load(cwd: str | os.PathLike | None = None) -> dict:
    """Le o registro. Arquivo ausente ou corrompido devolve registro vazio.

    Nunca levanta: isto roda dentro de hook, e hook que quebra e pior que hook
    que nao encontra ramo nenhum.
    """
    path = Path(branches_path(cwd))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("branches"), list):
        return _empty()
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("parent_session", None)
    return data


def save(data: dict, cwd: str | os.PathLike | None = None) -> None:
    """Escrita atomica: grava em `.tmp` e renomeia por cima."""
    path = Path(branches_path(cwd))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Nome legivel -> slug ASCII. Vira parte de nome de arquivo e de comando."""
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", ascii_only).strip("-").lower()
    return (s or "ramo")[:48]


def _unique_slug(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# ---------------------------------------------------------------------------
# Operacoes
# ---------------------------------------------------------------------------


def add(
    *,
    cwd: str | os.PathLike | None = None,
    name: str,
    topic: str,
    detector: str = "claude",
    origin_turn: int = 0,
    parent_session: str | None = None,
) -> dict:
    """Registra um ramo novo como `pending` e devolve o registro criado."""
    target = branches_path(cwd)
    with _Lock(target):
        data = load(cwd)
        existing = {b.get("slug") for b in data["branches"]}
        branch = {
            "slug": _unique_slug(slugify(name), existing),
            "name": str(name),
            "topic": str(topic),
            "status": "pending",
            "session_id": str(uuid.uuid4()),
            "seed_path": None,
            "launcher_path": None,
            "created_at": _now(),
            "opened_at": None,
            "closed_at": None,
            "origin_turn": int(origin_turn),
            "detector": detector,
            "conclusion": None,
        }
        transaction = _transaction_context(cwd)
        if transaction is not None:
            home, database, task = transaction
            normalized_topic = unicodedata.normalize("NFKC", str(topic)).casefold().strip()
            try:
                database.create_branch(
                    task["task_id"],
                    branch_id=branch["session_id"],
                    slug=branch["slug"],
                    name=branch["name"],
                    topic=branch["topic"],
                    topic_hash=hashlib.sha256(normalized_topic.encode("utf-8")).hexdigest(),
                    offered_turn=origin_turn or _sensor_turn(cwd),
                    max_offers=_integer_setting("HARNESS_BRANCH_MAX_OFFERS", 2),
                    cooldown_turns=_integer_setting("HARNESS_BRANCH_COOLDOWN_TURNS", 8),
                )
            except StateTransitionError as exc:
                raise ValueError(str(exc)) from exc
            _sync_task(home, database.task(task["task_id"]))
        if parent_session and not data.get("parent_session"):
            data["parent_session"] = parent_session
        data["branches"].append(branch)
        save(data, cwd)
    signal("created")
    return branch


def get(*, cwd: str | os.PathLike | None = None, slug: str) -> dict:
    for b in load(cwd)["branches"]:
        if b.get("slug") == slug:
            return b
    raise KeyError(slug)


def set_status(
    *,
    cwd: str | os.PathLike | None = None,
    slug: str,
    status: str,
    conclusion: str | None = None,
    seed_path: str | None = None,
    launcher_path: str | None = None,
) -> dict:
    """Move o ramo no automato. Transicao invalida levanta `ValueError`."""
    if status not in ALL_STATUSES:
        raise ValueError(f"status desconhecido: {status}")
    target = branches_path(cwd)
    with _Lock(target):
        data = load(cwd)
        for b in data["branches"]:
            if b.get("slug") != slug:
                continue
            atual = b.get("status", "pending")
            if status not in TRANSITIONS.get(atual, set()):
                raise ValueError(f"transicao invalida: {atual} -> {status} ({slug})")
            transaction = _transaction_context(cwd)
            if transaction is not None:
                home, database, task = transaction
                branch_id = str(b.get("session_id") or "")
                try:
                    if status == "open":
                        selected_seed = seed_path or b.get("seed_path")
                        if not selected_seed:
                            raise StateTransitionError("branch seed path is required before opening")
                        current = database.branch(branch_id)
                        if not current.get("approved_at"):
                            database.request_branch_approval(branch_id)
                            database.approve_branch(branch_id)
                        database.open_branch(
                            branch_id,
                            seed_path=str(selected_seed),
                            max_open=_integer_setting("HARNESS_BRANCH_MAX_OPEN", 3),
                        )
                    else:
                        current = database.branch(branch_id)
                        if not current.get("approved_at"):
                            try:
                                database.resolve_branch_decision(branch_id, "park")
                            except StateTransitionError as exc:
                                if "pending branch-open gate" not in str(exc):
                                    raise
                        database.update_branch(
                            branch_id,
                            status=status,
                            seed_path=seed_path,
                            conclusion=conclusion,
                        )
                except StateTransitionError as exc:
                    _sync_task(home, database.task(task["task_id"]))
                    raise ValueError(str(exc)) from exc
                _sync_task(home, database.task(task["task_id"]))
            b["status"] = status
            if status == "open":
                b["opened_at"] = _now()
            if status in TERMINAL_STATUSES:
                b["closed_at"] = _now()
            if conclusion is not None:
                b["conclusion"] = conclusion
            if seed_path is not None:
                b["seed_path"] = seed_path
            if launcher_path is not None:
                b["launcher_path"] = launcher_path
            save(data, cwd)
            signal(status)
            return b
    raise KeyError(slug)


def attach_files(
    *,
    cwd: str | os.PathLike | None = None,
    slug: str,
    seed_path: str,
    launcher_path: str,
) -> dict:
    target = branches_path(cwd)
    with _Lock(target):
        data = load(cwd)
        for branch in data["branches"]:
            if branch.get("slug") != slug:
                continue
            transaction = _transaction_context(cwd)
            if transaction is not None:
                home, database, task = transaction
                try:
                    database.update_branch(
                        str(branch.get("session_id") or ""),
                        status=str(branch.get("status") or "pending"),
                        seed_path=seed_path,
                    )
                except StateTransitionError as exc:
                    raise ValueError(str(exc)) from exc
                _sync_task(home, database.task(task["task_id"]))
            branch["seed_path"] = seed_path
            branch["launcher_path"] = launcher_path
            save(data, cwd)
            return branch
    raise KeyError(slug)


def decide(
    *, cwd: str | os.PathLike | None = None, slug: str, decision: str
) -> dict:
    if decision not in {"park", "discard"}:
        raise ValueError(f"decisao desconhecida: {decision}")
    target = branches_path(cwd)
    with _Lock(target):
        data = load(cwd)
        for branch in data["branches"]:
            if branch.get("slug") != slug:
                continue
            transaction = _transaction_context(cwd)
            if transaction is not None:
                home, database, task = transaction
                try:
                    database.resolve_branch_decision(
                        str(branch.get("session_id") or ""), decision
                    )
                except StateTransitionError as exc:
                    raise ValueError(str(exc)) from exc
                _sync_task(home, database.task(task["task_id"]))
            if decision == "discard":
                data["branches"] = [item for item in data["branches"] if item is not branch]
                save(data, cwd)
                signal("discarded")
                return branch
            return branch
    raise KeyError(slug)


def discard(*, cwd: str | os.PathLike | None = None, slug: str) -> None:
    """Apaga o ramo do registro. Unico caminho de perda, e e explicito."""
    decide(cwd=cwd, slug=slug, decision="discard")


def by_status(cwd: str | os.PathLike | None = None, *statuses: str) -> list[dict]:
    return [b for b in load(cwd)["branches"] if b.get("status") in statuses]


def pending(cwd: str | os.PathLike | None = None) -> list[dict]:
    return by_status(cwd, "pending")


def open_branches(cwd: str | os.PathLike | None = None) -> list[dict]:
    return by_status(cwd, "open")


def can_open(cwd: str | os.PathLike | None = None) -> bool:
    """Teto de janelas simultaneas. Estourou, o ramo novo fica `pending`."""
    try:
        teto = branch_config.get_int("HARNESS_BRANCH_MAX_OPEN")
    except ValueError:
        teto = 3
    transaction = _transaction_context(cwd)
    if transaction is not None:
        _home, database, task = transaction
        opened = [
            branch
            for branch in database.list_branches(task["task_id"])
            if branch["status"] == "open"
        ]
        return len(opened) < teto
    return len(open_branches(cwd)) < teto


def _tokens(text: str) -> set[str]:
    nfkd = unicodedata.normalize("NFKD", str(text).lower())
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return {t for t in re.split(r"[^a-z0-9]+", ascii_only) if len(t) > 2}


def already_seen(
    *, cwd: str | os.PathLike | None = None, topic: str, floor: float = 0.6
) -> bool:
    """Dedupe lexical contra ramos ja registrados.

    Deliberadamente sem embedding: `already_seen` roda no caminho quente do
    hook, e Jaccard sobre tokens resolve o caso real (a mesma ideia voltando
    com as mesmas palavras) sem pagar 1s de Ollama. A camada semantica fica no
    sensor, que ja tem o embedding do turno em maos.
    """
    alvo = _tokens(topic)
    if not alvo:
        return False
    for b in load(cwd)["branches"]:
        outro = _tokens(b.get("topic", ""))
        if not outro:
            continue
        inter = len(alvo & outro)
        union = len(alvo | outro)
        if union and inter / union >= floor:
            return True
    return False


def signal(event: str, harness_root: str | os.PathLike | None = None) -> None:
    """Contador de eventos de ramo em `signals.json`, bloco `branch`.

    Os pisos do sensor (`HARNESS_BRANCH_FLOOR`, `DRIFT_FLOOR`) sao chutes ate
    existir medida. A razao que importa e `discarded / offers`: descarte alto
    significa piso frouxo, e a calibragem deixa de ser opiniao. Mesmo loop que
    `aggregates.classify` fechou para a classificacao.

    Fica na raiz, nao no bucket do projeto: telemetria e agregada de proposito.
    Nunca levanta — perder um contador nao pode custar um ramo.
    """
    path = Path(harness_paths.signals_dir(harness_root)) / "signals.json"
    try:
        with _Lock(str(path)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
            except (OSError, ValueError):
                data = {"version": 3, "tasks": [], "aggregates": {}}
            bloco = data.get("branch")
            if not isinstance(bloco, dict):
                bloco = {}
            bloco[event] = int(bloco.get(event, 0)) + 1
            data["branch"] = bloco
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        pass


#: Truncagem da conclusao devolvida ao pai. Menor que TOPIC_TRUNC nao serve:
#: uma conclusao cortada em 80 chars vira "o ramo terminou" sem dizer no que.
CONCLUSION_TRUNC = 220


def _conclusoes_pendentes(dados: dict) -> list[dict]:
    """Ramos fechados cuja conclusao o pai ainda nao viu.

    O ramo existe para tirar um assunto do pai. Mas se o que ele descobriu
    nunca volta, ramificar vira perder o assunto em vez de organiza-lo — e a
    proxima vez que alguem tocar no tema no pai comeca do zero.

    Entrega UMA vez. Reinjetar a cada turno transformaria a conclusao em ruido
    de fundo, que e o que o proprio parking existe para evitar.
    """
    return [
        b for b in dados.get("branches", [])
        if b.get("status") == "closed"
        and b.get("conclusion")
        and not b.get("conclusion_delivered")
    ]


def _marcar_entregues(cwd, slugs: list) -> None:
    """Marca as conclusoes como vistas.

    Nunca levanta: perder a marca custa uma repeticao; quebrar o hook custa o
    turno inteiro.
    """
    if not slugs:
        return
    try:
        target = branches_path(cwd)
        with _Lock(target):
            dados = load(cwd)
            for b in dados["branches"]:
                if b.get("slug") in slugs:
                    b["conclusion_delivered"] = True
            save(dados, cwd)
    except Exception:
        pass


def parked_block(cwd: str | os.PathLike | None = None) -> str:
    """Bloco `<harness-parked>` injetado no contexto a cada turno.

    Limitado a `MAX_PARKED_LINES` itens com tema truncado. O orcamento e o
    ponto: esta feature existe para poupar contexto — um bloco que cresce sem
    teto gastaria mais do que o parking economiza.
    """
    dados = load(cwd)
    vivos = [b for b in dados["branches"] if b.get("status") in LIVE_STATUSES]
    entregar = _conclusoes_pendentes(dados)
    if not vivos and not entregar:
        return ""

    linhas = []
    for b in vivos[-MAX_PARKED_LINES:]:
        topic = str(b.get("topic", "")).replace("\n", " ").strip()
        if len(topic) > TOPIC_TRUNC:
            topic = topic[:TOPIC_TRUNC].rstrip() + "..."
        linhas.append(
            f'- {topic} -> ramo "{b.get("name")}" ({b.get("status")}). '
            f'NAO desenvolver aqui; ofereca /branch recall {b.get("slug")}.'
        )
    for b in entregar:
        conclusao = str(b.get("conclusion", "")).replace("\n", " ").strip()
        if len(conclusao) > CONCLUSION_TRUNC:
            conclusao = conclusao[:CONCLUSION_TRUNC].rstrip() + "..."
        linhas.append(
            f'- ramo "{b.get("name")}" FECHOU: {conclusao} '
            f'(sessao {str(b.get("session_id", ""))[:8]})'
        )
    if entregar:
        _marcar_entregues(cwd, [b["slug"] for b in entregar])

    corpo = "\n".join(linhas)
    return f"<harness-parked>\n{corpo}\n</harness-parked>"


# ---------------------------------------------------------------------------
# CLI — os hooks em bash e a skill falam com o registro por aqui
# ---------------------------------------------------------------------------


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Registro de ramos do Branch Keeper.")
    p.add_argument(
        "acao", choices=["list", "parked", "add", "status", "decision", "discard", "path"]
    )
    p.add_argument("--cwd", default=None)
    p.add_argument("--slug", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--topic", default="")
    p.add_argument("--set", dest="new_status", default=None)
    p.add_argument("--conclusion", default=None)
    p.add_argument("--seed", default=None)
    p.add_argument("--launcher", default=None)
    p.add_argument("--detector", default="claude")
    p.add_argument("--decision", choices=["park", "discard"], default=None)
    p.add_argument("--parent-session", dest="parent_session", default=None,
                   help="uuid da sessao pai; e o unico fio que liga o ramo a ela")
    p.add_argument("--origin-turn", dest="origin_turn", type=int, default=0)
    args = p.parse_args()
    cwd = args.cwd or os.getcwd()

    if args.acao == "path":
        print(branches_path(cwd))
    elif args.acao == "list":
        print(json.dumps(load(cwd), ensure_ascii=False, indent=2))
    elif args.acao == "parked":
        print(parked_block(cwd))
    elif args.acao == "add":
        if not args.name:
            p.error("add exige --name")
        print(
            json.dumps(
                add(cwd=cwd, name=args.name, topic=args.topic,
                    detector=args.detector, parent_session=args.parent_session,
                    origin_turn=args.origin_turn),
                ensure_ascii=False,
            )
        )
    elif args.acao == "status":
        if not (args.slug and args.new_status):
            p.error("status exige --slug e --set")
        print(
            json.dumps(
                set_status(
                    cwd=cwd,
                    slug=args.slug,
                    status=args.new_status,
                    conclusion=args.conclusion,
                    seed_path=args.seed,
                    launcher_path=args.launcher,
                ),
                ensure_ascii=False,
            )
        )
    elif args.acao == "decision":
        if not (args.slug and args.decision):
            p.error("decision exige --slug e --decision")
        print(
            json.dumps(
                decide(cwd=cwd, slug=args.slug, decision=args.decision),
                ensure_ascii=False,
            )
        )
    elif args.acao == "discard":
        if not args.slug:
            p.error("discard exige --slug")
        discard(cwd=cwd, slug=args.slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

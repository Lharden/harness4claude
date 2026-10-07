#!/usr/bin/env python
"""Registra uma task concluida/abandonada em signals.json e recalcula agregados.

Monta o registro da task (task_id, classification, classification_meta,
arquivos alterados), combina com flags de conclusao, e acrescenta (ou atualiza,
de forma idempotente por task_id) a task em signals.json, recalculando
aggregates — incluindo o bloco `classify` (loop de accuracy regex-suggested vs
semantica-final) — via migrate_state.recompute_aggregates.

De onde vem o registro:

- `--expect-task` e `harness.db` no balde com a task: do banco, pelo id
  (`task`, `classification`, `files`). A projecao `state.json` nao e lida. O
  contador `.session-files-count` so entra se o `task_id` dele for o esperado,
  e o registro diz se entrou (`contador_usado`).
- senao: da projecao, como sempre foi. Com `--expect-task` e projecao de outra
  task, nada e gravado e o exit code e 2 (incidente 2026-06-12: task fantasma
  t-20260612-034438 registrada no lugar da task real porque o state global
  tinha sido trocado entre o inicio do pipeline e o DONE).

Ate 2026-09-30 a projecao era a unica fonte, e `--expect-task` so comparava.
Depois que ela passou a descrever sempre a task VIVA do escopo
(`fix/sim-nao-fecha-entrega`), toda task substituida saia 2 e nunca chegava a
signals.json. Ver `docs/specs/sinal-da-task-substituida-diagnostico.md`.

`--abandoned` exige `--expect-task` e encerra no banco a task esperada, nunca a
da projecao: o abandono vem de troca de assunto, e nesse momento a projecao ja
e da task nova.

Usado pelo harness-workflow no passo DONE. O script so escreve signals.json e,
com `--abandoned`, o desfecho no harness.db — nunca state.json nem o contador.

Uso:
    python record_signal.py --completed --steps "discuss,write-spec,tdd" --expect-task t-XXXXXXXX-XXXXXX
    python record_signal.py --abandoned --reason "user_switch" --expect-task t-XXXXXXXX-XXXXXX
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from branch_state import LockUnavailable, _Lock  # type: ignore[import-not-found]
from harness_paths import recusa_balde_repinado  # type: ignore[import-not-found]
from migrate_state import (  # type: ignore[import-not-found]
    load_json,
    recompute_aggregates,
)

logger = logging.getLogger("harness.record")


def actual_level(files: int) -> str:
    """Deriva o nivel real a partir do numero de arquivos modificados."""
    if files <= 1:
        return "L0"
    if files <= 3:
        return "L1"
    return "L2"


#: Caminho sintetico que o hook transacional grava quando um comando de shell
#: rodou mas nao da para atribuir arquivo nenhum a ele.
PLACEHOLDER_SHELL = "shell-command"


def _arquivos_do_banco(harness_dir: Path, task_id: str) -> tuple[list[str], bool]:
    """Caminhos atribuidos pelo hook transacional, e se houve shell nao atribuido.

    Degrada em silencio: bucket sem `harness.db` e o caso normal em projeto que
    nunca rodou pipeline transacional, nao um erro.
    """
    caminho = Path(harness_dir) / "harness.db"
    if not caminho.is_file():
        return [], False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from transactional_state import HarnessDatabase

        registrados = HarnessDatabase(Path(harness_dir)).files(task_id)
    except Exception:
        return [], False
    reais = [c for c in registrados if c != PLACEHOLDER_SHELL]
    return reais, len(reais) != len(registrados)


def _fora_da_raiz(counter, task_id: str) -> int:
    """Escritas que o hook viu fora da raiz e nao contou. O contador tem um slot
    so; o de outra task nao e desta."""
    if isinstance(counter, dict) and counter.get("task_id") == task_id:
        return len(counter.get("fora_da_raiz") or [])
    return 0


def files_modified(harness_dir, counter: dict, task_id: str) -> int:
    """Quantos arquivos esta task alterou, unindo as DUAS fontes.

    `.session-files-count` so cresce por Edit/Write (`harness-reclassify.sh`,
    matcher `Edit|Write`). A tabela `files` do harness.db recebe o shell (hook
    transacional, matcher `Bash|PowerShell`) e TAMBEM o Edit/Write: o mesmo
    `harness-reclassify.sh` chama `touch_file(..., origem='edit')` desde
    `dcf7aed`. Medido em 2026-09-30 nos bancos da maquina: 2 101 toques `edit`,
    nenhum sem linha em `files`. O que so o contador ve e o Edit/Write que o
    banco recusou — task ja terminal (L0 nasce `done`) ou escrita que falhou.
    Ate 2026-09-30 este texto dizia que as fontes eram disjuntas.
    """
    do_banco, _ = _arquivos_do_banco(Path(harness_dir), task_id) if harness_dir else ([], False)
    do_counter = [str(c) for c in (counter.get("files") or [])]
    uniao = {str(Path(c)) for c in do_counter + do_banco}
    if uniao:
        return len(uniao)
    return int(counter.get("count", 0) or 0)


def build_task(
    state: dict,
    counter: dict,
    *,
    completed: bool,
    steps: list[str],
    reason: str | None,
    timestamp: str,
    harness_dir=None,
) -> dict:
    """Monta o registro da task a partir de state + counter + flags de conclusao."""
    task_id = state.get("task_id") or "unknown"
    if harness_dir is None:
        files = int(counter.get("count", 0) or 0)
        incompleta = False
    else:
        files = files_modified(harness_dir, counter, task_id)
        _, incompleta = _arquivos_do_banco(Path(harness_dir), task_id)
    return _registro(state, files, incompleta, completed=completed, steps=steps,
                     reason=reason, timestamp=timestamp,
                     fora=_fora_da_raiz(counter, task_id))


def build_task_do_banco(
    banco,
    task_id: str,
    counter: dict,
    *,
    completed: bool,
    steps: list[str],
    reason: str | None,
    timestamp: str,
) -> dict | None:
    """O registro da task `task_id` montado do harness.db, sem ler a projecao.

    None quando o banco nao tem a task: quem chama cai na projecao, como antes.
    O contador so entra se for desta task; `contador_usado` diz se entrou,
    porque "nao tinha Edit/Write fora do banco" e "nao deu para saber" nao
    podem virar o mesmo numero.
    """
    from transactional_state import StateTransitionError

    try:
        linha = banco.task(task_id)
    except StateTransitionError:
        return None
    try:
        meta = banco.classification(task_id)
    except StateTransitionError:
        # Task anterior a tabela `classifications`: o mesmo default do caminho
        # da projecao, que `recompute_aggregates` ja sabe nao contar.
        meta = {}
    registrados = banco.files(task_id)
    reais = [c for c in registrados if c != PLACEHOLDER_SHELL]
    usado = isinstance(counter, dict) and counter.get("task_id") == task_id
    do_counter = [str(c) for c in (counter.get("files") or [])] if usado else []
    uniao = {str(Path(c)) for c in reais + do_counter}
    files = len(uniao) if uniao else (int(counter.get("count", 0) or 0) if usado else 0)
    state = {
        "task_id": task_id,
        "classification": linha["legacy_level"],
        "classification_meta": meta,
        "pipeline": linha["pipeline"],
    }
    task = _registro(state, files, len(reais) != len(registrados), completed=completed,
                     steps=steps, reason=reason, timestamp=timestamp,
                     fora=_fora_da_raiz(counter, task_id))
    task["contador_usado"] = usado
    # O desfecho que o banco gravou. `pipeline_completed` sozinho nao separa a
    # task fechada por `complete` da `superseded` verificada (registrada como
    # concluida de proposito, S1 do sinal da substituida). So o registro do
    # banco o leva: a projecao nao e autoridade de desfecho.
    task["desfecho"] = linha["status"]
    return task


def _registro(
    state: dict,
    files: int,
    incompleta: bool,
    *,
    completed: bool,
    steps: list[str],
    reason: str | None,
    timestamp: str,
    fora: int = 0,
) -> dict:
    # Escrita fora da raiz do projeto da sessao e excluida da contagem de
    # proposito (nao expira evidencia), mas e escrita: a medicao e parcial.
    incompleta = incompleta or fora > 0
    task: dict[str, object] = {
        "task_id": state.get("task_id") or "unknown",
        "classification": state.get("classification") or "unknown",
        "classification_meta": state.get("classification_meta", {}),
        "actual_level": actual_level(files),
        "pipeline_completed": completed,
        "steps_executed": steps or list(state.get("pipeline", [])),
        "files_modified": files,
        # Houve comando de shell que pode ter escrito sem dar para ver qual
        # arquivo. Sem esta marca, "nenhum arquivo atribuido" e "nenhum arquivo
        # alterado" ficam indistinguiveis, e e a segunda leitura que vira L0.
        "atribuicao_incompleta": incompleta,
        # Quantos arquivos o hook viu ser escritos fora da raiz e nao contou.
        "fora_da_raiz": fora,
    }
    if completed:
        task["completed_at"] = timestamp
    else:
        task["abandoned_at"] = timestamp
        if reason:
            task["reason"] = reason
    return task


def record(harness_dir: Path, task: dict) -> dict:
    """Acrescenta/atualiza a task em signals.json (idempotente) e recalcula aggregates.

    Levanta `LockUnavailable` se o lock nao vier: gravar sem ele apagaria o que
    o dono do lock estiver gravando.
    """
    signals_path = harness_dir / "signals.json"
    # O mesmo `_Lock` de `branch_state.signal` e `migrate_state.run`: os tres
    # reescrevem o documento inteiro, e ate 2026-09-30 so `signal` o tomava.
    # Lock que um so escritor respeita nao exclui ninguem — a task gravada aqui,
    # ou o contador de `branch` gravado la, sumia sem erro
    # (tests/test_signals_escritores.py).
    with _Lock(str(signals_path), required=True):
        signals = load_json(signals_path) or {
            "version": 3,
            "harness_version": "v3",
            "tasks": [],
            "aggregates": {},
        }
        tasks = [t for t in signals.get("tasks", []) if t.get("task_id") != task["task_id"]]
        tasks.append(task)
        signals["version"] = 3
        signals["harness_version"] = "v3"
        signals["tasks"] = tasks
        signals["aggregates"] = recompute_aggregates(tasks, signals.get("aggregates"))
        # Escrita atomica: tmp -> flush+fsync -> os.replace. Evita signals.json
        # corrompido se o processo morrer no meio do dump (consistente com a escrita
        # de state.json em harness-classify.sh). os.replace e rename atomico.
        tmp = signals_path.parent / f"{signals_path.name}.tmp-{os.getpid()}"
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(signals, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, signals_path)
    return signals


def default_harness_dir() -> Path:
    """Diretorio de estado: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude" / "harness"


def warn_if_flag_diverges_from_env(chosen: Path) -> None:
    """REQ-F13: a flag vence, mas divergencia silenciosa esconde bug."""
    env = os.environ.get("HARNESS_DIR")
    if env and Path(env).expanduser().resolve() != Path(chosen).expanduser().resolve():
        print(
            f"aviso: --harness-dir={chosen} sobrepoe HARNESS_DIR={env}",
            file=sys.stderr,
        )


#: Vai no lugar do id na linha que corrige o `--abandoned` sem `--expect-task`.
#: O unico id que o script tem a mao e o da projecao, e numa troca de assunto
#: ele e o da task nova: imprimi-lo faria a linha copiavel repetir o defeito.
MARCADOR_TASK = "<task_id anotado no inicio do pipeline>"


def _contador_tolerante(harness_dir: Path) -> dict:
    """O contador, ou {} se ausente, ilegivel ou sem forma de dict.

    Ilegivel aqui nao e erro: so quer dizer que ele nao entra no registro, e o
    registro diz isso (`contador_usado: false`).
    """
    try:
        dado = load_json(Path(harness_dir) / ".session-files-count")
    except (OSError, ValueError):
        return {}
    return dado if isinstance(dado, dict) else {}


def _tasks_recentes(harness_dir: Path, limite: int = 5) -> list[tuple]:
    """As ultimas tasks do balde, do banco, so leitura. Vazia se nao der."""
    caminho = Path(harness_dir) / "harness.db"
    if not caminho.is_file():
        return []
    try:
        conexao = sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True, timeout=1.0)
        try:
            return conexao.execute(
                "SELECT task_id, status, legacy_level, started_at FROM tasks "
                "ORDER BY started_at DESC LIMIT ?", (limite,),
            ).fetchall()
        finally:
            conexao.close()
    except sqlite3.Error:
        return []


def _recusa_abandono_sem_task(args) -> None:
    """Exit 2 com a linha que corrige, e com o que ajuda a acha-la.

    ASCII de proposito: no Windows o stderr sai em cp1252.
    """
    partes = [
        f'python "{Path(__file__).resolve()}" --abandoned',
        f'--reason "{args.reason or "<motivo>"}"',
        f'--expect-task "{MARCADOR_TASK}"',
        f'--harness-dir "{args.harness_dir}"',
    ]
    if args.signals_dir:
        partes.append(f'--signals-dir "{args.signals_dir}"')
    print(
        "erro: --abandoned exige --expect-task. Sem ele o abandono cairia na task "
        "da projecao, e numa troca de assunto ela e a task NOVA. Nada registrado, "
        "nada encerrado. Rode de novo com o task_id anotado no inicio do pipeline:",
        file=sys.stderr,
    )
    print(" ".join(partes), file=sys.stderr)
    recentes = _tasks_recentes(args.harness_dir)
    if recentes:
        print("Tasks recentes deste balde (harness.db), da mais nova:", file=sys.stderr)
        for task_id, status, nivel, inicio in recentes:
            print(f"  {task_id}  {status}  {nivel}  {inicio}", file=sys.stderr)


def main() -> int:
    """Ponto de entrada CLI."""
    parser = argparse.ArgumentParser(description="Registra task em signals.json.")
    default_dir = default_harness_dir()
    parser.add_argument("--harness-dir", type=Path, default=default_dir,
                        help="bucket do projeto: onde ficam state.json e o contador")
    parser.add_argument("--signals-dir", type=Path, default=None,
                        help="raiz onde signals.json e agregado entre projetos "
                             "(default: --harness-dir)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--completed", action="store_true", help="pipeline concluido")
    group.add_argument("--abandoned", action="store_true", help="task abandonada")
    parser.add_argument("--steps", default="", help="steps executados (CSV)")
    parser.add_argument("--reason", default=None, help="motivo do abandono")
    parser.add_argument(
        "--expect-task",
        default=None,
        metavar="TASK_ID",
        help="task_id esperado; aborta sem gravar se o state.json contiver outro "
        "(proteção contra state sobrescrito por sessão paralela)",
    )
    args = parser.parse_args()
    warn_if_flag_diverges_from_env(args.harness_dir)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    expect = (args.expect_task or "").strip() or None
    recusa = recusa_balde_repinado(args.harness_dir, expect)
    if recusa:
        print(recusa, file=sys.stderr)
        return 2
    if args.abandoned and not expect:
        _recusa_abandono_sem_task(args)
        return 2
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    completed = not args.abandoned
    timestamp = datetime.now(timezone.utc).isoformat()

    banco = None
    task = None
    if expect and (args.harness_dir / "harness.db").is_file():
        # Presente e ilegivel nao vira "grava o que a projecao disser": nao
        # conseguir perguntar a autoridade nao e o mesmo que ela nao saber.
        # Trava de escrita ja espera o `busy_timeout` (5 s) dentro do SQLite.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from transactional_state import HarnessDatabase

            banco = HarnessDatabase(args.harness_dir)
            task = build_task_do_banco(
                banco, expect, _contador_tolerante(args.harness_dir), completed=completed,
                steps=steps, reason=args.reason, timestamp=timestamp,
            )
        except (sqlite3.Error, OSError) as exc:
            logger.error(
                "harness.db de %s nao pode ser lido (%s); nada registrado. Rode de "
                "novo o mesmo comando quando o banco responder.", args.harness_dir, exc,
            )
            return 1

    if task is None:
        state = load_json(args.harness_dir / "state.json")
        if expect and (state or {}).get("task_id") != expect:
            logger.error(
                "state.json de %s contem task %s, esperado %s, e o harness.db do balde "
                "nao tem a task esperada: nada registrado. Causas, nesta ordem: (1) "
                "--harness-dir aponta para o bucket do PROJETO, ou de outra sessao, e "
                "nao o da SESSAO da task (resolva com harness_paths.py --session-id "
                "<id>); (2) state sobrescrito por outra sessao.",
                args.harness_dir,
                (state or {}).get("task_id"),
                expect,
            )
            return 2
        counter = load_json(args.harness_dir / ".session-files-count")
        task = build_task(
            state, counter, completed=completed, steps=steps, reason=args.reason,
            timestamp=timestamp, harness_dir=args.harness_dir,
        )
        # Fora do banco nao ha o que encerrar nele: `abandon_task` levantaria
        # "task not found" depois de a telemetria ja ter sido gravada.
        banco = None
    try:
        signals = record(args.signals_dir or args.harness_dir, task)
    except LockUnavailable as exc:
        logger.error("nada registrado em signals.json: %s. Rode de novo.", exc)
        return 1
    if args.abandoned and banco is not None and expect:
        # O abandono tem de chegar a autoridade: a continuacao pergunta ao banco,
        # e uma task abandonada so na telemetria voltava como CONTINUING no
        # prompt seguinte (ramo ciclo-de-vida-da-task, achado do /code-review).
        # E vai para a task ESPERADA: a da projecao, numa troca de assunto, e a
        # task nova que o usuario acabou de pedir.
        try:
            encerrada = banco.abandon_task(expect, reason=args.reason)
        except Exception as exc:  # noqa: BLE001 - telemetria ja gravada; o erro vai escrito
            logger.error("task %s NAO foi encerrada no harness.db: %s", expect, exc)
            return 1
        # A linha foi gravada com o status de antes do abandono. Regrava com o
        # que o banco devolveu (`abandoned`, ou o terminal que ja estava la).
        task["desfecho"] = encerrada["status"]
        signals = record(args.signals_dir or args.harness_dir, task)
    accuracy = signals["aggregates"].get("classify", {}).get("avg_classify_accuracy")
    logger.info(
        "registrado %s (level=%s, files=%s); avg_classify_accuracy=%s",
        task["task_id"], task["actual_level"], task["files_modified"], accuracy,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

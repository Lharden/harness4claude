#!/usr/bin/env python
"""Registra uma task concluida/abandonada em signals.json e recalcula agregados.

Le task_id/classification/classification_meta do state.json e files_modified do
counter (.session-files-count), combina com flags de conclusao, e acrescenta
(ou atualiza, de forma idempotente por task_id) a task em signals.json,
recalculando aggregates — incluindo o bloco `classify` (loop de accuracy
regex-suggested vs semantica-final) — via migrate_state.recompute_aggregates.

Usado pelo harness-workflow no passo DONE.

Uso:
    python record_signal.py --completed --steps "discuss,write-spec,tdd" --expect-task t-XXXXXXXX-XXXXXX
    python record_signal.py --abandoned --reason "user_switch"

--expect-task protege contra state.json sobrescrito por sessão paralela
(incidente 2026-06-12: task fantasma t-20260612-034438 registrada no lugar
da task real porque o state global tinha sido trocado entre o início do
pipeline e o DONE). Com mismatch, nada é gravado e o exit code é 2.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


def files_modified(harness_dir, counter: dict, task_id: str) -> int:
    """Quantos arquivos esta task alterou, unindo as DUAS fontes.

    `.session-files-count` so cresce por Edit/Write (`harness-reclassify.sh`,
    matcher `Edit|Write`); a tabela `files` do harness.db so recebe o que o hook
    transacional ve (matcher `Bash|PowerShell`). As fontes sao disjuntas, e ler
    uma delas subconta — em modo Bash-first, sistematicamente.
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
    }
    if completed:
        task["completed_at"] = timestamp
    else:
        task["abandoned_at"] = timestamp
        if reason:
            task["reason"] = reason
    return task


def record(harness_dir: Path, task: dict) -> dict:
    """Acrescenta/atualiza a task em signals.json (idempotente) e recalcula aggregates."""
    signals_path = harness_dir / "signals.json"
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
    state = load_json(args.harness_dir / "state.json")
    if args.expect_task and (state or {}).get("task_id") != args.expect_task:
        logger.error(
            "state.json contem task %s, esperado %s — nada registrado "
            "(state sobrescrito por outra sessao?)",
            (state or {}).get("task_id"),
            args.expect_task,
        )
        return 2
    counter = load_json(args.harness_dir / ".session-files-count")
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    completed = not args.abandoned
    timestamp = datetime.now(timezone.utc).isoformat()
    task = build_task(
        state, counter, completed=completed, steps=steps, reason=args.reason,
        timestamp=timestamp, harness_dir=args.harness_dir,
    )
    signals = record(args.signals_dir or args.harness_dir, task)
    accuracy = signals["aggregates"].get("classify", {}).get("avg_classify_accuracy")
    logger.info(
        "registrado %s (level=%s, files=%s); avg_classify_accuracy=%s",
        task["task_id"], task["actual_level"], task["files_modified"], accuracy,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Migra state.json e signals.json do Harness para o schema v3.

- state.json: adiciona `schema_version: 3` e garante os 7 campos base + campos
  FSM opcionais. `classification` permanece string (backward-compat).
- signals.json: migra v2 -> v3, preservando `tasks`, adicionando `harness_version`,
  o bloco `aggregates.sdd_usage` e o bloco `aggregates.classify` (loop de feedback
  da classificacao em 2 camadas). Recalcula os agregados a partir das tasks.

Idempotente: rodar multiplas vezes nao corrompe (agregados sao recalculados da
fonte de verdade `tasks`; campos ausentes sao preenchidos, presentes sao mantidos).

Uso:
    python migrate-state.py [--harness-dir DIR] [--dry-run] [--no-backup]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("harness.migrate")

SCHEMA_VERSION_STATE = 3
SCHEMA_VERSION_SIGNALS = 3

STATE_BASE_FIELDS: dict[str, object] = {
    "task_id": None,
    "classification": None,
    "status": "idle",
    "pipeline": [],
    "current_step": None,
    "artifacts_so_far": [],
    "started_at": None,
}

SDD_USAGE_DEFAULT: dict[str, int] = {
    "specs_generated": 0,
    "spec_lights_generated": 0,
    "designs_generated": 0,
    "verifications_passed": 0,
    "verifications_failed": 0,
    "clarifications_resolved": 0,
}


def load_json(path: Path) -> dict:
    """Carrega um arquivo JSON UTF-8 como dict (ou {} se ausente)."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def backup_file(path: Path) -> Path | None:
    """Copia path para path.bak-migrate-<timestamp>. Retorna o caminho do backup."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(path.suffix + f".bak-migrate-{stamp}")
    shutil.copy2(path, backup)
    logger.info("backup: %s -> %s", path.name, backup.name)
    return backup


def migrate_state(state: dict) -> dict:
    """Retorna uma copia do state com schema_version 3 e campos base garantidos."""
    migrated = dict(state) if state else {}
    for key, default in STATE_BASE_FIELDS.items():
        migrated.setdefault(key, default() if callable(default) else default)
    migrated["schema_version"] = SCHEMA_VERSION_STATE
    return migrated


def _is_abandoned(task: dict) -> bool:
    """True se a task foi abandonada (nao deve contar nos agregados de execucao)."""
    return bool(task.get("abandoned_at")) or task.get("actual_level") == "unknown"


def _level_of(task: dict) -> str | None:
    """Extrai o nivel (L0/L1/L2) de actual_level ou da classification string."""
    level = task.get("actual_level")
    if level in {"L0", "L1", "L2"}:
        return level
    cls = task.get("classification") or ""
    head = cls.split("-", 1)[0]
    return head if head in {"L0", "L1", "L2"} else None


def recompute_aggregates(tasks: list[dict], previous: dict | None = None) -> dict:
    """Recalcula os agregados v3 a partir da lista de tasks (fonte de verdade)."""
    prev = previous or {}
    counted = [t for t in tasks if not _is_abandoned(t)]
    total = len(counted)
    levels = {"L0": 0, "L1": 0, "L2": 0}
    files_total = 0
    completed = 0
    for task in counted:
        level = _level_of(task)
        if level in levels:
            levels[level] += 1
        files_total += int(task.get("files_modified", 0) or 0)
        if task.get("pipeline_completed"):
            completed += 1

    classified = [
        t for t in tasks
        if (t.get("classification_meta") or {}).get("agreed") is not None
    ]
    agreed = [t for t in classified if (t.get("classification_meta") or {}).get("agreed")]
    overrides = [
        t for t in classified
        if (t.get("classification_meta") or {}).get("source") == "human_override"
    ]
    accuracy: float | None = (len(agreed) / len(classified)) if classified else None

    sdd_usage = dict(SDD_USAGE_DEFAULT)
    sdd_usage.update(prev.get("sdd_usage", {}))

    return {
        "total_tasks": total,
        "l0_count": levels["L0"],
        "l1_count": levels["L1"],
        "l2_count": levels["L2"],
        "pipeline_completion_rate": (completed / total) if total else 0,
        "avg_files_per_task": (files_total / total) if total else 0,
        "sdd_usage": sdd_usage,
        "classify": {
            "total_classified": len(classified),
            "avg_classify_accuracy": accuracy,
            "regex_vs_semantic_agreement": accuracy,
            "human_override_count": len(overrides),
        },
    }


def migrate_signals(signals: dict) -> dict:
    """Migra o signals.json para v3, preservando tasks e recalculando agregados."""
    tasks = list(signals.get("tasks", []))
    return {
        "version": SCHEMA_VERSION_SIGNALS,
        "harness_version": "v3",
        "tasks": tasks,
        "aggregates": recompute_aggregates(tasks, signals.get("aggregates")),
    }


def validate(data: dict, schema_path: Path) -> list[str]:
    """Valida data contra um JSON Schema. Retorna lista de erros ([] se OK).

    Se a lib jsonschema nao estiver instalada, retorna [] com um warning (a
    validacao estrita roda nos testes/health-check, nao bloqueia a migracao).
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        logger.warning("jsonschema ausente — pulando validacao de %s", schema_path.name)
        return []
    schema = load_json(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in validator.iter_errors(data)]


def _write(path: Path, data: dict, *, dry_run: bool) -> None:
    """Grava data como JSON indentado (ou apenas loga, se dry_run)."""
    if dry_run:
        logger.info("[dry-run] gravaria %s", path.name)
        return
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    logger.info("gravado: %s", path.name)


def run(harness_dir: Path, schemas_dir: Path, *, dry_run: bool, do_backup: bool) -> int:
    """Executa a migracao completa. Retorna 0 em sucesso, 1 se houver erro de schema."""
    state_path = harness_dir / "state.json"
    signals_path = harness_dir / "signals.json"

    state = migrate_state(load_json(state_path))
    signals = migrate_signals(load_json(signals_path))

    errors: list[str] = []
    errors += [f"state: {e}" for e in validate(state, schemas_dir / "state.schema.json")]
    errors += [f"signals: {e}" for e in validate(signals, schemas_dir / "signals.schema.json")]
    if errors:
        for err in errors:
            logger.error("validacao falhou: %s", err)
        return 1

    if do_backup and not dry_run:
        backup_file(state_path)
        backup_file(signals_path)

    _write(state_path, state, dry_run=dry_run)
    _write(signals_path, signals, dry_run=dry_run)
    logger.info("migracao concluida (state schema_version=%s, signals version=%s)",
                state["schema_version"], signals["version"])
    return 0


def main() -> int:
    """Ponto de entrada CLI."""
    parser = argparse.ArgumentParser(description="Migra state/signals do Harness para v3.")
    _env_dir = os.environ.get("HARNESS_DIR")
    default_dir = (
        Path(_env_dir).expanduser().resolve()
        if _env_dir
        else Path.home() / ".claude" / "harness"
    )
    parser.add_argument("--harness-dir", type=Path, default=default_dir)
    parser.add_argument("--schemas-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent / "schemas")
    parser.add_argument("--dry-run", action="store_true", help="nao grava, apenas reporta")
    parser.add_argument("--no-backup", action="store_true", help="nao cria .bak")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(args.harness_dir, args.schemas_dir, dry_run=args.dry_run, do_backup=not args.no_backup)


if __name__ == "__main__":
    raise SystemExit(main())

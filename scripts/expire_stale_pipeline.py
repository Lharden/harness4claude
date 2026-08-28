#!/usr/bin/env python
"""Expira pipeline ativo que passou do TTL, liberando a classificacao.

Motivacao (auditoria 2026-07-28): `harness-classify.sh` emite CONTINUING e sai
ANTES de classificar sempre que o state tem `status == "active"`. Nada no sistema
fecha o state quando uma sessao e abandonada — o `record_signal.py` grava em
`signals.json` mas nao volta o `state.json` para `idle`. Resultado observado:
uma task de 2026-07-24 ficou ativa por 4 dias e bloqueou TODA classificacao nova
em TODOS os projetos da maquina (o state e global).

Este script e o disjuntor. Roda no inicio de `harness-classify.sh` e de
`harness-session-start.sh`: se o pipeline ativo passou do TTL, registra o
abandono em `signals.json` (preservando a telemetria) e devolve o state para
`idle`, de modo que o prompt corrente seja classificado normalmente.

Contrato com os hooks:
- stdout: `EXPIRED <task_id>` quando expirou; vazio caso contrario.
- exit code: SEMPRE 0. Um hook nunca pode bloquear o prompt do usuario.

Uso:
    python expire_stale_pipeline.py [--harness-dir DIR] [--ttl-hours N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_signal import build_task, record  # type: ignore[import-not-found]
from transactional_state import HarnessDatabase  # type: ignore[import-not-found]

DEFAULT_TTL_HOURS = 24

IDLE_STATE = {
    "task_id": None,
    "schema_version": 3,
    "classification": None,
    "status": "idle",
    "pipeline": [],
    "current_step": None,
    "artifacts_so_far": [],
    "started_at": None,
}


def default_harness_dir() -> Path:
    """Diretorio de estado: HARNESS_DIR se definida, senao ~/.claude/harness."""
    env = os.environ.get("HARNESS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".claude" / "harness"


def default_ttl_hours() -> float:
    """TTL em horas: HARNESS_PIPELINE_TTL_H se definida e valida, senao 24."""
    raw = os.environ.get("HARNESS_PIPELINE_TTL_H")
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        ttl = float(raw)
    except ValueError:
        return DEFAULT_TTL_HOURS
    return ttl if ttl > 0 else DEFAULT_TTL_HOURS


def is_expired(state: dict, ttl_hours: float, *, now: datetime | None = None) -> bool:
    """True se o state tem pipeline ativo e passou do TTL.

    `started_at` ausente ou impossivel de parsear conta como EXPIRADO: e
    exatamente a forma de state travado que nao teria como se recuperar sozinha.
    """
    if state.get("status") != "active" or not state.get("pipeline"):
        return False

    started_raw = state.get("started_at")
    if not started_raw:
        return True
    try:
        started = datetime.fromisoformat(str(started_raw))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    return (now - started) > timedelta(hours=ttl_hours)


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp -> flush+fsync -> os.replace, igual ao harness-classify.sh."""
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def expire(
    harness_dir: Path | str,
    ttl_hours: float,
    *,
    now: datetime | None = None,
    signals_dir: Path | str | None = None,
) -> str | None:
    """Expira o pipeline se necessario. Retorna o task_id expirado, ou None.

    `harness_dir` e o bucket do PROJETO (onde vivem state.json e o contador);
    `signals_dir` e a raiz, onde a telemetria e agregada entre projetos. Quando
    omitido, ambos coincidem — o caso de HARNESS_SCOPE=global e o dos testes.

    Aceita `str` porque o python inline do `harness-classify.sh` passa o dirname
    ja convertido por cygpath — nao ha pathlib do lado do hook.
    """
    harness_dir = Path(harness_dir)
    signals_dir = Path(signals_dir) if signals_dir is not None else harness_dir
    state_path = harness_dir / "state.json"
    try:
        with state_path.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None

    if not is_expired(state, ttl_hours, now=now):
        return None

    task_id = state.get("task_id") or "unknown"

    # Telemetria antes do reset: sem isso a task some sem deixar rastro e a
    # taxa de abandono (sinal de que o pipeline e pesado demais) fica invisivel.
    try:
        counter = {}
        counter_path = harness_dir / ".session-files-count"
        try:
            with counter_path.open(encoding="utf-8") as fh:
                counter = json.load(fh)
        except (OSError, ValueError):
            counter = {}
        task = build_task(
            state,
            counter,
            completed=False,
            steps=[],
            reason=f"ttl_expired_{ttl_hours:g}h",
            timestamp=(now or datetime.now(timezone.utc)).isoformat(),
        )
        record(signals_dir, task)
    except Exception:
        pass

    try:
        scope_id = str(state.get("scope_id") or "legacy")
        current = now or datetime.now(timezone.utc)
        HarnessDatabase(harness_dir).expire_stale_task(
            scope_id,
            ttl_seconds=ttl_hours * 3600,
            now=current.timestamp(),
        )
    except Exception:
        # Legacy installations may not have a transactional task yet. The
        # JSON projection still needs to recover, so expiry remains fail-open.
        pass

    _atomic_write_json(state_path, dict(IDLE_STATE))
    _atomic_write_json(harness_dir / ".session-files-count", {"count": 0, "files": [], "task_id": None})
    return task_id


def main() -> int:
    """Ponto de entrada CLI. Sempre retorna 0 — hook nunca bloqueia."""
    parser = argparse.ArgumentParser(description="Expira pipeline ativo alem do TTL.")
    parser.add_argument("--harness-dir", type=Path, default=None,
                        help="bucket do projeto (state.json + contador)")
    parser.add_argument("--signals-dir", type=Path, default=None,
                        help="raiz onde signals.json e agregado (default: --harness-dir)")
    parser.add_argument("--ttl-hours", type=float, default=None)
    args = parser.parse_args()

    harness_dir = args.harness_dir or default_harness_dir()
    ttl_hours = args.ttl_hours if args.ttl_hours is not None else default_ttl_hours()

    try:
        expired = expire(Path(harness_dir), ttl_hours, signals_dir=args.signals_dir)
    except Exception:
        return 0

    if expired:
        print(f"EXPIRED {expired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

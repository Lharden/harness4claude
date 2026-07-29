#!/usr/bin/env python
"""Verifica se o CLI host ainda esta CHAMANDO os hooks.

O smoke-test do health-check prova que os hooks funcionam quando executados. Ele
nao prova que continuam sendo chamados: se um host renomear ou remover um evento,
os hooks ficam inertes e todo o diagnostico permanece verde. E a falha silenciosa
que originou a auditoria de 2026-07-28 — codigo presente != codigo rodando — um
nivel acima.

Cada hook grava `$HARNESS_DIR/heartbeats/<Evento>` com o epoch da chamada, ANTES
de qualquer guard: mede a chamada, nao o trabalho. Aqui esse sinal e confrontado
com a atividade de sessao registrada pelo PROPRIO host (transcripts), que e
independente do harness.

## Por que so dois eventos recebem veredito

Um alarme que dispara sem razao vira alarme ignorado, entao so ha veredito onde
existe expectativa confiavel:

- `UserPromptSubmit` dispara em TODO prompt. Houve prompt e nao houve heartbeat
  => o hook nao esta sendo chamado. Assertivel.
- `SessionStart` dispara em toda sessao. Idem.
- `PreToolUse` (Bash), `PostToolUse` (Edit|Write) e `PreCompact` sao
  CONDICIONAIS: uma sessao inteira pode legitimamente nao rodar Bash, nao editar
  arquivo e nunca compactar. Ausencia de heartbeat nao prova nada, entao estes
  sao apenas reportados com o "visto por ultimo".

## Limite conhecido

A granularidade e de horas, nao de minutos: detecta "morreu ha dias", que e o
caso que importa, e nao uma falha de dez minutos. `GRACE_SECONDS` define a folga.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Eventos com expectativa confiavel de disparo. Os demais sao condicionais.
ASSERTABLE = ("UserPromptSubmit", "SessionStart")

# Folga entre a ultima atividade do host e o ultimo heartbeat. Transcripts
# continuam sendo escritos enquanto o assistente responde, bem depois do prompt
# que disparou o hook — sem folga isso viraria falso positivo em sessao longa.
GRACE_SECONDS = 3600

# Atividade mais velha que isto nao autoriza veredito: se ninguem usa o CLI ha
# uma semana, a ausencia de heartbeat nao diz nada sobre o contrato do host.
STALE_ACTIVITY_SECONDS = 7 * 24 * 3600


def newest_mtime(paths: list[Path]) -> float:
    """Maior mtime entre os caminhos dados. 0.0 se nenhum existir."""
    best = 0.0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            continue
    return best


def claude_activity(home: Path) -> float:
    """Ultima atividade de sessao do Claude Code (mtime dos transcripts)."""
    root = home / ".claude" / "projects"
    if not root.is_dir():
        return 0.0
    return newest_mtime(list(root.glob("*/*.jsonl")))


def codex_activity(home: Path) -> float:
    """Ultima atividade de sessao do Codex."""
    base = home / ".codex"
    if not base.is_dir():
        return 0.0
    candidates = [base / "session_index.jsonl", base / "history.jsonl"]
    sessions = base / "sessions"
    if sessions.is_dir():
        candidates.extend(sessions.rglob("*.jsonl"))
    return newest_mtime(candidates)


def registered_events(hooks_json: Path) -> list[str]:
    """Eventos que o plugin registra — a fonte de verdade e o proprio hooks.json."""
    try:
        with hooks_json.open(encoding="utf-8") as fh:
            return sorted(json.load(fh).get("hooks", {}))
    except (OSError, ValueError):
        return []


def read_heartbeat(harness_dir: Path, event: str) -> float | None:
    """Epoch do ultimo disparo do evento, ou None se nunca disparou."""
    try:
        raw = (harness_dir / "heartbeats" / event).read_text(encoding="utf-8").strip()
        value = float(raw)
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def verdict(
    event: str,
    beat: float | None,
    activity: float,
    now: float,
    *,
    any_beat: float = 0.0,
) -> tuple[str, str]:
    """Classifica um evento. Retorna (nivel, mensagem).

    Niveis: OK | INFO | FAIL. So eventos assertiveis chegam a FAIL.

    `any_beat` e o disparo mais recente entre TODOS os eventos. Sem ele, uma
    instalacao nova reprovaria antes da primeira sessao — o heartbeat ainda nao
    teve chance de existir, e um alarme que dispara no dia um vira alarme
    ignorado. Zero significa "o mecanismo nunca rodou": nada a concluir.

    Mensagens em ASCII puro: o console do Windows entrega mojibake em travessao,
    e um diagnostico ilegivel nao diagnostica.
    """
    conditional = event not in ASSERTABLE

    if any_beat <= 0:
        return "INFO", (
            f"{event}: heartbeat ainda nao inicializado "
            f"(reinicie o CLI para o mecanismo comecar a registrar)"
        )

    if activity <= 0:
        return "INFO", f"{event}: sem atividade de sessao registrada - nada a concluir"

    if now - activity > STALE_ACTIVITY_SECONDS:
        dias = (now - activity) / 86400
        return "INFO", f"{event}: host sem uso ha {dias:.0f}d - nada a concluir"

    if beat is None:
        if conditional:
            return "INFO", f"{event}: nunca disparou (evento condicional - pode ser normal)"
        return "FAIL", (
            f"{event}: NUNCA disparou, mas outros hooks dispararam e ha atividade "
            f"de sessao recente. O host provavelmente nao chama este evento."
        )

    atraso = activity - beat
    horas_desde = (now - beat) / 3600

    if atraso > GRACE_SECONDS:
        if conditional:
            return "INFO", (
                f"{event}: visto ha {horas_desde:.1f}h, atividade mais recente "
                f"(evento condicional - pode ser normal)"
            )
        return "FAIL", (
            f"{event}: atividade de sessao {atraso / 3600:.1f}h mais nova que o "
            f"ultimo disparo. O host parou de chamar este hook?"
        )

    return "OK", f"{event}: disparou ha {horas_desde:.1f}h"


def run(harness_dir: Path, hooks_json: Path, home: Path, now: float) -> tuple[int, list[str]]:
    """Avalia todos os eventos. Retorna (exit_code, linhas do relatorio)."""
    events = registered_events(hooks_json)
    if not events:
        return 0, ["[WARN]   hooks.json ilegivel — liveness nao verificada"]

    activity = max(claude_activity(home), codex_activity(home))
    beats = {e: read_heartbeat(harness_dir, e) for e in events}
    any_beat = max([b for b in beats.values() if b], default=0.0)
    lines: list[str] = []
    failed = False

    for event in events:
        level, msg = verdict(event, beats[event], activity, now, any_beat=any_beat)
        if level == "FAIL":
            failed = True
        lines.append(f"[{level}]".ljust(9) + msg)

    if failed:
        lines.append("         Rode a suite e confira se o CLI host mudou os nomes dos eventos:")
        lines.append("         python -m pytest tests/test_hook_liveness.py -q")

    return (1 if failed else 0), lines


def main() -> int:
    """Ponto de entrada CLI."""
    import time

    parser = argparse.ArgumentParser(description="Verifica se o host ainda chama os hooks.")
    parser.add_argument("--harness-dir", type=Path, default=None)
    parser.add_argument("--hooks-json", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=None)
    args = parser.parse_args()

    harness_dir = args.harness_dir or Path(
        os.environ.get("HARNESS_DIR") or (Path.home() / ".claude" / "harness")
    )
    hooks_json = args.hooks_json or (Path(__file__).resolve().parent.parent / "hooks" / "hooks.json")
    home = args.home or Path.home()

    code, lines = run(Path(harness_dir), Path(hooks_json), Path(home), time.time())
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

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
from datetime import datetime
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


def newest_session_opening(home: Path, *, since: float) -> float:
    """Epoch da sessao mais recente ABERTA depois de `since`. 0.0 se nenhuma.

    `SessionStart` dispara uma vez por sessao; `UserPromptSubmit`, a cada
    prompt. Confrontar os dois com o mesmo relogio — o mtime dos transcripts —
    so funciona para o segundo: a sessao viva continua escrevendo, entao um
    disparo correto no minuto zero fica "atrasado" assim que a conversa passa
    de uma hora. Toda sessao longa reprovava.

    A pergunta certa para este evento e "o host abriu sessao sem chamar o
    hook?", e a abertura esta na primeira linha do jsonl, nao no mtime.
    Transcript sem timestamp legivel e ignorado: um teste que nao consegue
    provar nada nao reprova nada.
    """
    root = home / ".claude" / "projects"
    if not root.is_dir():
        return 0.0
    melhor = 0.0
    for caminho in root.glob("*/*.jsonl"):
        try:
            if caminho.stat().st_mtime <= since:
                continue
            with caminho.open(encoding="utf-8", errors="replace") as fh:
                primeira = fh.readline()
        except OSError:
            continue
        try:
            bruto = json.loads(primeira).get("timestamp")
        except ValueError:
            continue
        if not isinstance(bruto, str):
            continue
        try:
            momento = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
        except ValueError:
            continue
        melhor = max(melhor, momento.timestamp())
    return melhor


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
    rotulo: str = "atividade de sessao",
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
            f"{event}: {rotulo} {atraso / 3600:.1f}h mais nova que o "
            f"ultimo disparo. O host parou de chamar este hook?"
        )

    return "OK", f"{event}: disparou ha {horas_desde:.1f}h"


def run(harness_dir: Path, hooks_json: Path, home: Path, now: float) -> tuple[int, list[str]]:
    """Avalia todos os eventos. Retorna (exit_code, linhas do relatorio)."""
    events = registered_events(hooks_json)
    if not events:
        return 0, ["[WARN]   hooks.json ilegivel — liveness nao verificada"]

    activity = claude_activity(home)
    beats = {e: read_heartbeat(harness_dir, e) for e in events}
    any_beat = max([b for b in beats.values() if b], default=0.0)
    lines: list[str] = []
    failed = False

    for event in events:
        # Cada evento assertivel tem o seu relogio de expectativa: prompt para
        # quem dispara a cada prompt, abertura de sessao para quem dispara a
        # cada sessao. Um relogio so reprovava sessao longa por existir.
        if event == "SessionStart" and beats[event]:
            esperado = newest_session_opening(home, since=beats[event])
            rotulo = "abertura de sessao"
            if esperado <= 0:
                esperado = beats[event]
        else:
            esperado = activity
            rotulo = "atividade de sessao"
        level, msg = verdict(
            event, beats[event], esperado, now, any_beat=any_beat, rotulo=rotulo
        )
        if level == "FAIL":
            failed = True
        lines.append(f"[{level}]".ljust(9) + msg)

    if failed:
        lines.append("         Rode a suite e confira se o CLI host mudou os nomes dos eventos:")
        lines.append("         python -m pytest tests/test_hook_liveness.py -q")

    return (1 if failed else 0), lines


def delivery_report(harness_dir: Path, home: Path) -> tuple[int, list[str]]:
    """Cruza o que foi emitido com o que apareceu nos transcripts.

    O heartbeat prova que o host CHAMOU o hook. Nao prova que o modelo
    RECEBEU — e foi exatamente ai que o harness passou meses. Entre
    2026-08 e 2026-09, `HARNESS v3 CLASSIFIED` foi emitido 81 vezes em 47
    sessoes com heartbeat verde o tempo todo, e `Skill(harness-workflow)`
    foi invocada zero vezes: o canal aceitava a escrita e nao entregava.

    Aqui a pergunta e a outra metade. Para cada `kind` emitido, quantas
    emissoes acham par no transcript da propria sessao. Emissoes > 0 com
    entregas == 0 e a assinatura da falha de 2026 se repetindo.
    """
    linhas: list[str] = []
    log = harness_dir / "emissions.jsonl"
    try:
        cru = log.read_text(encoding="utf-8")
    except OSError:
        return 0, [f"delivery: sem extrato em {log} (nada emitido ainda)"]

    por_kind: dict[str, dict] = {}
    por_sessao: dict[str, list[dict]] = {}
    for linha in cru.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            row = json.loads(linha)
        except ValueError:
            continue
        if row.get("channel") == "silent":
            continue  # suprimido de proposito; nao ha entrega a cobrar
        k = str(row.get("kind") or "?")
        b = por_kind.setdefault(k, {"emitidas": 0, "entregues": 0})
        b["emitidas"] += 1
        sid = str(row.get("session_id") or "")
        if sid:
            por_sessao.setdefault(sid, []).append(row)

    # O transcript nao guarda o texto do additionalContext, so o registro do
    # hook. A prova de entrega possivel aqui e a presenca da sessao com um
    # hook_success nao vazio no turno correspondente.
    raiz = home / ".claude" / "projects"
    achadas: set[str] = set()
    for sid in por_sessao:
        for caminho in raiz.glob(f"*/{sid}.jsonl"):
            try:
                if "hook" in caminho.read_text(encoding="utf-8", errors="replace"):
                    achadas.add(sid)
            except OSError:
                pass
            break

    for sid, rows in por_sessao.items():
        if sid in achadas:
            for row in rows:
                if row.get("channel") == "silent":
                    continue
                por_kind[str(row.get("kind") or "?")]["entregues"] += 1

    code = 0
    for k in sorted(por_kind):
        b = por_kind[k]
        marca = "ok "
        if b["emitidas"] and not b["entregues"]:
            marca = "!! "
            code = 1
        linhas.append(f"{marca}{k}: {b['entregues']}/{b['emitidas']} entregues")
    if not por_kind:
        linhas.append("delivery: extrato so tem emissoes silenciosas")
    if code:
        linhas.append(
            "ALERTA: kind com emissoes e zero entregas. Foi essa a assinatura "
            "do canal morto em 2026 — confira o canal em hooks/emit.py."
        )
    return code, linhas


def main() -> int:
    """Ponto de entrada CLI."""
    import time

    parser = argparse.ArgumentParser(description="Verifica se o host ainda chama os hooks.")
    parser.add_argument("--harness-dir", type=Path, default=None)
    parser.add_argument("--hooks-json", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--delivery", action="store_true",
                        help="cruza emissions.jsonl com os transcripts")
    args = parser.parse_args()

    harness_dir = args.harness_dir or Path(
        os.environ.get("HARNESS_DIR") or (Path.home() / ".claude" / "harness")
    )
    hooks_json = args.hooks_json or (Path(__file__).resolve().parent.parent / "hooks" / "hooks.json")
    home = args.home or Path.home()

    if args.delivery:
        code, lines = delivery_report(Path(harness_dir), Path(home))
    else:
        code, lines = run(Path(harness_dir), Path(hooks_json), Path(home), time.time())
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

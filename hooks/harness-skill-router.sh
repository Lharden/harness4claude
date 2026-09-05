#!/usr/bin/env bash
# Skill-router v3.3 — shim. Contrato: nunca falha, nunca bloqueia o prompt.
set -uo pipefail

# ---------------------------------------------------------------------------
# Opt-in explicito (auditoria 2026-07-28)
# ---------------------------------------------------------------------------
# A Camada B depende de um Ollama local. Sem ele, o router registrou 88 falhas
# consecutivas — 100% TimeoutError, zero sucessos — e o indice de 276 skills
# nunca foi consultado. Custo real (spawn de python + I/O do indice a cada
# prompt) em troca de nada. Fica dormente ate HARNESS_ROUTER=1.
[ "${HARNESS_ROUTER:-0}" = "1" ] || exit 0

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi
[ -z "$PY" ] && exit 0
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR
mkdir -p "$HARNESS_DIR/router" 2>/dev/null || true
"$PY" "$DIR/skill_router.py" 2>>"$HARNESS_DIR/router/shim-errors.log" || true
exit 0

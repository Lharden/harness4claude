#!/usr/bin/env bash
# SessionStart: staleness do indice + warm ping do Ollama. Sem output, nunca bloqueia.
set -uo pipefail

# Opt-in explicito: sem HARNESS_ROUTER=1 nao ha router para aquecer, e rebuildar
# um indice de 424KB que ninguem consulta e desperdicio. Ver harness-skill-router.sh.
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
IDX="$HARNESS_DIR/skills-index"
BUILDER="$DIR/../scripts/build_skills_index.py"
mkdir -p "$IDX" 2>/dev/null || true
if ! "$PY" "$BUILDER" --check-stale >/dev/null 2>&1; then
  touch "$IDX/.stale" 2>/dev/null || true
  # background: se o filho morrer com o hook (MSYS), o marker .stale fica e o
  # rebuild e retomado no proximo SessionStart (indice velho continua servivel)
  ( "$PY" "$BUILDER" >/dev/null 2>&1 && rm -f "$IDX/.stale" ) &
fi
if command -v curl >/dev/null 2>&1; then
  ( curl -s -m 3 -X POST "${HARNESS_OLLAMA_URL:-http://127.0.0.1:11434}/api/embed" \
      -H "Content-Type: application/json" \
      -d '{"model":"'"${HARNESS_EMBED_MODEL:-nomic-embed-text-v2-moe}"'","input":["warmup"],"keep_alive":"30m"}' \
      >/dev/null 2>&1 ) &
fi
exit 0

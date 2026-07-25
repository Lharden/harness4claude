#!/usr/bin/env bash
# SessionStart: staleness do indice + warm ping do Ollama. Sem output, nunca bloqueia.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
PY="$(command -v python3 || command -v python || true)"
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
  ( curl -s -m 3 -X POST "${HARNESS_OLLAMA_URL:-http://localhost:11434}/api/embed" \
      -H "Content-Type: application/json" \
      -d '{"model":"nomic-embed-text-v2-moe","input":["warmup"],"keep_alive":"30m"}' \
      >/dev/null 2>&1 ) &
fi
exit 0

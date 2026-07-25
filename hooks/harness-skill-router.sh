#!/usr/bin/env bash
# Skill-router v3.3 — shim. Contrato: nunca falha, nunca bloqueia o prompt.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && exit 0
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR
mkdir -p "$HARNESS_DIR/router" 2>/dev/null || true
"$PY" "$DIR/skill_router.py" 2>>"$HARNESS_DIR/router/shim-errors.log" || true
exit 0

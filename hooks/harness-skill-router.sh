#!/usr/bin/env bash
# Skill-router v3.3 — shim. Contrato: nunca falha, nunca bloqueia o prompt.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && exit 0
mkdir -p "$HOME/.claude/harness/router" 2>/dev/null || true
"$PY" "$DIR/skill_router.py" 2>>"$HOME/.claude/harness/router/shim-errors.log" || true
exit 0

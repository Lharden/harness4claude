#!/usr/bin/env bash
# Context7 proactive trigger wrapper (versionado no Harness v3 SDD).
# Le JSON do UserPromptSubmit via stdin e delega para Python.
set -uo pipefail

# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HOOK_DIR}/context7-trigger.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
  exit 0
fi

if command -v python >/dev/null 2>&1; then
  PYTHON="$PY"
elif command -v py >/dev/null 2>&1; then
  PYTHON="py -3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  exit 0
fi

exec ${PYTHON} "${PY_SCRIPT}"

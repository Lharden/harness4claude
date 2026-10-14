#!/usr/bin/env bash
# Context7 proactive trigger wrapper (versionado no Harness v3 SDD).
# Le JSON do UserPromptSubmit via stdin e delega para Python.
set -uo pipefail

# >>> mh:preludio (DERIVADO de master-harness:mh/interpretador.py; nao edite: mh identidade semear --artefato preludio)
# Interpretador nomeado (master-harness). Sem marcador, `python` — e a queda fica registrada.
export PYTHONNOUSERSITE=1
_MH_CASA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}"
_MH_MARCA="$_MH_CASA/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND=""
    IFS= read -r _MH_CAND < "$_MH_MARCA" || true
    _MH_CAND="${_MH_CAND#$'\xef\xbb\xbf'}"
    _MH_CAND="${_MH_CAND%$'\r'}"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi
if [ "$PY" = "python" ] && [ -d "$_MH_CASA/batimentos" ]; then
    _MH_EU=${0##*[/\\]}
    printf '%s %s\n' "${EPOCHSECONDS:-0}" "$_MH_EU" >> "$_MH_CASA/batimentos/interpretador-queda" 2>/dev/null || true
fi
# <<< mh:preludio

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

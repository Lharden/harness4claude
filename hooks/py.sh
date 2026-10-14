#!/usr/bin/env bash
# py.sh — roda um hook Python do hooks.json com o interpretador nomeado.
# Uso no hooks.json: bash "${CLAUDE_PLUGIN_ROOT}/hooks/py.sh" <script.py> [args...]
#
# O hooks.json nao tem variavel de shell, entao `python` cru ali resolve pelo
# PATH do host. Em 2026-10-09 o App Installer recriou os aliases da Microsoft
# Store em WindowsApps antes do Python real: os 11 `.sh` (com o preludio de
# 347f801) seguiram funcionando, e as 8 entradas `python` cru — o portao
# transacional entre elas — falharam a cada evento com "Python was not found".
# Teste: tests/test_interpretador_nos_hooks_json.py.
#
# Sem fork alem do Python: `exec` entrega stdin, stdout e codigo de saida do
# script direto ao host.

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

# Diretorio deste arquivo por expansao de parametro (barra ou contrabarra).
_EU="${BASH_SOURCE[0]}"
_HOOK_DIR="${_EU%[/\\]*}"
[ "$_HOOK_DIR" = "$_EU" ] && _HOOK_DIR="."

_SCRIPT="$1"
shift
exec "$PY" "$_HOOK_DIR/$_SCRIPT" "$@"

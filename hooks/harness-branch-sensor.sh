#!/usr/bin/env bash
# harness-branch-sensor.sh — sensor passivo de ramificacao e deriva (Branch Keeper).
#
# Roda em dois eventos:
#   UserPromptSubmit — pega a tangente que o usuario joga na conversa
#   Stop             — pega a tangente que o proprio modelo levantou na resposta
#
# Contrato, igual aos outros oito hooks: nunca falha, nunca bloqueia o prompt.
# Ligado por padrao — ao contrario do skill-router, que exige Ollama para servir
# para alguma coisa. Aqui a Camada A (regex) funciona sozinha, entao um Ollama
# ausente degrada a deteccao em vez de anula-la.
set -uo pipefail

[ "${HARNESS_BRANCH:-1}" = "0" ] && exit 0

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

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

# Heartbeat: prova que o HOST ainda chama este evento. O smoke-test do
# health-check prova que o hook FUNCIONA quando executado; nao prova que
# continua sendo CHAMADO. Para o evento Stop isso importa mais que para os
# outros — ele e o mais novo do contrato e o primeiro a sumir numa mudanca
# de host, e a falha seria invisivel: nenhum ramo detectado parece igual a
# nenhum ramo existente.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/branch-sensor"; } 2>/dev/null || true

mkdir -p "$HARNESS_DIR" 2>/dev/null || true
"$PY" "$DIR/../scripts/branch_sensor.py" \
    2>>"$HARNESS_DIR/branch-sensor-errors.log" || true
exit 0

#!/usr/bin/env bash
# harness-roteamento.sh <PreToolUse|UserPromptSubmit> — filtro do roteamento de chips.
# Spec: docs/specs/roteamento-de-sessoes-spec-light.md
#
# O UserPromptSubmit dispara em TODO prompt; abrir Python em cada um custaria
# sem necessidade. Aqui so passa adiante o prompt que tem o marcador
# (`Roteamento:` ou `[roteamento]`). O PreToolUse ja vem filtrado pelo matcher
# do hooks.json (spawn_task), entao segue direto. HARNESS_ROTEAMENTO=0 desliga.
set -uo pipefail

[ "${HARNESS_ROTEAMENTO:-1}" = "0" ] && exit 0

EVENTO="${1:-UserPromptSubmit}"

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

# UTF-8 no Python filho, como os demais hooks (cp1252 no pipe recusava todo chip com "·"; medido 2026-09-30)
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG=C.UTF-8

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "${HOOK_DIR}/harness-roteamento.py" ] || exit 0

INPUT=$(cat)

if [ "$EVENTO" = "UserPromptSubmit" ]; then
    # `[roteamento-ok]` tambem passa o filtro; quem decide que nao e gatilho e o Python.
    case "$INPUT" in
        *Roteamento:*|*"[roteamento"*) ;;
        *) exit 0 ;;
    esac
fi

printf '%s' "$INPUT" | "$PY" "${HOOK_DIR}/harness-roteamento.py" --event "$EVENTO"
exit 0

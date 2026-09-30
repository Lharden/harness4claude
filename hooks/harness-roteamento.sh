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

# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi

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

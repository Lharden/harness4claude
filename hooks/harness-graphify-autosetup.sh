#!/usr/bin/env bash
# harness-graphify-autosetup.sh — SessionStart hook (Harness v3 / graphify)
#
# Quando uma sessao inicia DENTRO de um repo git que ainda NAO tem knowledge graph
# (graphify-out/graph.json ausente), este hook:
#   1) dispara o passe AST do graphify em background (graphify update — gratis, sem LLM);
#   2) injeta um additionalContext convidando o agente a rodar /graphify para o build
#      semantico completo.
# Roda no maximo UMA vez por repo (marker global), nunca bloqueia o session start e
# nunca falha a sessao (sempre exit 0).
#
# Escopo: "qualquer repo git que eu abrir" (decisao do usuario, 2026-06-18).

# Nunca deixar um erro abortar o SessionStart.

# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi
set +e

# ----------------------------------------------------------------------------
# 1. Descobrir o cwd da sessao (SessionStart entrega JSON no stdin com .cwd)
# ----------------------------------------------------------------------------
STDIN_JSON="$(cat 2>/dev/null)"
CWD=""
if command -v python >/dev/null 2>&1; then
    CWD="$(printf '%s' "$STDIN_JSON" | PYTHONUTF8=1 "$PY" -c \
"import sys,json
try:
    print(json.load(sys.stdin).get('cwd','') or '')
except Exception:
    print('')
" 2>/dev/null)"
fi
[ -z "$CWD" ] && CWD="$(pwd)"

# ----------------------------------------------------------------------------
# 2. So agir se o cwd estiver dentro de um repo git
# ----------------------------------------------------------------------------
command -v git >/dev/null 2>&1 || exit 0
REPO="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)"
[ -z "$REPO" ] && exit 0   # nao e repo git -> nada a fazer

# ----------------------------------------------------------------------------
# 3. Ja tem grafo? entao nada a fazer
# ----------------------------------------------------------------------------
[ -f "$REPO/graphify-out/graph.json" ] && exit 0

# ----------------------------------------------------------------------------
# 4. Marker global: nudge/AST apenas UMA vez por repo
# ----------------------------------------------------------------------------
: "${HARNESS_DIR:=$HOME/.claude/harness}"
MARKER_DIR="$HARNESS_DIR/graphify-autosetup"
mkdir -p "$MARKER_DIR" 2>/dev/null
SAFE="$(printf '%s' "$REPO" | tr '/\\: ' '____')"
MARKER="$MARKER_DIR/$SAFE"
[ -f "$MARKER" ] && exit 0
printf '%s\n' "$REPO" > "$MARKER" 2>/dev/null

# ----------------------------------------------------------------------------
# 5. Passe AST gratis em background (best-effort; nunca bloqueia)
#    graphify update <path> = re-extracao de codigo, sem LLM.
# ----------------------------------------------------------------------------
mkdir -p "$REPO/graphify-out" 2>/dev/null
REPO_ARG="$REPO"
command -v cygpath >/dev/null 2>&1 && REPO_ARG="$(cygpath -w "$REPO" 2>/dev/null || echo "$REPO")"
if command -v graphify >/dev/null 2>&1; then
    nohup graphify update "$REPO_ARG" \
        >"$REPO/graphify-out/.autosetup-ast.log" 2>&1 &
    disown 2>/dev/null
fi

# ----------------------------------------------------------------------------
# 6. Nudge: pedir ao agente o build semantico completo
# ----------------------------------------------------------------------------
REPO_NAME="$(basename "$REPO")"
MSG="GRAPHIFY AUTO-SETUP: o repo '$REPO_NAME' ($REPO) ainda nao tem knowledge graph. \
Um passe AST gratuito (graphify update, sem LLM) foi disparado em background para criar \
graphify-out/graph.json. Para o grafo semantico completo (conceitos, comunidades, god nodes), \
rode a skill /graphify neste diretorio quando fizer sentido. Se for um projeto so de codigo, \
o passe AST ja basta e nenhuma acao e necessaria."

if command -v python >/dev/null 2>&1; then
    PYTHONUTF8=1 MSG="$MSG" "$PY" -c \
"import json,os
print(json.dumps({'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':os.environ['MSG']}}))" 2>/dev/null
fi

exit 0

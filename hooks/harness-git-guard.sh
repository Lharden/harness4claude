#!/usr/bin/env bash
# harness-git-guard.sh — PreToolUse:Bash hook
# Protects against destructive git operations.
# Reads JSON from stdin (field: tool_input.command)
# Exit 2 = BLOCK, Exit 0 = PASS (with optional warning)

set -euo pipefail

# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi

: "${HARNESS_DIR:=$HOME/.claude/harness}"

# Heartbeat de disparo — ver harness-classify.sh para o porque.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/PreToolUse"; } 2>/dev/null || true

INPUT=$(cat)

# Extrai STATUS na primeira linha e o comando no resto. O status distingue
# "nao havia comando" de "o payload nao tem a forma que eu conheco" — colapsar
# os dois em string vazia era o bug: numa mudanca de schema do host o guard
# passava a devolver exit 0 para TUDO e parava de bloquear operacoes
# destrutivas, sem emitir um unico sinal. Guard que some em silencio e pior que
# guard ausente, porque voce conta com ele.
EXTRACT=$(printf '%s' "$INPUT" | "$PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
if not isinstance(d, dict):
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
ti = d.get('tool_input')
if not isinstance(ti, dict):
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
if 'command' not in ti:
    print('NO_COMMAND_KEY'); print(''); raise SystemExit(0)
print('OK')
print(ti.get('command') or '')
" 2>/dev/null || printf 'SHAPE_UNKNOWN\n\n')

# Split por expansao de parametro: comando pode ter multiplas linhas, e um pipe
# para head/tail morreria com SIGPIPE sob pipefail em comandos grandes.
STATUS="${EXTRACT%%$'\n'*}"
STATUS="${STATUS%$'\r'}"   # print() do Python no Windows emite \r\n
COMMAND="${EXTRACT#*$'\n'}"

# Payload em formato desconhecido: NUNCA bloquear (bloquear todo Bash da maquina
# numa mudanca de schema seria pior que o problema), mas nunca em silencio.
# Rate-limit de 1h: sem ele o aviso sairia em cada chamada de Bash e viraria
# ruido — e ruido vira alarme ignorado.
if [[ "$STATUS" != "OK" ]]; then
  MARKER="$HARNESS_DIR/.git-guard-blind"
  NOW=$(date +%s 2>/dev/null || echo 0)
  LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
  case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
  if [ "$NOW" -eq 0 ] || [ $((NOW - LAST)) -ge 3600 ]; then
    mkdir -p "$HARNESS_DIR" 2>/dev/null || true
    printf '%s\n' "$NOW" > "$MARKER" 2>/dev/null || true
    echo "## Harness Warning: git-guard nao reconheceu o payload do PreToolUse ($STATUS)."
    echo "   O bloqueio de operacoes git destrutivas esta INATIVO ate isto ser corrigido."
    echo "   Provavel mudanca no formato do hook pelo CLI host. Rode: bash scripts/health-check.sh"
  fi
  exit 0
fi

# Sem comando (payload valido, campo vazio): nada a inspecionar.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Parser compartilhado com Harness4Contract: separa invocacoes reais de texto
# citado e inspeciona cada segmento de uma cadeia shell.
POLICY_JSON=$("$PY" "$(dirname "${BASH_SOURCE[0]}")/../scripts/command_policy.py" \
  --command "$COMMAND" 2>/dev/null || printf '{"action":"unknown","reason":"parser failure"}')
POLICY_ACTION=$(printf '%s' "$POLICY_JSON" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('action','unknown'))" 2>/dev/null || echo unknown)
POLICY_REASON=$(printf '%s' "$POLICY_JSON" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null || echo "")
case "$POLICY_ACTION" in
  deny)
    printf '{"decision":"block","reason":"BLOCKED: %s"}\n' "$POLICY_REASON" >&2
    exit 2
    ;;
  require_approval)
    printf '{"decision":"block","reason":"APPROVAL REQUIRED: %s"}\n' "$POLICY_REASON" >&2
    exit 2
    ;;
  warn)
    printf '## Harness Warning: %s. Confirme com o usuario antes de prosseguir.\n' "$POLICY_REASON"
    exit 0
    ;;
  allow)
    exit 0
    ;;
  *)
    echo "## Harness Warning: command-policy nao conseguiu analisar o comando; trate como nao observado."
    exit 0
    ;;
esac

# --- HARD BLOCK (exit 2) ---

# git push --force / --force-with-lease
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force'; then
  echo '{"decision":"block","reason":"BLOCKED: git push --force e uma operacao destrutiva. Use git push sem --force."}' >&2
  exit 2
fi

# git reset --hard
if echo "$COMMAND" | grep -qE 'git\s+reset\s+.*--hard'; then
  echo '{"decision":"block","reason":"BLOCKED: git reset --hard descarta alteracoes irrecuperavelmente. Use git stash ou git reset --soft."}' >&2
  exit 2
fi

# git clean -f / -fd
if echo "$COMMAND" | grep -qE 'git\s+clean\s+.*-[a-zA-Z]*f'; then
  echo '{"decision":"block","reason":"BLOCKED: git clean -f remove arquivos nao-rastreados permanentemente."}' >&2
  exit 2
fi

# git branch -D (uppercase D only)
if echo "$COMMAND" | grep -qE 'git\s+branch\s+.*-[a-zA-Z]*D'; then
  echo '{"decision":"block","reason":"BLOCKED: git branch -D forca exclusao de branch sem verificar merge. Use git branch -d."}' >&2
  exit 2
fi

# git checkout .
if echo "$COMMAND" | grep -qE 'git\s+checkout\s+\.\s*$'; then
  echo '{"decision":"block","reason":"BLOCKED: git checkout . descarta todas as alteracoes locais nao commitadas."}' >&2
  exit 2
fi

# git restore .
if echo "$COMMAND" | grep -qE 'git\s+restore\s+\.\s*$'; then
  echo '{"decision":"block","reason":"BLOCKED: git restore . descarta todas as alteracoes locais nao commitadas."}' >&2
  exit 2
fi

# --- WARNING (exit 0, print message) ---

# git push (without --force, already filtered above)
if echo "$COMMAND" | grep -qE 'git\s+push(\s|$)'; then
  echo "## Harness Warning: git push detectado. Confirme com o usuario antes de prosseguir."
  exit 0
fi

# --- PASS ---
exit 0

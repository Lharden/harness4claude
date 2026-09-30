#!/usr/bin/env bash
# state-lock.sh — Lock cooperativo para ~/.claude/harness/state.json.
#
# Use mkdir (atomico em todos os FS comuns) como semaforo. Cada hook que
# le-modifica-escreve state.json deve:
#
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/state-lock.sh"
#   acquire_state_lock || exit 0
#   trap release_state_lock EXIT
#   # ... operacoes com state.json ...
#
# Stale-lock: se lockdir existe ha mais de STATE_LOCK_STALE_SECS, e
# considerado abandonado e e removido automaticamente.
#
# Variaveis de configuracao (export ANTES de source para customizar):
#   HARNESS_DIR              default: ~/.claude/harness
#   STATE_LOCK_TIMEOUT_SECS  default: 5      (max espera por lock)
#   STATE_LOCK_STALE_SECS    default: 30     (idade que considera stale)
#   STATE_LOCK_POLL_MS       default: 50     (intervalo de retry em ms)

: "${HARNESS_DIR:=$HOME/.claude/harness}"
: "${STATE_LOCK_TIMEOUT_SECS:=5}"
: "${STATE_LOCK_STALE_SECS:=30}"
: "${STATE_LOCK_POLL_MS:=50}"

STATE_LOCK_DIR="$HARNESS_DIR/state.json.lockdir"
STATE_LOCK_OWNER_FILE="$STATE_LOCK_DIR/owner"

# Relogio sem fork. No Git Bash do Windows cada `$(...)` custa 0,1-0,3s (medido:
# `$(date)` 0,28s, `$(:)` 0,13s, builtin ~0), e o laco de aquisicao forkava ~9
# vezes por volta: com N waiters a maquina entope, o holder e o handoff atrasam e
# o timeout estoura sem defeito de exclusao mutua. Por isso o laco so forka o
# que nao tem builtin (`mkdir`, `sleep`) e a checagem de stale (`stat`) roda no
# maximo 1x por segundo.
#
# Escreve em `_STATE_LOCK_NOW_MS` (milissegundos desde a epoca). Ordem:
# $EPOCHREALTIME (bash >= 5), printf %()T (bash >= 4.2, resolucao de 1s),
# `date` (bash antigo; unico caso que forka).
_state_lock_tick() {
  if [[ -n "${EPOCHREALTIME:-}" ]]; then
    local us="${EPOCHREALTIME/[.,]/}"
    _STATE_LOCK_NOW_MS=$(( 10#$us / 1000 ))
  elif printf -v _STATE_LOCK_NOW_MS '%(%s)T' -1 2>/dev/null; then
    _STATE_LOCK_NOW_MS=$(( _STATE_LOCK_NOW_MS * 1000 ))
  else
    _STATE_LOCK_NOW_MS=$(( $(date +%s) * 1000 ))
  fi
}

_state_lock_dir_age_secs() {
  local lockdir="$1"
  if [[ ! -d "$lockdir" ]]; then
    echo "-1"
    return
  fi
  local mtime
  if mtime=$(stat -c %Y "$lockdir" 2>/dev/null); then
    :
  elif mtime=$(stat -f %m "$lockdir" 2>/dev/null); then
    :
  else
    echo "-1"
    return
  fi
  _state_lock_tick
  echo $(( _STATE_LOCK_NOW_MS / 1000 - mtime ))
}

_state_lock_remove_if_stale() {
  local age
  age=$(_state_lock_dir_age_secs "$STATE_LOCK_DIR")
  if [[ "$age" -ge 0 && "$age" -ge "$STATE_LOCK_STALE_SECS" ]]; then
    rm -rf "$STATE_LOCK_DIR" 2>/dev/null || return 1
    return 0
  fi
  return 1
}

acquire_state_lock() {
  [[ -d "$HARNESS_DIR" ]] || mkdir -p "$HARNESS_DIR" 2>/dev/null
  _state_lock_tick
  local deadline_ms=$(( _STATE_LOCK_NOW_MS + STATE_LOCK_TIMEOUT_SECS * 1000 ))
  local next_stale_check_ms=0
  local poll_secs="0.${STATE_LOCK_POLL_MS}"

  while true; do
    if mkdir "$STATE_LOCK_DIR" 2>/dev/null; then
      _state_lock_tick
      printf '%s %s\n' "$$" "$(( _STATE_LOCK_NOW_MS / 1000 ))" > "$STATE_LOCK_OWNER_FILE" 2>/dev/null
      return 0
    fi
    _state_lock_tick
    if (( _STATE_LOCK_NOW_MS >= next_stale_check_ms )); then
      next_stale_check_ms=$(( _STATE_LOCK_NOW_MS + 1000 ))
      _state_lock_remove_if_stale && continue
      _state_lock_tick
    fi
    if (( _STATE_LOCK_NOW_MS >= deadline_ms )); then
      return 1
    fi
    sleep "$poll_secs" 2>/dev/null || sleep 1
  done
}

release_state_lock() {
  if [[ -f "$STATE_LOCK_OWNER_FILE" ]]; then
    local owner_pid=""
    read -r owner_pid _ < "$STATE_LOCK_OWNER_FILE" 2>/dev/null
    if [[ -n "$owner_pid" && "$owner_pid" != "$$" ]]; then
      return 0
    fi
  fi
  rm -rf "$STATE_LOCK_DIR" 2>/dev/null || true
}

# Entry-point CLI para uso em testes:
#   bash state-lock.sh acquire           -> 0 se conseguiu, 1 se timeout
#   bash state-lock.sh release           -> sempre 0
#   bash state-lock.sh is-locked         -> 0 se locked, 1 se livre
#   bash state-lock.sh age-secs          -> idade em segundos (-1 se nao existe)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    acquire)
      if acquire_state_lock; then
        exit 0
      else
        echo "[state-lock] timeout adquirindo lock apos ${STATE_LOCK_TIMEOUT_SECS}s" >&2
        exit 1
      fi
      ;;
    release)
      release_state_lock
      exit 0
      ;;
    is-locked)
      if [[ -d "$STATE_LOCK_DIR" ]]; then
        exit 0
      else
        exit 1
      fi
      ;;
    age-secs)
      _state_lock_dir_age_secs "$STATE_LOCK_DIR"
      exit 0
      ;;
    *)
      echo "uso: state-lock.sh {acquire|release|is-locked|age-secs}" >&2
      exit 2
      ;;
  esac
fi

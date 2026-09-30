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
# Dono: quem segura o lock tem, dentro do lockdir, um arquivo de nome unico
# (owner.<pid>.<ms>.<aleatorio>). Nenhuma remocao usa so o nome do lockdir:
# cada uma apaga as entradas que viu, pelo nome delas, e o lockdir com `rm -d`,
# que falha se ele nao estiver vazio. Lockdir vazio nao tem dono.
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
# Arquivo de dono da aquisicao deste shell; vazio enquanto ele nao segura o lock.
STATE_LOCK_OWNER_FILE=""

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

# Entradas do lockdir, em _STATE_LOCK_ENTRIES, sem fork. O glob roda com as
# opcoes de quem fez source: `set -f` o desligaria e `failglob` abortaria o laco
# no primeiro padrao sem casamento, entao as duas saem aqui e voltam no fim.
_state_lock_entries() {
  _STATE_LOCK_ENTRIES=()
  local f sem_glob=0 failglob=0
  if [[ $- == *f* ]]; then sem_glob=1; set +f; fi
  if shopt -q failglob; then failglob=1; shopt -u failglob; fi
  for f in "$STATE_LOCK_DIR"/* "$STATE_LOCK_DIR"/.[!.]* "$STATE_LOCK_DIR"/..?*; do
    if [[ -e "$f" || -L "$f" ]]; then _STATE_LOCK_ENTRIES+=("$f"); fi
  done
  if (( failglob )); then shopt -s failglob; fi
  if (( sem_glob )); then set -f; fi
  return 0
}

# Quebra o lock abandonado sem tocar em nenhum que tenha nascido depois.
#
# A idade e a remocao nao sao uma operacao so: entre as duas, o lockdir velho
# pode ter sido quebrado por outro waiter e pego por um terceiro. Ate
# 2026-09-30 a remocao era `rm -rf` no nome do lockdir e apagava o lock do
# terceiro (tests/test_state_lock.py::TestCorridaDaQuebraDeStale). Agora sai so
# o que foi VISTO, cada entrada pelo proprio nome, e o lockdir com `-d`: o dono
# de um lock novo tem um nome que ninguem viu, e o `rm -d` falha no lockdir dele.
#
# As entradas sao lidas ANTES da idade. Na ordem inversa, um lockdir trocado
# entre as duas leituras juntaria a idade do velho com o dono do novo.
_state_lock_remove_if_stale() {
  _state_lock_entries
  local age
  age=$(_state_lock_dir_age_secs "$STATE_LOCK_DIR")
  if [[ "$age" -ge 0 && "$age" -ge "$STATE_LOCK_STALE_SECS" ]]; then
    rm -df ${_STATE_LOCK_ENTRIES[@]+"${_STATE_LOCK_ENTRIES[@]}"} "$STATE_LOCK_DIR" 2>/dev/null
    return
  fi
  return 1
}

# Grava o dono no lockdir recem-criado e so fica com o lock se for o UNICO dono.
#
# Entre o `mkdir` e esta gravacao o lockdir esta vazio, e lockdir vazio pode
# sair a qualquer momento: o `rm -d` de uma quebra atrasada, ou do release de
# quem ja perdeu o lock, vale nele, porque vazio nao tem dono. Se o nosso saiu e
# outro processo criou outro, a gravacao cai no lockdir do outro. Quem grava le
# o lockdir depois de gravar; de dois que gravam no mesmo, pelo menos um ve o
# arquivo do outro, e esse remove so o proprio arquivo e tenta de novo.
_state_lock_claim() {
  local own="$STATE_LOCK_DIR/owner.$$.${_STATE_LOCK_NOW_MS}.${RANDOM}"
  if ! { printf '%s %s\n' "$$" "$(( _STATE_LOCK_NOW_MS / 1000 ))" > "$own"; } 2>/dev/null; then
    return 1
  fi
  _state_lock_entries
  local f meu=0 alheio=0
  for f in ${_STATE_LOCK_ENTRIES[@]+"${_STATE_LOCK_ENTRIES[@]}"}; do
    case "${f##*/}" in
      owner|owner.*)
        if [[ "${f##*/}" == "${own##*/}" ]]; then meu=1; else alheio=1; fi
        ;;
    esac
  done
  if (( meu && ! alheio )); then
    STATE_LOCK_OWNER_FILE="$own"
    return 0
  fi
  rm -df "$own" "$STATE_LOCK_DIR" 2>/dev/null
  return 1
}

acquire_state_lock() {
  [[ -d "$HARNESS_DIR" ]] || mkdir -p "$HARNESS_DIR" 2>/dev/null
  _state_lock_tick
  local deadline_ms=$(( _STATE_LOCK_NOW_MS + STATE_LOCK_TIMEOUT_SECS * 1000 ))
  local next_stale_check_ms=0
  # ms -> segundos decimais. "0.${MS}" lia 50 como 0.50 s e 5 como 0.5 s.
  local poll_secs
  printf -v poll_secs '%d.%03d' $(( STATE_LOCK_POLL_MS / 1000 )) $(( STATE_LOCK_POLL_MS % 1000 ))

  while true; do
    if mkdir "$STATE_LOCK_DIR" 2>/dev/null; then
      _state_lock_tick
      if _state_lock_claim; then
        return 0
      fi
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

# Solta o lock deste shell: remove o proprio arquivo de dono, pelo nome, e o
# lockdir so se ficar vazio. Quem perdeu o lock por prazo (outro o quebrou e
# entrou) nao acha mais o proprio arquivo, e o `rm -d` falha no lockdir do
# sucessor. Ate 2026-09-30 o release conferia o pid do `owner` e depois fazia
# `rm -rf` no nome, e uma quebra entre as duas coisas custava o lock do outro.
release_state_lock() {
  [[ -n "$STATE_LOCK_OWNER_FILE" ]] || return 0
  rm -df "$STATE_LOCK_OWNER_FILE" "$STATE_LOCK_DIR" 2>/dev/null || true
  STATE_LOCK_OWNER_FILE=""
}

# Entry-point CLI para uso em testes:
#   bash state-lock.sh acquire           -> 0 se conseguiu, 1 se timeout
#   bash state-lock.sh release           -> sempre 0 (so solta o que o proprio
#                                           processo adquiriu: no CLI, nada)
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

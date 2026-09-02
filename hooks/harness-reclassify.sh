#!/bin/bash
# harness-reclassify.sh — Conta arquivos, reclassifica L0→L1 se 3+
set -euo pipefail

# Resolve HARNESS_DIR ANTES do cygpath (INV-3): converter primeiro descartaria
# o override, pois cygpath operaria sobre o default.
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR

# Heartbeat de disparo — ver harness-classify.sh para o porque.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/PostToolUse"; } 2>/dev/null || true

# Convert paths for Python on Windows
if command -v cygpath &>/dev/null; then
    HARNESS_DIR_WIN=$(cygpath -w "$HARNESS_DIR")
else
    HARNESS_DIR_WIN="$HARNESS_DIR"
fi

# Read file path + cwd from stdin JSON via env var (safe from injection).
# Saida: linha 1 = session_id, linha 2 = cwd, linha 3 = file_path, linha 4 = tool_name.
# sem ele, editar arquivos num repo promovia a classificacao de outro (o
# contador global chegou a 130 arquivos misturando dois projetos).
INPUT=$(cat)
export PYTHONUTF8=1
EXTRACT=$(echo "$INPUT" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('session_id') or '').replace('\n', ' '))
    print((d.get('cwd') or '').replace('\n', ' '))
    print(d.get('tool_input',{}).get('file_path',''))
    print(d.get('tool_name') or d.get('toolName') or '')
except Exception:
    print('')
    print('')
    print('')
    print('')
" 2>/dev/null)

# Expansao de parametro em vez de pipe: `head`/`sed` fecham o pipe cedo e, com
# `set -o pipefail`, o produtor morre com SIGPIPE (exit 141). Ver harness-classify.sh.
SESSION_ID="${EXTRACT%%$'\n'*}"
SESSION_ID="${SESSION_ID%$'\r'}"
EXTRACT_REST="${EXTRACT#*$'\n'}"
SESSION_CWD="${EXTRACT_REST%%$'\n'*}"
SESSION_CWD="${SESSION_CWD%$'\r'}"   # print() do Python no Windows emite \r\n
EXTRACT_REST="${EXTRACT_REST#*$'\n'}"
FILE_PATH="${EXTRACT_REST%%$'\n'*}"
FILE_PATH="${FILE_PATH%%$'\n'*}"
FILE_PATH="${FILE_PATH%$'\r'}"
TOOL_NAME="${EXTRACT_REST#*$'\n'}"
TOOL_NAME="${TOOL_NAME%%$'\n'*}"
TOOL_NAME="${TOOL_NAME%$'\r'}"

# Acquire exclusive lock on state.json before any read/modify/write.
HOOK_DIR_REL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_LIB="${HOOK_DIR_REL}/../scripts/state-lock.sh"
if [[ -f "$LOCK_LIB" ]]; then
  # shellcheck source=../scripts/state-lock.sh
  source "$LOCK_LIB"
  if ! acquire_state_lock; then
    exit 0
  fi
  trap release_state_lock EXIT
fi

# Pass file_path via env var to avoid shell injection
# MSYS_NO_PATHCONV prevents Git Bash from mangling paths like /app/src → C:/Program Files/Git/app/src
export MSYS_NO_PATHCONV=1
export HARNESS_FILE_PATH="$FILE_PATH"
export HARNESS_TOOL_NAME="$TOOL_NAME"
export HARNESS_SESSION_CWD="$SESSION_CWD"
export HARNESS_SESSION_ID="$SESSION_ID"
SCRIPTS_DIR="${HOOK_DIR_REL}/../scripts"
if command -v cygpath &>/dev/null; then
    export HARNESS_SCRIPTS_DIR="$(cygpath -w "$SCRIPTS_DIR")"
else
    export HARNESS_SCRIPTS_DIR="$SCRIPTS_DIR"
fi

# All logic in single Python call to avoid path issues
python -c "
import json, os, sys

harness_dir = r'$HARNESS_DIR_WIN'
# Bucket do projeto; fallback para a raiz preserva o comportamento antigo se a
# resolucao falhar. Ver scripts/harness_paths.py.
try:
    sys.path.insert(0, os.environ['HARNESS_SCRIPTS_DIR'])
    from harness_paths import ensure_state_dir
    harness_dir = str(ensure_state_dir(harness_dir, os.environ.get('HARNESS_SESSION_CWD') or None,
                                       session_id=os.environ.get('HARNESS_SESSION_ID') or None))
except Exception:
    pass
state_file = os.path.join(harness_dir, 'state.json')
counter_file = os.path.join(harness_dir, '.session-files-count')
file_path = os.environ['HARNESS_FILE_PATH']
tool_name = os.environ.get('HARNESS_TOOL_NAME') or ''
if tool_name not in {'Edit', 'Write'}:
    raise SystemExit(0)
from post_tool_policy import counts_as_modified_file, touch_target
target = touch_target(tool_name, file_path)
if target is None:
    raise SystemExit(0)

# Read state
# Contrato PostToolUse: este hook só consome tool_input.file_path (contagem).
# Conteúdo de tool (tool_input.content / tool_response) NUNCA é classificado —
# texto de tool não é prompt (incidente 2026-06-12, t-20260612-034438).
state_task_id = ''
state_class = ''
state_status = ''
state = {}
try:
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    state_task_id = state.get('task_id') or ''
    state_class = state.get('classification') or ''
    state_status = state.get('status') or ''
except Exception:
    pass

# Every edit advances the transactional code revision. This invalidates any
# verification evidence recorded before the edit, even when the same file is
# modified more than once.
transaction_db = None
if state_task_id:
    try:
        from transactional_state import HarnessDatabase
        transaction_db = HarnessDatabase(harness_dir)
        transactional = transaction_db.touch_file(state_task_id, target)
        state.update({
            'status': transactional['status'],
            'current_step': transactional['phase'],
            'revision': transactional['revision'],
            'code_revision': transactional['code_revision'],
            'owner_epoch': transactional['owner_epoch'],
            'verified': transactional['verified'],
            'pending_gate': transactional['pending_gate'],
            'scope_id': transactional['scope_id'],
        })
        state_status = transactional['status']
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception:
        transaction_db = None

# Read/update counter
try:
    with open(counter_file, encoding='utf-8') as f:
        counter = json.load(f)
except Exception:
    counter = {'count': 0, 'files': [], 'task_id': None}

if counter.get('task_id') != state_task_id:
    counter = {'count': 0, 'files': [], 'task_id': state_task_id}

if counts_as_modified_file(tool_name, file_path) and file_path not in counter['files']:
    counter['files'].append(file_path)
    counter['count'] = len(counter['files'])

with open(counter_file, 'w', encoding='utf-8') as f:
    json.dump(counter, f, indent=2)

# Reclassify if L0 and 3+ files.
# Travas (PostToolUse jamais cria task nova — task_id é sempre preservado):
# - status=active: pipeline em andamento é intocável
# - confirmação semântica/humana explícita trava a classificação
from reclassification_policy import should_promote
if should_promote(state, counter['count']):
    try:
        with open(state_file, encoding='utf-8') as f:
            state = json.load(f)
        new_class = 'L1-feature'
        state['classification'] = new_class
        state['status'] = 'active'
        state['pipeline'] = ['write-spec-light', 'tdd', 'verify-against-spec']
        # Mantem classification_meta em sincronia com a promocao. A promocao e
        # uma decisao da camada regex (contagem de arquivos), entao espelha o
        # tratamento de L1+ do classify hook: final/agreed=None ate a
        # confirmacao semantica, para a task promovida nao parecer ja finalizada
        # quando record_signal.py tira o snapshot (fecha o loop de accuracy).
        meta = state.get('classification_meta') or {}
        meta['suggested'] = new_class
        meta['final'] = None
        meta['source'] = 'regex'
        meta['agreed'] = None
        state['classification_meta'] = meta
        if transaction_db is not None and state_task_id:
            transactional = transaction_db.reclassify(
                state_task_id,
                legacy_level=new_class,
                tier='L1',
                kind='feature',
                pipeline=state['pipeline'],
            )
            state.update({
                'status': transactional['status'],
                'current_step': transactional['phase'],
                'revision': transactional['revision'],
                'code_revision': transactional['code_revision'],
                'owner_epoch': transactional['owner_epoch'],
                'verified': transactional['verified'],
                'pending_gate': transactional['pending_gate'],
                'scope_id': transactional['scope_id'],
            })
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass
    print('<harness-reclassification>')
    print('  previous: L0')
    print('  new: L1')
    print('  reason: 3+ arquivos modificados na tarefa')
    print('  pipeline: write-spec-light -> tdd -> verify-against-spec')
    print('</harness-reclassification>')
" 2>/dev/null

exit 0

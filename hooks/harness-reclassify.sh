#!/bin/bash
# harness-reclassify.sh — Conta arquivos, reclassifica L0→L1 se 3+
set -euo pipefail

# Resolve HARNESS_DIR ANTES do cygpath (INV-3): converter primeiro descartaria
# o override, pois cygpath operaria sobre o default.
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR

# Convert paths for Python on Windows
if command -v cygpath &>/dev/null; then
    HARNESS_DIR_WIN=$(cygpath -w "$HARNESS_DIR")
else
    HARNESS_DIR_WIN="$HARNESS_DIR"
fi

# Read file path from stdin JSON via env var (safe from injection)
INPUT=$(cat)
export PYTHONUTF8=1
FILE_PATH=$(echo "$INPUT" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input',{}).get('file_path',''))
except Exception:
    print('')
" 2>/dev/null)

[ -z "$FILE_PATH" ] && exit 0

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

# All logic in single Python call to avoid path issues
python -c "
import json, os

harness_dir = r'$HARNESS_DIR_WIN'
state_file = os.path.join(harness_dir, 'state.json')
counter_file = os.path.join(harness_dir, '.session-files-count')
file_path = os.environ['HARNESS_FILE_PATH']

# Read state
# Contrato PostToolUse: este hook só consome tool_input.file_path (contagem).
# Conteúdo de tool (tool_input.content / tool_response) NUNCA é classificado —
# texto de tool não é prompt (incidente 2026-06-12, t-20260612-034438).
state_task_id = ''
state_class = ''
state_status = ''
meta_agreed = None
try:
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    state_task_id = state.get('task_id') or ''
    state_class = state.get('classification') or ''
    state_status = state.get('status') or ''
    meta_agreed = (state.get('classification_meta') or {}).get('agreed')
except Exception:
    pass

# Read/update counter
try:
    with open(counter_file, encoding='utf-8') as f:
        counter = json.load(f)
except Exception:
    counter = {'count': 0, 'files': [], 'task_id': None}

if counter.get('task_id') != state_task_id:
    counter = {'count': 0, 'files': [], 'task_id': state_task_id}

if file_path not in counter['files']:
    counter['files'].append(file_path)
    counter['count'] = len(counter['files'])

with open(counter_file, 'w', encoding='utf-8') as f:
    json.dump(counter, f, indent=2)

# Reclassify if L0 and 3+ files.
# Travas (PostToolUse jamais cria task nova — task_id é sempre preservado):
# - status=active: pipeline em andamento é intocável
# - agreed=True: classificação confirmada semanticamente está travada
if (counter['count'] >= 3 and state_class.startswith('L0')
        and state_status != 'active' and meta_agreed is not True):
    try:
        with open(state_file, encoding='utf-8') as f:
            state = json.load(f)
        new_class = state_class.replace('L0', 'L1')
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

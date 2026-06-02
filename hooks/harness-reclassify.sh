#!/bin/bash
# harness-reclassify.sh — Conta arquivos, reclassifica L0→L1 se 3+
set -euo pipefail

# Convert paths for Python on Windows
HARNESS_DIR="$HOME/.claude/harness"
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
state_task_id = ''
state_class = ''
try:
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    state_task_id = state.get('task_id') or ''
    state_class = state.get('classification') or ''
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

# Reclassify if L0 and 3+ files
if counter['count'] >= 3 and state_class.startswith('L0'):
    try:
        with open(state_file, encoding='utf-8') as f:
            state = json.load(f)
        state['classification'] = state_class.replace('L0', 'L1')
        state['status'] = 'active'
        state['pipeline'] = ['write-spec-light', 'tdd', 'verify-against-spec']
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

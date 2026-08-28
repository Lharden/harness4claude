#!/bin/bash
# harness-precompact.sh — Snapshot de handoff antes de compaction
set -euo pipefail

# Convert paths for Python on Windows (MSYS /c/... → C:\...)
# Parametrizavel via env: testes isolam com HARNESS_DIR temporario (default = producao)
HARNESS_DIR="${HARNESS_DIR:-$HOME/.claude/harness}"
export HARNESS_DIR

# Heartbeat de disparo — ver harness-classify.sh para o porque.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/PreCompact"; } 2>/dev/null || true

HOOK_DIR_REL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v cygpath &>/dev/null; then
    HARNESS_ROOT_WIN=$(cygpath -w "$HARNESS_DIR")
    SCRIPTS_DIR_WIN=$(cygpath -w "${HOOK_DIR_REL}/../scripts")
else
    HARNESS_ROOT_WIN="$HARNESS_DIR"
    SCRIPTS_DIR_WIN="${HOOK_DIR_REL}/../scripts"
fi

# O snapshot descreve a TAREFA corrente, entao vive no bucket do projeto — um
# trace unico da maquina intercalava sessoes de repos diferentes. Fallback para
# a raiz mantem o comportamento antigo se a resolucao falhar.
# `-t 0` obrigatorio: sem stdin de pipe (execucao manual, teste) um `cat` puro
# bloquearia o hook para sempre.
INPUT=""
if [ ! -t 0 ]; then INPUT="$(cat 2>/dev/null || true)"; fi
export PYTHONUTF8=1
export HARNESS_ROOT_WIN SCRIPTS_DIR_WIN
STATE_DIR="$(printf '%s' "$INPUT" | python -c "
import sys, json, os
sys.path.insert(0, os.environ['SCRIPTS_DIR_WIN'])
root = os.environ['HARNESS_ROOT_WIN']
try:
    payload = json.load(sys.stdin)
    cwd = payload.get('cwd') or ''
    session_id = payload.get('session_id') or ''
except Exception:
    cwd = ''
    session_id = ''
try:
    from harness_paths import ensure_state_dir
    print(ensure_state_dir(root, cwd or None, session_id=session_id or None))
except Exception:
    print(root)
" 2>/dev/null || printf '%s' "$HARNESS_DIR")"
STATE_DIR="${STATE_DIR%$'\r'}"   # $() tira \n final, mas nao o \r do Windows
[ -z "$STATE_DIR" ] && STATE_DIR="$HARNESS_DIR"

STATE_FILE="$STATE_DIR/state.json"
TRACE_FILE="$STATE_DIR/trace-current.md"
COUNTER_FILE="$STATE_DIR/.session-files-count"
TRACES_DIR="$STATE_DIR/traces"
HARNESS_DIR_WIN="$STATE_DIR"
mkdir -p "$STATE_DIR" 2>/dev/null || true
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Rotacionar se trace > 50KB
if [ -f "$TRACE_FILE" ]; then
    SIZE=$(wc -c < "$TRACE_FILE" 2>/dev/null | tr -d '[:space:]' || echo 0)
    if [ "$SIZE" -gt 51200 ]; then
        ROTATION_NAME="$(date '+%Y-%m-%d-%H%M').md"
        mkdir -p "$TRACES_DIR"
        mv "$TRACE_FILE" "$TRACES_DIR/$ROTATION_NAME" 2>/dev/null || true
        echo "# Harness v3 Trace — rotacionado em $TIMESTAMP" > "$TRACE_FILE"
    fi
fi

# Read state + counter via single Python call (avoids path issues)
export PYTHONUTF8=1
read_result=$(python -c "
import json, os

harness_dir = r'$HARNESS_DIR_WIN'
state_file = os.path.join(harness_dir, 'state.json')
counter_file = os.path.join(harness_dir, '.session-files-count')

task_id = 'none'
classification = 'unknown'
current_step = 'none'
pipeline = 'none'
artifacts = 'none'
files_count = 0

try:
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    task_id = state.get('task_id', 'none') or 'none'
    classification = state.get('classification', 'unknown') or 'unknown'
    current_step = str(state.get('current_step', 'none') or 'none')
    pipeline = ' -> '.join(state.get('pipeline', [])) or 'none'
    artifacts = ', '.join(state.get('artifacts_so_far', [])) or 'none'
except:
    pass

try:
    with open(counter_file, encoding='utf-8') as f:
        counter = json.load(f)
    files_count = counter.get('count', 0)
except:
    pass

print(f'{task_id}|{classification}|{current_step}|{pipeline}|{artifacts}|{files_count}')
" 2>/dev/null || echo "none|unknown|none|none|none|0")

IFS='|' read -r TASK_ID CLASSIFICATION CURRENT_STEP PIPELINE ARTIFACTS FILES_COUNT <<< "$read_result"

# Append snapshot
cat >> "$TRACE_FILE" << EOF

## [SNAPSHOT] $TIMESTAMP
- Task ID: $TASK_ID
- Classificacao: $CLASSIFICATION
- Pipeline: $CURRENT_STEP de ($PIPELINE)
- Artefatos: $ARTIFACTS
- Arquivos modificados: $FILES_COUNT
EOF

# Auto-sync para o vault Obsidian (E3) — non-blocking; nunca quebra o handoff.
# Espelha traces/specs/.remember para o vault. Se vault ausente, sai 0 (graceful).
PLUGIN_DIR="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VAULT_SYNC="$PLUGIN_DIR/scripts/vault_sync.py"
if [ -f "$VAULT_SYNC" ]; then
    python "$VAULT_SYNC" --quiet --harness-dir "$HARNESS_DIR_WIN" >/dev/null 2>&1 || true
fi

exit 0

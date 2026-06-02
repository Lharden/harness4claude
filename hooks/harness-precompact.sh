#!/bin/bash
# harness-precompact.sh — Snapshot de handoff antes de compaction
set -euo pipefail

# Convert paths for Python on Windows (MSYS /c/... → C:\...)
HARNESS_DIR="$HOME/.claude/harness"
if command -v cygpath &>/dev/null; then
    HARNESS_DIR_WIN=$(cygpath -w "$HARNESS_DIR")
else
    HARNESS_DIR_WIN="$HARNESS_DIR"
fi

STATE_FILE="$HARNESS_DIR/state.json"
TRACE_FILE="$HARNESS_DIR/trace-current.md"
COUNTER_FILE="$HARNESS_DIR/.session-files-count"
TRACES_DIR="$HARNESS_DIR/traces"
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
    python "$VAULT_SYNC" --quiet >/dev/null 2>&1 || true
fi

exit 0

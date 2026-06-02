#!/usr/bin/env bash
# harness-session-start.sh — SessionStart hook for Harness v3
# Checks for active pipeline and emits systemMessage to resume.

set -euo pipefail

# ============================================================================
# Bootstrap: create state directory and files on first run
# ============================================================================
HARNESS_DIR="$HOME/.claude/harness"
mkdir -p "$HARNESS_DIR"

if [ ! -f "$HARNESS_DIR/state.json" ]; then
    cat > "$HARNESS_DIR/state.json" << 'INITEOF'
{
  "task_id": null,
  "schema_version": 3,
  "classification": null,
  "status": "idle",
  "pipeline": [],
  "current_step": null,
  "artifacts_so_far": [],
  "started_at": null
}
INITEOF
fi

if [ ! -f "$HARNESS_DIR/signals.json" ]; then
    cat > "$HARNESS_DIR/signals.json" << 'INITEOF'
{
  "version": 3,
  "harness_version": "v3",
  "tasks": [],
  "aggregates": {
    "total_tasks": 0,
    "l0_count": 0,
    "l1_count": 0,
    "l2_count": 0,
    "pipeline_completion_rate": 0,
    "avg_files_per_task": 0,
    "sdd_usage": {
      "specs_generated": 0,
      "spec_lights_generated": 0,
      "designs_generated": 0,
      "verifications_passed": 0,
      "verifications_failed": 0,
      "clarifications_resolved": 0
    },
    "classify": {
      "total_classified": 0,
      "avg_classify_accuracy": null,
      "regex_vs_semantic_agreement": null,
      "human_override_count": 0
    }
  }
}
INITEOF
fi

if [ ! -f "$HARNESS_DIR/.session-files-count" ]; then
    echo '{"count": 0, "files": [], "task_id": null}' > "$HARNESS_DIR/.session-files-count"
fi

# ============================================================================
# Auto-migrate: upgrade pre-v3.1 state/signals to schema v3 (multi-machine safe)
# Installs criados pelo v3.0 carregam signals.json v2 que o bootstrap acima nao
# toca (so cria quando ausente). A migracao e idempotente; gate por versao para
# so rodar (e gerar .bak) quando algo esta de fato abaixo de v3.
# ============================================================================
if command -v cygpath &>/dev/null; then
    HARNESS_DIR_PY="$(cygpath -w "$HARNESS_DIR")"
else
    HARNESS_DIR_PY="$HARNESS_DIR"
fi
PLUGIN_DIR="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
MIGRATE_PY="$PLUGIN_DIR/scripts/migrate_state.py"
if [ -f "$MIGRATE_PY" ] && command -v python >/dev/null 2>&1; then
    export PYTHONUTF8=1
    export HARNESS_DIR_PY
    NEEDS_MIGRATE=$(python -c "
import json, os
d = os.environ['HARNESS_DIR_PY']
need = '0'
for name, key in (('state.json', 'schema_version'), ('signals.json', 'version')):
    try:
        with open(os.path.join(d, name), encoding='utf-8') as f:
            if (json.load(f).get(key) or 0) < 3:
                need = '1'
    except Exception:
        pass
print(need)
" 2>/dev/null)
    if [ "$NEEDS_MIGRATE" = "1" ]; then
        python "$MIGRATE_PY" >/dev/null 2>&1 || true
    fi
fi

# Dep check (first run only)
BOOTSTRAP_FLAG="$HARNESS_DIR/.bootstrap-done"
if [ ! -f "$BOOTSTRAP_FLAG" ]; then
    MISSING=""
    command -v python >/dev/null 2>&1 || MISSING="$MISSING python"
    command -v jq >/dev/null 2>&1 || MISSING="$MISSING jq"
    if [ -n "$MISSING" ]; then
        echo "Harness v3: dependencias faltando:$MISSING" >&2
    fi
    if command -v pip >/dev/null 2>&1; then
        PLUGIN_DIR="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
        if [ -f "$PLUGIN_DIR/requirements.txt" ]; then
            pip install --user -q -r "$PLUGIN_DIR/requirements.txt" 2>/dev/null || true
        fi
    fi
    touch "$BOOTSTRAP_FLAG"
fi

STATE_FILE="$HARNESS_DIR/state.json"

[ ! -f "$STATE_FILE" ] && exit 0

# Convert path for Python on Windows
if command -v cygpath &>/dev/null; then
    STATE_FILE_PY="$(cygpath -w "$STATE_FILE")"
else
    STATE_FILE_PY="$STATE_FILE"
fi

export PYTHONUTF8=1
python -c "
import json, sys
try:
    with open(r'$STATE_FILE_PY') as f:
        state = json.load(f)
    if state.get('status') == 'active' and state.get('pipeline'):
        tid = state.get('task_id', 'unknown')
        cls = state.get('classification', 'unknown')
        step = state.get('current_step') or (state['pipeline'][0] if state['pipeline'] else 'none')
        pipe = ' -> '.join(state['pipeline'])
        msg = json.dumps({
            'systemMessage': (
                f'HARNESS v3 RESUMING: Active pipeline {cls} (task {tid}). '
                f'Current step: {step}. Pipeline: {pipe}. '
                f'Invoke harness-workflow skill to continue where you left off.'
            )
        })
        print(msg)
except Exception:
    pass
" 2>/dev/null

exit 0

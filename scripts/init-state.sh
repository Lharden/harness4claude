#!/bin/bash
# init-state.sh — Inicializa $HOME/.claude/harness/ com state files defaults
# Uso: bash scripts/init-state.sh
# Idempotente — seguro rodar múltiplas vezes

set -euo pipefail

: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR
mkdir -p "$HARNESS_DIR"

if [ ! -f "$HARNESS_DIR/state.json" ]; then
    cat > "$HARNESS_DIR/state.json" << 'EOF'
{
  "task_id": null,
  "classification": null,
  "status": "idle",
  "pipeline": [],
  "current_step": null,
  "artifacts_so_far": [],
  "started_at": null
}
EOF
    echo "Created: state.json"
fi

if [ ! -f "$HARNESS_DIR/signals.json" ]; then
    cat > "$HARNESS_DIR/signals.json" << 'EOF'
{
  "version": 3,
  "harness_version": "v3",
  "tasks": [],
  "aggregates": {
    "total_tasks": 0,
    "pipeline_completion_rate": 0,
    "avg_files_per_task": 0,
    "sdd_usage": {
      "specs_generated": 0,
      "spec_lights_generated": 0,
      "designs_generated": 0,
      "verifications_passed": 0,
      "verifications_failed": 0,
      "clarifications_resolved": 0
    }
  }
}
EOF
    echo "Created: signals.json"
fi

if [ ! -f "$HARNESS_DIR/.session-files-count" ]; then
    echo '{"count": 0, "files": [], "task_id": null}' > "$HARNESS_DIR/.session-files-count"
    echo "Created: .session-files-count"
fi

echo "Harness v3 state initialized at: $HARNESS_DIR"

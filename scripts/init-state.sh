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

# signals.json nasce sob o `_Lock` dos escritores em Python, nunca com `cat >`:
# entre a abertura e a escrita do `cat` o arquivo existia vazio, e um
# record_signal concorrente perdia a task (tests/test_signals_criacao.py).
# O script imprime "Created: signals.json" quando cria.
if [ ! -f "$HARNESS_DIR/signals.json" ]; then
    SIG_DIR="$HARNESS_DIR"
    command -v cygpath >/dev/null 2>&1 && SIG_DIR="$(cygpath -w "$HARNESS_DIR")"
    python "$(dirname "$0")/migrate_state.py" --cria-signals --harness-dir "$SIG_DIR"
fi

if [ ! -f "$HARNESS_DIR/.session-files-count" ]; then
    echo '{"count": 0, "files": [], "task_id": null}' > "$HARNESS_DIR/.session-files-count"
    echo "Created: .session-files-count"
fi

echo "Harness v3 state initialized at: $HARNESS_DIR"

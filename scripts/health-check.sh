#!/bin/bash
# Harness v3 Health Check
# Verifica se todas as dependencias e artefatos do harness estao em estado saudavel.
# Uso: bash ~/.claude/harness/health-check.sh

set -u

# Resolve plugin root (works as plugin and standalone)
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    PLUGIN_DIR="$CLAUDE_PLUGIN_ROOT"
else
    PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi

# REQ-F11: o diagnostico segue HARNESS_DIR, para permitir inspecionar ambientes
# isolados (store em shadow, canarios) sem editar este script.
#
# ATENCAO (REQ-NF5, handoff para P-1.a): o futuro bloco de PROVENIENCIA — que
# compara cache do plugin x git HEAD x marketplace — deve inspecionar sempre o
# cache real, IGNORANDO HARNESS_DIR. Ele responde "qual codigo roda", nao "qual
# estado"; as duas perguntas nao compartilham variavel.
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR
HOOKS_DIR="$PLUGIN_DIR/hooks"
SKILLS_DIR="$PLUGIN_DIR/skills"

echo "Inspecionando: $HARNESS_DIR"
if [ "$HARNESS_DIR" != "$HOME/.claude/harness" ]; then
    echo "WARN: HARNESS_DIR override ativo (default: $HOME/.claude/harness)"
fi

EXIT_CODE=0

check() {
    local name="$1"
    local test_cmd="$2"
    if eval "$test_cmd" >/dev/null 2>&1; then
        echo "[OK]     $name"
    else
        echo "[FAIL]   $name"
        EXIT_CODE=1
    fi
}

warn() {
    echo "[WARN]   $1"
}

echo "=== Harness v3 Health Check ==="
echo ""

echo "--- Dependencies ---"
check "jq"               "command -v jq"
check "python"           "command -v python"
check "pytest"           "python -m pytest --version"
check "ruff"             "command -v ruff"
check "pyright"          "command -v pyright"
if command -v graphify >/dev/null 2>&1; then
    echo "[OK]     graphify (knowledge graph CLI)"
else
    warn "graphify ausente (opcional) — instalar: bash $PLUGIN_DIR/scripts/setup-graphify.sh"
fi
echo ""

echo "--- Harness state ---"
check "state.json"                 "test -f '$HARNESS_DIR/state.json'"
check "signals.json"               "test -f '$HARNESS_DIR/signals.json'"
check "state.json is valid JSON"   "python -c 'import json,sys; json.load(open(sys.argv[1], encoding=\"utf-8\"))' \"$HARNESS_DIR/state.json\""
check "signals.json is valid JSON" "python -c 'import json,sys; json.load(open(sys.argv[1], encoding=\"utf-8\"))' \"$HARNESS_DIR/signals.json\""
echo ""

echo "--- Hooks ---"
check "harness-classify.sh"      "test -f '$HOOKS_DIR/harness-classify.sh'"
check "harness-git-guard.sh"     "test -f '$HOOKS_DIR/harness-git-guard.sh'"
check "harness-precompact.sh"    "test -f '$HOOKS_DIR/harness-precompact.sh'"
check "harness-reclassify.sh"    "test -f '$HOOKS_DIR/harness-reclassify.sh'"
check "harness-session-start.sh" "test -f '$HOOKS_DIR/harness-session-start.sh'"
echo ""

echo "--- Skills ---"
check "harness-workflow skill"   "test -f '$SKILLS_DIR/harness-workflow/SKILL.md'"
check "discuss skill"            "test -f '$SKILLS_DIR/discuss/SKILL.md'"
check "validate-plan skill"      "test -f '$SKILLS_DIR/validate-plan/SKILL.md'"
echo ""

echo "--- SDD Skills (v3) ---"
if [ -d "$SKILLS_DIR/write-spec" ]; then
    check "write-spec skill"          "test -f '$SKILLS_DIR/write-spec/SKILL.md'"
    check "write-spec-light skill"    "test -f '$SKILLS_DIR/write-spec-light/SKILL.md'"
    check "design-doc skill"          "test -f '$SKILLS_DIR/design-doc/SKILL.md'"
    check "verify-against-spec skill" "test -f '$SKILLS_DIR/verify-against-spec/SKILL.md'"
else
    warn "SDD skills not yet installed (Phase 2-5 pending)"
fi
echo ""

echo "--- v3.1 Workflow + Schemas + Scripts ---"
check "state.schema.json"        "test -f '$PLUGIN_DIR/schemas/state.schema.json'"
check "signals.schema.json"      "test -f '$PLUGIN_DIR/schemas/signals.schema.json'"
check "migrate_state.py"         "test -f '$PLUGIN_DIR/scripts/migrate_state.py'"
check "record_signal.py"         "test -f '$PLUGIN_DIR/scripts/record_signal.py'"
check "vault_sync.py"            "test -f '$PLUGIN_DIR/scripts/vault_sync.py'"
check "wf-verify-multimodel.js"  "test -f '$PLUGIN_DIR/scripts/workflows/wf-verify-multimodel.js'"
check "wf-context-scan.js"       "test -f '$PLUGIN_DIR/scripts/workflows/wf-context-scan.js'"
check "signals.version == 3"     "python -c \"import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); sys.exit(0 if d.get('version')==3 else 1)\" \"$HARNESS_DIR/signals.json\""
if command -v node >/dev/null 2>&1; then
    check "Workflows validam (node)" "node '$PLUGIN_DIR/scripts/workflows/validate_workflows.cjs'"
else
    warn "node ausente — validacao de Workflows pulada"
fi
echo ""

echo "--- Orphaned artifacts ---"
# Check for ralph-loop orphan in current directory (project-scoped)
if [ -f ".claude/ralph-loop.local.md" ]; then
    echo "[FAIL]   ralph-loop orphan state present (.claude/ralph-loop.local.md)"
    EXIT_CODE=1
else
    echo "[OK]     No orphan ralph-loop state in current dir"
fi

# Check for stale active task in state.json
STALE_CHECK=$(python -c "
import json, os, sys
from datetime import datetime, timezone, timedelta
try:
    path = os.path.join(os.environ['HARNESS_DIR'], 'state.json')
    with open(path, encoding='utf-8') as f:
        s = json.load(f)
    if s.get('status') == 'active' and s.get('started_at'):
        ts = s['started_at'].replace('Z', '+00:00')
        started = datetime.fromisoformat(ts)
        age = datetime.now(timezone.utc) - started
        if age > timedelta(hours=24):
            print(f'stale:{age.days}d')
            sys.exit(0)
    print('ok')
except Exception:
    print('ok')
" 2>/dev/null)

if [[ "$STALE_CHECK" == stale:* ]]; then
    warn "state.json has active task older than 24h: $STALE_CHECK (abandoned?)"
else
    echo "[OK]     No stale active tasks"
fi
echo ""

echo "--- Obsidian integration ---"
# WARN-only: o harness funciona sem Obsidian (vault_sync degrada graceful). Estes
# checks reaproveitam tools/vault_sync_doctor.py (sem duplicar logica) e NUNCA
# falham o health-check — Obsidian aberto nao e pre-requisito do harness.
VAULT_ROOT="${VAULT_PATH:-$HOME/Documents/Obsidian Vault}"
if [ ! -d "$VAULT_ROOT" ]; then
    warn "vault ausente em $VAULT_ROOT (defina VAULT_PATH) — integracao Obsidian opcional"
elif [ -z "${OBSIDIAN_API_KEY:-}" ]; then
    warn "OBSIDIAN_API_KEY nao exportada — MCP 'obsidian' (REST) ficara sem auth"
elif [ ! -d "$PLUGIN_DIR/tools" ]; then
    warn "tools/ ausente (PR de tooling nao mergeado?) — doctor indisponivel"
elif ( cd "$PLUGIN_DIR" && python -m tools.vault_sync_doctor --root "$VAULT_ROOT" --check-rest >/dev/null 2>&1 ); then
    echo "[OK]     Obsidian doctor ready (plugins + REST API 127.0.0.1:27124)"
else
    warn "Obsidian doctor nao-ready (app fechado / REST off?) — rode: (cd '$PLUGIN_DIR' && python -m tools.vault_sync_doctor --root \"\$VAULT_PATH\" --check-rest)"
fi
echo ""

echo "--- Skill Router (opcional) ---"
IDX="$HARNESS_DIR/skills-index"
if [ -f "$IDX/meta.json" ]; then
    echo "[OK]     skills-index presente"
    [ -f "$IDX/.stale" ] && warn "skills-index stale (rebuild pendente)"
else
    warn "skills-index ausente — rode: python scripts/build_skills_index.py"
fi
if command -v curl >/dev/null 2>&1 && curl -s -m 2 "${HARNESS_OLLAMA_URL:-http://localhost:11434}/api/tags" 2>/dev/null | grep -q "${HARNESS_EMBED_MODEL:-nomic-embed-text-v2-moe}"; then
    echo "[OK]     Ollama + ${HARNESS_EMBED_MODEL:-nomic-embed-text-v2-moe}"
else
    warn "Ollama/modelo indisponivel — router degrada p/ camada A"
fi
echo ""

echo "--- Plugin load (Claude Code) ---"
# Plugin em disco != plugin CARREGADO. Sem o marketplace 'local' registrado, o
# sufixo @local nao resolve e os hooks nunca disparam (falha silenciosa). Esta
# checagem detecta isso — a lacuna que escondeu o plugin por todo o pos-format.
if command -v claude >/dev/null 2>&1; then
    if claude plugin list 2>/dev/null | grep -qa "harness4claude"; then
        echo "[OK]     harness4claude carregado no Claude Code (marketplace local)"
    else
        echo "[FAIL]   harness4claude NAO carregado — hooks inativos. Corrija com:"
        echo "         claude plugin marketplace add ~/.claude/plugins/local"
        echo "         claude plugin install harness4claude@local   (e reinicie)"
        EXIT_CODE=1
    fi
else
    warn "claude CLI ausente no PATH — nao foi possivel verificar carga do plugin"
fi

echo ""
echo "--- Proveniencia (qual codigo esta rodando) ---"
# ADR-000: o objeto-de-medicao e o plugin CARREGADO, nao o clone de trabalho.
# Este bloco responde "qual codigo roda" — pergunta distinta de "qual estado",
# entao ele IGNORA HARNESS_DIR de proposito (REQ-NF5). Sem isto, uma divergencia
# entre cache e main faz diagnosticos apontarem para o codigo errado.
PLUGIN_VERSION="$(python -c "
import json,sys
try:
    d=json.load(open(sys.argv[1],encoding='utf-8'))
    print(d.get('version','?'))
except Exception:
    print('?')
" "$PLUGIN_DIR/.claude-plugin/plugin.json" 2>/dev/null || echo "?")"

INSTALLED_VERSION="$(python -c "
import json,os,sys
p=os.path.join(os.path.expanduser('~'),'.claude','plugins','installed_plugins.json')
try:
    d=json.load(open(p,encoding='utf-8'))
    for k,v in d.get('plugins',{}).items():
        if k.startswith('harness4claude') and v:
            print(v[0].get('version','?')); break
    else:
        print('nao-instalado')
except Exception:
    print('?')
" 2>/dev/null || echo "?")"

echo "         plugin.json (arvore atual): $PLUGIN_VERSION"
echo "         instalado (Claude Code):    $INSTALLED_VERSION"

if [ "$INSTALLED_VERSION" = "nao-instalado" ]; then
    warn "plugin nao consta em installed_plugins.json"
elif [ "$PLUGIN_VERSION" != "$INSTALLED_VERSION" ]; then
    warn "DIVERGENCIA de versao: a arvore local esta em $PLUGIN_VERSION mas o Claude Code carrega $INSTALLED_VERSION."
    echo "         O codigo que roda NAO e o que voce esta editando."
    echo "         Corrija com: git push && /plugin update harness4claude"
else
    echo "[OK]     versao coerente entre arvore local e plugin carregado"
fi

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "=== All checks passed ==="
else
    echo "=== Some checks failed (see above) ==="
fi

exit $EXIT_CODE

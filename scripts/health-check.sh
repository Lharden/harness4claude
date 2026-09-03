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
# python e a UNICA dependencia dura: todo hook parseia JSON com `python -c`
# inline. A auditoria 2026-07-28 nao encontrou uma unica invocacao de jq em
# hooks/ ou scripts/ — era um gate FAIL sobre algo que nada usa. jq/ruff/pyright
# viraram WARN: sao ferramentas de inspecao e de lint, nao do caminho de execucao.
check "python"           "command -v python"
check "pytest"           "python -m pytest --version"
command -v jq      >/dev/null 2>&1 || warn "jq ausente (opcional — nenhum hook o usa; util para inspecionar signals.json)"
command -v ruff    >/dev/null 2>&1 || warn "ruff ausente (opcional — lint da suite)"
command -v pyright >/dev/null 2>&1 || warn "pyright ausente (opcional — type check da suite)"
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

echo "--- Hooks (presenca) ---"
check "harness-classify.sh"      "test -f '$HOOKS_DIR/harness-classify.sh'"
check "harness-git-guard.sh"     "test -f '$HOOKS_DIR/harness-git-guard.sh'"
check "harness-precompact.sh"    "test -f '$HOOKS_DIR/harness-precompact.sh'"
check "harness-reclassify.sh"    "test -f '$HOOKS_DIR/harness-reclassify.sh'"
check "harness-session-start.sh" "test -f '$HOOKS_DIR/harness-session-start.sh'"
check "harness-branch-sensor.sh" "test -f '$HOOKS_DIR/harness-branch-sensor.sh'"
echo ""

# ===========================================================================
# Smoke-test: EXECUTA cada hook com payload sintetico
# ===========================================================================
# Ate 2026-07-29 esta secao so conferia que os arquivos existiam em disco. Um
# hook presente e inerte — por dependencia quebrada, por mudanca no formato do
# payload pelo CLI host — passava como [OK]. E exatamente o defeito que originou
# esta auditoria (codigo presente != codigo rodando), um nivel acima.
#
# Roda tudo num HARNESS_DIR temporario: o smoke-test NUNCA toca o estado real.
echo "--- Hooks (execucao) ---"
SMOKE_DIR="$(mktemp -d 2>/dev/null || echo "")"
if [ -z "$SMOKE_DIR" ] || [ ! -d "$SMOKE_DIR" ]; then
    warn "nao foi possivel criar dir temporario — smoke-test pulado"
else
    SMOKE_REPO="$SMOKE_DIR/repo"
    mkdir -p "$SMOKE_REPO/.git"
    if command -v cygpath >/dev/null 2>&1; then
        SMOKE_CWD="$(cygpath -w "$SMOKE_REPO")"
    else
        SMOKE_CWD="$SMOKE_REPO"
    fi

    # Cada caso: nome | hook | payload | exit esperado | trecho exigido no stdout
    smoke() {
        _name="$1"; _hook="$2"; _payload="$3"; _want_code="$4"; _want_out="${5:-}"
        # AI_BRAIN_PATH aponta para um vault VAZIO dentro do sandbox. Sem isso o
        # arsenal-gate leria o vault real da maquina, e o smoke passaria a medir o
        # conteudo do registry de quem roda em vez do gate. Vault vazio = nenhuma
        # decisao registrada, que e exatamente o cenario que o gate deve bloquear.
        _out="$(printf '%s' "$_payload" | \
            HARNESS_DIR="$SMOKE_DIR/state" HARNESS_SKIP_DEPCHECK=1 \
            AI_BRAIN_PATH="$SMOKE_DIR/vault" \
            bash "$HOOKS_DIR/$_hook" 2>/dev/null)" && _code=0 || _code=$?
        if [ "$_code" != "$_want_code" ]; then
            echo "[FAIL]   $_name — exit $_code, esperado $_want_code"
            EXIT_CODE=1
            return
        fi
        if [ -n "$_want_out" ] && ! printf '%s' "$_out" | grep -q "$_want_out"; then
            echo "[FAIL]   $_name — saida nao contem '$_want_out'"
            EXIT_CODE=1
            return
        fi
        echo "[OK]     $_name"
    }

    # cwd escapado para JSON (contrabarras do Windows viram \\)
    SMOKE_CWD_JSON="$(printf '%s' "$SMOKE_CWD" | sed 's/\\/\\\\/g')"

    smoke "classify emite CLASSIFIED" harness-classify.sh \
        "{\"prompt\":\"cria um sistema completo de autenticacao\",\"cwd\":\"$SMOKE_CWD_JSON\"}" \
        0 "HARNESS v3 CLASSIFIED"

    smoke "classify ignora prompt vazio" harness-classify.sh '{}' 0

    # Branch Keeper: um sensor presente e inerte e indistinguivel de "nao havia
    # ramo" — a falha mais silenciosa deste subsistema. O smoke prova as duas
    # pontas: a ancora nasce no primeiro turno, e a tangente seguinte dispara.
    smoke "branch-sensor fixa ancora no 1o turno" harness-branch-sensor.sh         "{\"prompt\":\"vamos construir o indexador de traces\",\"cwd\":\"$SMOKE_CWD_JSON\",\"session_id\":\"smoke-1\"}" 0

    smoke "branch-sensor detecta tangente" harness-branch-sensor.sh         "{\"prompt\":\"e se a gente tambem cadastrasse receitas de bolo no aplicativo?\",\"cwd\":\"$SMOKE_CWD_JSON\",\"session_id\":\"smoke-1\"}"         0 "BRANCH SIGNAL"

    smoke "branch-sensor ignora payload vazio" harness-branch-sensor.sh '{}' 0

    smoke "git-guard bloqueia destrutivo" harness-git-guard.sh \
        '{"tool_input":{"command":"git re'"$(printf 'set --ha')"'rd HEAD~1"}}' 2

    smoke "git-guard passa comando benigno" harness-git-guard.sh \
        '{"tool_input":{"command":"ls -la"}}' 0

    smoke "git-guard avisa payload estranho" harness-git-guard.sh \
        '{"toolInput":{"command":"ls"}}' 0 "nao reconheceu"

    # Arsenal gate: a unica barreira dura do sistema. Um plugin inexistente nao
    # tem decisao no registry por definicao, entao serve de alvo estavel sem
    # depender do conteudo do vault desta maquina.
    smoke "arsenal-gate bloqueia install sem decisao" harness-arsenal-gate.sh \
        '{"tool_input":{"command":"claude plugin install plugin-que-nao-existe-xyz@mkt"}}' 2

    smoke "arsenal-gate passa comando benigno" harness-arsenal-gate.sh \
        '{"tool_input":{"command":"ls -la"}}' 0

    # Tirar nunca precisa de permissao: `disable` e `uninstall` passam direto.
    smoke "arsenal-gate passa disable" harness-arsenal-gate.sh \
        '{"tool_input":{"command":"claude plugin disable foo --scope user"}}' 0

    smoke "arsenal-gate avisa payload estranho" harness-arsenal-gate.sh \
        '{"toolInput":{"command":"ls"}}' 0 "nao reconheceu"

    # Os tres recebem cwd para exercitar a propagacao ate o resolvedor de
    # bucket — e para que a contagem de buckets abaixo tenha significado.
    smoke "reclassify aceita payload valido" harness-reclassify.sh \
        "{\"cwd\":\"$SMOKE_CWD_JSON\",\"tool_input\":{\"file_path\":\"/tmp/smoke.py\"}}" 0

    smoke "session-start responde" harness-session-start.sh \
        "{\"cwd\":\"$SMOKE_CWD_JSON\"}" 0

    smoke "precompact responde" harness-precompact.sh \
        "{\"cwd\":\"$SMOKE_CWD_JSON\"}" 0

    # O bucket por projeto so funciona se o cwd chegar limpo ao resolvedor. Um
    # \r do print() do Windows fazia raiz e subdiretorio virarem buckets
    # distintos — sintoma silencioso, achado so por inspecao.
    if [ -d "$SMOKE_DIR/state/projects" ]; then
        SMOKE_BUCKETS="$(find "$SMOKE_DIR/state/projects" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d '[:space:]')"
        if [ "${SMOKE_BUCKETS:-0}" = "1" ]; then
            echo "[OK]     escopo por projeto resolve para um bucket unico"
        else
            echo "[FAIL]   escopo por projeto criou $SMOKE_BUCKETS buckets para um repo so"
            EXIT_CODE=1
        fi
    fi

    rm -rf "$SMOKE_DIR" 2>/dev/null || true
fi
echo ""

# ===========================================================================
# Liveness: o host ainda CHAMA os hooks?
# ===========================================================================
# O smoke-test acima prova que os hooks funcionam quando executados. Nao prova
# que continuam sendo chamados — se o host renomear um evento, tudo fica inerte
# e verde. Cada hook grava um heartbeat ao ser invocado; aqui esse sinal e
# confrontado com a atividade de sessao registrada pelo proprio host.
echo "--- Hooks (liveness) ---"
LIVENESS_PY="$PLUGIN_DIR/scripts/check_hook_liveness.py"
if [ -f "$LIVENESS_PY" ] && command -v python >/dev/null 2>&1; then
    if python "$LIVENESS_PY" --hooks-json "$PLUGIN_DIR/hooks/hooks.json"; then
        :
    else
        EXIT_CODE=1
    fi
else
    warn "check_hook_liveness.py ausente — liveness nao verificada"
fi
echo ""

# ===========================================================================
# Cobertura de eventos por CLI host
# ===========================================================================
# O harness registra 5 eventos. O Codex nao implementa PreCompact, entao o
# snapshot de handoff (trace, rotacao, sync do Obsidian) simplesmente nao roda
# la — silenciosamente. Melhor dizer isso do que deixar o usuario descobrir pela
# ausencia de traces.
if [ -f "$HOME/.codex/hooks.json" ]; then
    echo "--- Cobertura de eventos (Codex detectado) ---"
    python - "$HOME/.codex/hooks.json" "$PLUGIN_DIR/hooks/hooks.json" <<'PYEOF' 2>/dev/null || warn "nao foi possivel comparar eventos"
import json, sys
try:
    codex = set(json.load(open(sys.argv[1], encoding="utf-8")).get("hooks", {}))
    ours = set(json.load(open(sys.argv[2], encoding="utf-8")).get("hooks", {}))
except Exception:
    raise SystemExit(1)
missing = sorted(ours - codex)
if missing:
    print(f"[WARN]   eventos nao suportados pelo Codex: {', '.join(missing)}")
    print("         os hooks desses eventos nunca disparam em sessoes do Codex")
else:
    print(f"[OK]     todos os {len(ours)} eventos registrados existem no Codex")
PYEOF
    echo ""
fi

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
    check "branch-out skill"          "test -f '$SKILLS_DIR/branch-out/SKILL.md'"
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
check "branch_state.py"          "test -f '$PLUGIN_DIR/scripts/branch_state.py'"
check "branch_seed.py"           "test -f '$PLUGIN_DIR/scripts/branch_seed.py'"
check "branch_sensor.py"         "test -f '$PLUGIN_DIR/scripts/branch_sensor.py'"
# Lista fixa nao acompanha Workflow novo. Em 2026-08-26 o `wf-grill.js` entrou e
# o health-check seguiu dizendo "All checks passed" sem nunca ter olhado para ele
# — verde que nao cobre o que da a entender. Conta o que existe e exige o que e
# load-bearing pelo nome; o resto entra sozinho.
WF_DIR="$PLUGIN_DIR/scripts/workflows"
for wf in wf-verify-multimodel.js wf-context-scan.js wf-grill.js; do
    check "$wf" "test -f '$WF_DIR/$wf'"
done
WF_ENCONTRADOS="$(find "$WF_DIR" -maxdepth 1 -name 'wf-*.js' 2>/dev/null | wc -l | tr -d ' ')"
echo "[INFO]   Workflows presentes: $WF_ENCONTRADOS (todos validados abaixo)"
check "signals.version == 3"     "python -c \"import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); sys.exit(0 if d.get('version')==3 else 1)\" \"$HARNESS_DIR/signals.json\""
if command -v node >/dev/null 2>&1; then
    check "Workflows validam (node)" "node '$PLUGIN_DIR/scripts/workflows/validate_workflows.cjs'"
else
    warn "node ausente — validacao de Workflows pulada"
fi

# Canario da classificacao. Numero que ninguem le e numero que nao existe: a
# metrica semantica ficou null por semanas porque nada a reportava. Aqui ela
# aparece toda vez, junto com quantas tasks estao sem confirmacao.
CLASSIFY_LINHA="$(python - "$HARNESS_DIR/signals.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    c = json.load(open(sys.argv[1], encoding="utf-8"))["aggregates"]["classify"]
except Exception:
    raise SystemExit(0)
sem = c.get("sem_confirmacao")
proxy, n = c.get("proxy_regex_vs_observado"), c.get("proxy_amostras") or 0
partes = []
if c.get("avg_classify_accuracy") is None:
    partes.append(f"acuracia semantica: null ({sem} task(s) sem confirmacao)"
                  if sem else "acuracia semantica: null (sem tasks)")
else:
    partes.append(f"acuracia semantica: {c['avg_classify_accuracy']:.0%}")
partes.append(f"canario (proxy, nao e acuracia): {proxy:.0%} em {n}" if proxy is not None
              else "canario: sem amostra")
print(" | ".join(partes))
PY
)"
[ -n "$CLASSIFY_LINHA" ] && echo "[INFO]   classify -> $CLASSIFY_LINHA"

# Grafo: so reporta quando existe um. Repo sem grafo nao e defeito.
if [ -f "graphify-out/graph.json" ]; then
    if python "$PLUGIN_DIR/tools/graph_lint.py" >/dev/null 2>&1; then
        echo "[OK]     knowledge graph integro"
    else
        echo "[FAIL]   knowledge graph com problema de integridade — rode:"
        echo "         python tools/graph_lint.py --report"
        EXIT_CODE=1
    fi
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
else
    # Tres desfechos, nao dois. Ate 2026-09-03 qualquer saida nao-zero virava
    # "app fechado / REST off?", e o doutor estava QUEBRADO
    # (ModuleNotFoundError: console, porque o bloco __main__ supunha `tools/`
    # em sys.path[0], o que nao vale sob `-m` — que e como esta linha invoca).
    # Medido no diagnostico: Obsidian com 3 processos, 27124 LISTENING, TLS
    # estrito devolvendo 200, MCP respondendo initialize. Tudo no ar, e o
    # relatorio mandava consertar o Obsidian.
    #
    # Mandar consertar a coisa errada gasta o tempo de quem le e deixa o
    # defeito real de pe. O doutor ja fala JSON com `ready`: se a saida nao e
    # JSON, quem falhou foi o doutor.
    DOCTOR_OUT="$( cd "$PLUGIN_DIR" && python -m tools.vault_sync_doctor --root "$VAULT_ROOT" --check-rest 2>&1 )"
    DOCTOR_RC=$?
    if [ "$DOCTOR_RC" -eq 0 ]; then
        echo "[OK]     Obsidian doctor ready (plugins + REST API 127.0.0.1:27124)"
    elif printf '%s' "$DOCTOR_OUT" | python -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
        warn "Obsidian doctor rodou e reprovou (app fechado / REST off?) — rode: (cd '$PLUGIN_DIR' && python -m tools.vault_sync_doctor --root \"\$VAULT_PATH\" --check-rest)"
    else
        warn "Obsidian doctor NAO RODOU (defeito na ferramenta, nao no Obsidian): $(printf '%s' "$DOCTOR_OUT" | tail -n 1)"
    fi
fi
echo ""

echo "--- Skill Router (opt-in) ---"
# Desde a auditoria 2026-07-28 o router so roda com HARNESS_ROUTER=1. Avisar
# sobre Ollama para quem nao ligou o router e ruido puro: nada nesse caminho
# executa, entao nao ha degradacao a reportar.
if [ "${HARNESS_ROUTER:-0}" != "1" ]; then
    echo "[OK]     router desligado (HARNESS_ROUTER != 1) — nada a verificar"
else
    IDX="$HARNESS_DIR/skills-index"
    if [ -f "$IDX/meta.json" ]; then
        echo "[OK]     skills-index presente"
        [ -f "$IDX/.stale" ] && warn "skills-index stale (rebuild pendente)"
    else
        warn "skills-index ausente — rode: python scripts/build_skills_index.py"
    fi
    if command -v curl >/dev/null 2>&1 && curl -s -m 2 "${HARNESS_OLLAMA_URL:-http://127.0.0.1:11434}/api/tags" 2>/dev/null | grep -q "${HARNESS_EMBED_MODEL:-nomic-embed-text-v2-moe}"; then
        echo "[OK]     Ollama + ${HARNESS_EMBED_MODEL:-nomic-embed-text-v2-moe}"
    else
        warn "HARNESS_ROUTER=1 mas Ollama/modelo indisponivel — camada B nunca respondera"
    fi
    BREAKER="$HARNESS_DIR/router/layer-b-breaker.json"
    if [ -f "$BREAKER" ]; then
        FAILS="$(python -c "import json,sys;print(json.load(open(sys.argv[1])).get('failures',0))" "$BREAKER" 2>/dev/null || echo 0)"
        [ "${FAILS:-0}" -ge 3 ] && warn "camada B em cooldown ($FAILS falhas seguidas)"
    fi
fi
echo ""

echo "--- Plugin load (Claude Code) ---"
# Plugin em disco != plugin CARREGADO. Sem o marketplace 'local' registrado, o
# sufixo @local nao resolve e os hooks nunca disparam (falha silenciosa). Esta
# checagem detecta isso — a lacuna que escondeu o plugin por todo o pos-format.
if command -v claude >/dev/null 2>&1; then
    if claude plugin list 2>/dev/null | grep -qa "harness4claude"; then
        # Sem parenteses sobre a origem: o grep so confirma que o nome aparece
        # em `claude plugin list`. Ate 2026-07-28 esta linha afirmava
        # "(marketplace local)" enquanto o plugin vinha do GitHub.
        echo "[OK]     harness4claude carregado no Claude Code"
    else
        echo "[FAIL]   harness4claude NAO carregado — hooks inativos. Corrija com:"
        echo "         claude plugin marketplace add ~/.claude/plugins/local"
        echo "         claude plugin install harness4claude@local   (e reinicie)"
        EXIT_CODE=1
    fi
else
    warn "claude CLI ausente no PATH — nao foi possivel verificar carga do plugin"
fi

# Contrato comum e engine transacional.
if python "$PLUGIN_DIR/scripts/contract_adapter.py" check --root "$PLUGIN_DIR" >/dev/null 2>&1; then
    echo "[OK]     Harness4Contract v1: 22 capacidades equivalentes e lock valido"
else
    echo "[FAIL]   Harness4Contract degradado; rode: python scripts/contract_adapter.py check"
    EXIT_CODE=1
fi

DB_FAILURES="$(python - "$HARNESS_DIR" <<'PY'
import sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
bad = []
for path in root.rglob("harness.db") if root.exists() else []:
    try:
        with sqlite3.connect(path) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok": bad.append(f"{path}: {result}")
    except sqlite3.Error as exc:
        bad.append(f"{path}: {exc}")
print("\n".join(bad))
PY
)"
if [ -n "$DB_FAILURES" ]; then
    echo "[FAIL]   integridade do estado transacional: $DB_FAILURES"
    EXIT_CODE=1
else
    echo "[OK]     estado transacional SQLite integro"
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

# Versao igual NAO significa codigo igual.
#
# Descoberto em 2026-08-13, no fecho do dia: a arvore e o cache estavam ambos em
# 3.7.0 e o bloco abaixo dizia "coerente" — enquanto o cache nao tinha tools/
# impact.py, carregava um arsenal.py 27 KB menor e um gate que ainda bloqueava
# por mencao. Todo trabalho feito DEPOIS de um bump, sem outro bump, roda no
# clone e nao roda no Claude Code, e o unico sinal era nenhum.
#
# E a mesma licao que este bloco ja tinha aprendido uma vez com versao (era WARN
# e escondeu 3.3.0 vs 3.2.0 por 41 dias), chegando pela porta ao lado: o
# indicador conferia a etiqueta, nao o conteudo.
CONTEUDO_DIVERGE="$(python - "$PLUGIN_DIR" <<'PY' 2>/dev/null || echo "?"
import hashlib, json, os, sys
local = sys.argv[1]
try:
    d = json.load(open(os.path.join(os.path.expanduser("~"), ".claude", "plugins",
                                    "installed_plugins.json"), encoding="utf-8"))
    caminho = next(e["installPath"] for k, v in d.get("plugins", {}).items()
                   if k.startswith("harness4claude") for e in v if e.get("installPath"))
except Exception:
    print("?"); raise SystemExit(0)

def impressao(raiz):
    # Fim de linha normalizado ANTES do hash. Sem isso o check acusava divergencia
    # em 5 arquivos que normalizam para identico: a arvore local tem CRLF
    # (Windows), o cache e checkout do git com LF. Byte diferente, conteudo igual.
    # Guard que grita lobo na primeira execucao e guard que ninguem le na segunda —
    # foi o defeito que este proprio bloco quase introduziu, em 2026-08-13.
    h = hashlib.sha256()
    for sub in ("hooks", "scripts", "skills", "tools"):
        base = os.path.join(raiz, sub)
        for r, _, fs in os.walk(base):
            if "__pycache__" in r:
                continue
            for f in sorted(fs):
                p = os.path.join(r, f)
                try:
                    h.update(os.path.relpath(p, raiz).replace(os.sep, "/").encode())
                    h.update(open(p, "rb").read().replace(b"\r\n", b"\n"))
                except OSError:
                    pass
    return h.hexdigest()[:12]

a, b = impressao(local), impressao(caminho)
print("iguais" if a == b else f"{a} != {b}")
PY
)"
if [ "$CONTEUDO_DIVERGE" = "iguais" ]; then
    echo "[OK]     conteudo identico entre arvore local e plugin carregado"
elif [ "$CONTEUDO_DIVERGE" = "?" ]; then
    warn "nao foi possivel comparar o CONTEUDO entre arvore e cache"
else
    echo "[FAIL]   MESMA versao, CONTEUDO diferente ($CONTEUDO_DIVERGE)."
    echo "         O Claude Code carrega codigo antigo com o numero novo. Suba a versao"
    echo "         em .claude-plugin/plugin.json e marketplace.json, faca push, e rode"
    echo "         /plugin update harness4claude"
    EXIT_CODE=1
fi

if [ "$INSTALLED_VERSION" = "nao-instalado" ]; then
    warn "plugin nao consta em installed_plugins.json"
elif [ "$PLUGIN_VERSION" != "$INSTALLED_VERSION" ]; then
    # FAIL, nao WARN (auditoria 2026-07-28). Este bloco JA detectava a
    # divergencia 3.3.0 vs 3.2.0 — o defeito que escondia todos os outros por 41
    # dias — mas saia com exit 0, entao nenhum gate reagia. Um health-check que
    # passa enquanto "o codigo que roda nao e o seu" nao esta checando nada.
    echo "[FAIL]   DIVERGENCIA de versao: a arvore local esta em $PLUGIN_VERSION mas o Claude Code carrega $INSTALLED_VERSION."
    echo "         O codigo que roda NAO e o que voce esta editando."
    echo "         Corrija com:"
    echo "           git push"
    echo "           claude plugin marketplace update harness4claude"
    echo "           claude plugin update harness4claude@harness4claude   (e reinicie)"
    EXIT_CODE=1
else
    echo "[OK]     versao coerente entre arvore local e plugin carregado"
fi

# plugin-root e compartilhado entre CLIs no mesmo $HOME (last-writer-wins). Com
# versoes identicas nao ha dano; com versoes divergentes as skills executariam
# scripts da arvore errada. Barato de detectar, caro de diagnosticar depois.
# As skills usam plugin-root como prefixo de execucao. O que importa nao e de
# qual CLI a arvore veio — e se ela EXISTE e esta na mesma versao. Avisar so por
# ser de outro CLI, com versoes iguais, seria alarme falso; e alarme falso vira
# alarme ignorado.
PLUGIN_ROOT_FILE="$HARNESS_DIR/plugin-root"
if [ ! -f "$PLUGIN_ROOT_FILE" ]; then
    warn "plugin-root ausente — sera criado no proximo SessionStart"
else
    PR_VALUE="$(cat "$PLUGIN_ROOT_FILE" 2>/dev/null || echo "")"
    if [ -z "$PR_VALUE" ]; then
        echo "[FAIL]   plugin-root vazio — toda skill que o usa como prefixo falha"
        EXIT_CODE=1
    elif [ ! -f "$PR_VALUE/scripts/record_signal.py" ]; then
        echo "[FAIL]   plugin-root aponta para arvore inexistente ou incompleta:"
        echo "         $PR_VALUE"
        echo "         Toda skill que o usa como prefixo falha. Corrija reiniciando a sessao"
        echo "         (o SessionStart reescreve) ou: printf '%s\\n' \"\$PLUGIN_DIR\" > $PLUGIN_ROOT_FILE"
        EXIT_CODE=1
    else
        PR_VERSION="$(python -c "
import json,sys
try:
    print(json.load(open(sys.argv[1],encoding='utf-8')).get('version','?'))
except Exception:
    print('?')
" "$PR_VALUE/.claude-plugin/plugin.json" 2>/dev/null || echo "?")"
        if [ "$PR_VERSION" != "$INSTALLED_VERSION" ]; then
            warn "plugin-root em $PR_VERSION, mas o Claude Code carrega $INSTALLED_VERSION"
            echo "         $PR_VALUE"
            echo "         (arquivo compartilhado entre CLIs no mesmo \$HOME; as skills leem dele)"
        else
            echo "[OK]     plugin-root valido e na versao $PR_VERSION"
        fi
    fi
fi

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "=== All checks passed ==="
else
    echo "=== Some checks failed (see above) ==="
fi

exit $EXIT_CODE

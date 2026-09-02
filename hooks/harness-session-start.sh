#!/usr/bin/env bash
# harness-session-start.sh — SessionStart hook for Harness v3
# Checks for active pipeline and emits the resume block via hooks/emit.py.

set -euo pipefail

# ============================================================================
# Bootstrap: create state directory and files on first run
# ============================================================================
: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR
mkdir -p "$HARNESS_DIR"

# Heartbeat de disparo — ver harness-classify.sh para o porque.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/SessionStart"; } 2>/dev/null || true

# ---------------------------------------------------------------------------
# Plugin root portavel
# ---------------------------------------------------------------------------
# CLAUDE_PLUGIN_ROOT so existe no ambiente dos HOOKS. O modelo, ao executar os
# comandos das skills via Bash, nao a enxerga — e um caminho absoluto de uma
# maquina especifica (~/.claude/plugins/local/...) pode nem existir em outra
# instalacao. Este hook, que tem a variavel, persiste o valor resolvido para que
# as skills o leiam:
#
#     python "$(cat ~/.claude/harness/plugin-root)/scripts/record_signal.py" ...
#
# Reescrito a cada sessao: acompanha update de versao ou mudanca de caminho.
PLUGIN_ROOT_RESOLVED="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Formato misto no Windows (C:/Users/... com barras normais): o unico que
# funciona tanto no Git Bash quanto no PowerShell. O formato MSYS (/c/Users/...)
# quebra em PowerShell, e o nativo com contrabarras quebra em bash.
if command -v cygpath >/dev/null 2>&1; then
    PLUGIN_ROOT_RESOLVED="$(cygpath -m "$PLUGIN_ROOT_RESOLVED" 2>/dev/null || printf '%s' "$PLUGIN_ROOT_RESOLVED")"
fi

# So grava se a arvore resolvida realmente contiver os scripts. O arquivo e
# compartilhado entre CLIs no mesmo $HOME (last-writer-wins) e as skills o usam
# como prefixo de execucao: um valor podre quebra TODAS elas de uma vez.
# Aconteceu em 2026-07-28 — o Codex apontou para o proprio cache, esse cache foi
# removido no upgrade de versao, e o arquivo passou a nomear um caminho
# inexistente. Se o valor atual esta podre, sobrescreve mesmo assim.
if [ -f "$PLUGIN_ROOT_RESOLVED/scripts/record_signal.py" ]; then
    PLUGIN_ROOT_CURRENT="$(cat "$HARNESS_DIR/plugin-root" 2>/dev/null || echo "")"
    if [ -z "$PLUGIN_ROOT_CURRENT" ] || [ ! -f "$PLUGIN_ROOT_CURRENT/scripts/record_signal.py" ] \
       || [ "$PLUGIN_ROOT_CURRENT" != "$PLUGIN_ROOT_RESOLVED" ]; then
        printf '%s\n' "$PLUGIN_ROOT_RESOLVED" > "$HARNESS_DIR/plugin-root" 2>/dev/null || true
    fi
fi

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

# ---------------------------------------------------------------------------
# Emissao
# ---------------------------------------------------------------------------
# Ate 2026-09-01 os tres pontos de saida deste hook usavam `systemMessage`,
# que e canal de UI: RESUMING, CONTINUING, o digest do vault e o do arsenal
# nunca chegaram ao modelo.
#
# A migracao passa por CLI em vez de import inline de proposito. Os blocos que
# emitem aqui vivem dentro de `python -c` numa string de aspas duplas do bash,
# onde escape de quebra de linha vira quebra real e crase vira substituicao de
# comando — os dois quebraram este arquivo em 2026-08-13, com exit 1 e stderr
# vazio. Um pipe de texto cru nao mexe nesse terreno.
#
# Se o emissor falhar, o texto cru sai mesmo assim: stdout em SessionStart e
# canal provado. Perder o digest por causa do mensageiro repetiria a falha.
# `|| true` em cada uso nao e paranoia: `set -euo pipefail` faz qualquer erro
# dentro do bloco python — um import que falta, um state.json ilegivel — matar
# o hook com exit 1 e stderr vazio. O SessionStart morreria em silencio, que e
# a mesma classe de falha que o canal morto: roda, nao avisa, nao entrega.
_harness_emit() {
    local kind="${1:-session_start}"
    local texto
    texto="$(cat)"
    [ -z "$texto" ] && return 0
    printf '%s' "$texto" | python "$PLUGIN_DIR/hooks/emit.py"         --event SessionStart --kind "$kind" --hook session_start         --cwd "$PWD" --text-file - 2>/dev/null         || printf '%s' "$texto"
}

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
#
# HARNESS_SKIP_DEPCHECK=1 pula este bloco. Necessario para testes hermeticos:
# com HARNESS_DIR temporario o flag .bootstrap-done nunca existe, entao cada
# invocacao dispararia um "pip install --user" — lento, dependente de rede, e
# com efeito colateral fora do diretorio isolado.
BOOTSTRAP_FLAG="$HARNESS_DIR/.bootstrap-done"
if [ -n "${HARNESS_SKIP_DEPCHECK:-}" ]; then
    touch "$BOOTSTRAP_FLAG" 2>/dev/null || true
elif [ ! -f "$BOOTSTRAP_FLAG" ]; then
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

# ============================================================================
# Escopo do estado: bucket do projeto (default) ou raiz global
# ============================================================================
# Sem isto o hook oferecia retomar a MESMA task em toda sessao de todo projeto
# da maquina — a checagem era so `status == "active"`, sem nocao de onde a task
# nasceu. Ver scripts/harness_paths.py. `-t 0` porque sem stdin de pipe
# (execucao manual) um `cat` puro bloquearia o hook.
SESSION_INPUT=""
if [ ! -t 0 ]; then SESSION_INPUT="$(cat 2>/dev/null || true)"; fi
if command -v cygpath &>/dev/null; then
    export HARNESS_SCRIPTS_DIR_PY="$(cygpath -w "$PLUGIN_DIR/scripts")"
else
    export HARNESS_SCRIPTS_DIR_PY="$PLUGIN_DIR/scripts"
fi
export HARNESS_ROOT_PY="$HARNESS_DIR_PY"
STATE_DIR_PY="$(printf '%s' "$SESSION_INPUT" | python -c "
import sys, json, os
sys.path.insert(0, os.environ['HARNESS_SCRIPTS_DIR_PY'])
root = os.environ['HARNESS_ROOT_PY']
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
" 2>/dev/null || printf '%s' "$HARNESS_DIR_PY")"
STATE_DIR_PY="${STATE_DIR_PY%$'\r'}"   # $() tira \n final, mas nao o \r do Windows
[ -z "$STATE_DIR_PY" ] && STATE_DIR_PY="$HARNESS_DIR_PY"

# ============================================================================
# Digest do vault AI-Brain (~400 bytes)
# ============================================================================
# Calculado UMA vez aqui e usado em TODOS os caminhos de saida. Ha tres: bucket
# de projeto novo (state.json ausente), pipeline expirado pelo TTL, e o caminho
# normal. Injetar so no ultimo deixaria justamente a primeira sessao de cada
# projeto — quando o contexto do vault mais importa — sem nenhum aviso de que a
# memoria de decisao existe.
if command -v cygpath &>/dev/null; then
    PLUGIN_DIR_PY="$(cygpath -w "$PLUGIN_DIR")"
else
    PLUGIN_DIR_PY="$PLUGIN_DIR"
fi
export PYTHONUTF8=1
export PLUGIN_DIR_PY
# `tr -d '\r'`: o print() do Windows emite CRLF e o \r vazaria para dentro do JSON do
# systemMessage. E o mesmo defeito que fez raiz e subdiretorio virarem buckets distintos
# (auditoria 2026-07-28) — silencioso e chato de rastrear depois.
VAULT_DIGEST="$(python -c "
import os, sys
sys.path.insert(0, os.path.join(os.environ['PLUGIN_DIR_PY'], 'tools'))
try:
    import wiki_index
    print(wiki_index.build_digest(wiki_index.default_root()))
except Exception:
    pass
" 2>/dev/null | tr -d '\r' || true)"
export VAULT_DIGEST

# Funil proativo do arsenal. A skill `assimilar` espera gatilho do usuario; a
# varredura de marketplace nao deveria — a fonte e local e verificavel, e ate
# 2026-08-13 ela existia e nunca tinha sido agendada, que e o mesmo destino da
# operacao `ingest` do vault (declarada em 2026-05, primeira execucao em 08).
#
# Rate-limit de 24h por marker: rodar a cada sessao gastaria I/O sem novidade, e
# ruido diario vira linha que ninguem le. SILENCIOSO quando nao ha candidato —
# aviso que aparece sempre deixa de ser aviso.
ARSENAL_DIGEST=""
_MARKER="$HARNESS_DIR/.arsenal-candidates-last"
_NOW=$(date +%s 2>/dev/null || echo 0)
_LAST=$(cat "$_MARKER" 2>/dev/null || echo 0)
case "$_LAST" in ''|*[!0-9]*) _LAST=0 ;; esac
if [ "$_NOW" -eq 0 ] || [ $((_NOW - _LAST)) -ge 86400 ]; then
    mkdir -p "$HARNESS_DIR" 2>/dev/null || true
    printf '%s\n' "$_NOW" > "$_MARKER" 2>/dev/null || true
    ARSENAL_DIGEST="$(python -c "
import json, os, subprocess, sys
raiz = os.path.join(os.environ['PLUGIN_DIR_PY'], 'tools', 'arsenal.py')
try:
    r = subprocess.run([sys.executable, raiz, 'candidates'],
                       capture_output=True, text=True, timeout=25)
    d = json.loads(r.stdout)
except Exception:
    raise SystemExit(0)
n = d.get('resumo', {}).get('novos', 0)
if not n:
    raise SystemExit(0)   # silencioso quando nao ha novidade
ids = ', '.join(c['id'] for c in d.get('candidatos', [])[:5])
print(f'ARSENAL: {n} candidato(s) ainda sem decisao ({ids}). '
      'Rode: python tools/arsenal.py candidates --report')
" 2>/dev/null | tr -d '\r' || true)"
fi
export ARSENAL_DIGEST

STATE_FILE_PY="$STATE_DIR_PY/state.json"
if [ ! -f "$STATE_FILE_PY" ]; then
    python -c "
import json, os, sys
d = sys.argv[1]
os.makedirs(d, exist_ok=True)
json.dump({'task_id': None, 'schema_version': 3, 'classification': None,
           'status': 'idle', 'pipeline': [], 'current_step': None,
           'artifacts_so_far': [], 'started_at': None},
          open(os.path.join(d, 'state.json'), 'w'), indent=2)
" "$STATE_DIR_PY" 2>/dev/null || exit 0
    # Bucket recem-criado: nao ha pipeline a retomar, mas o digest do vault ainda
    # vale — e a primeira sessao do projeto e onde ele mais rende.
    python -c "
import json, os
digest = os.environ.get('VAULT_DIGEST', '').strip()
arsenal = os.environ.get('ARSENAL_DIGEST', '').strip()
# Junta com chr(10) em vez de escape. Este bloco vive dentro de python -c
# numa string de aspas duplas do bash: escape de quebra de linha vira
# quebra real e quebra a sintaxe, e crase vira substituicao de comando.
# Os dois aconteceram aqui em 2026-08-13, e o sintoma foi exit 1 sem stderr.
partes_saida = [x for x in (digest, arsenal) if x]
if partes_saida:
    print((chr(10) * 2).join(partes_saida))
" 2>/dev/null | _harness_emit digest || true
    exit 0
fi

# ============================================================================
# TTL: expira pipeline abandonado antes de oferecer RESUMING
# ============================================================================
# Sem isso o hook convida a retomar uma task morta em toda sessao de todo
# projeto (auditoria 2026-07-28: uma task de 24/07 reaparecia 4 dias depois).
# Quando expira, avisa EXPIRED em vez de RESUMING e nao ha o que retomar.
EXPIRED_TASK=""
LOCK_LIB="$PLUGIN_DIR/scripts/state-lock.sh"
if [[ -f "$LOCK_LIB" ]]; then
    # shellcheck source=../scripts/state-lock.sh
    source "$LOCK_LIB"
    if ! acquire_state_lock; then
        exit 0
    fi
    trap release_state_lock EXIT
fi

EXPIRE_PY="$PLUGIN_DIR/scripts/expire_stale_pipeline.py"
if [ -f "$EXPIRE_PY" ] && command -v python >/dev/null 2>&1; then
    EXPIRED_TASK="$(python "$EXPIRE_PY" --harness-dir "$STATE_DIR_PY" \
        --signals-dir "$HARNESS_DIR_PY" 2>/dev/null || true)"
fi

if [ -n "$EXPIRED_TASK" ]; then
    export HARNESS_EXPIRED_TASK="${EXPIRED_TASK#EXPIRED }"
    python -c "
import json, os
tid = os.environ.get('HARNESS_EXPIRED_TASK', 'unknown')
partes = [
    f'HARNESS v3 EXPIRED: pipeline anterior (task {tid}) passou do TTL e foi '
    f'encerrado como abandonado. Nao ha pipeline ativo — a proxima tarefa '
    f'sera classificada do zero.'
]
digest = os.environ.get('VAULT_DIGEST', '').strip()
if digest:
    partes.append(digest)
arsenal = os.environ.get('ARSENAL_DIGEST', '').strip()
if arsenal:
    partes.append(arsenal)
print('\n\n'.join(partes))
" 2>/dev/null | _harness_emit resuming || true
    exit 0
fi

python -c "
import json, os, sys
sys.path.insert(0, os.environ['HARNESS_SCRIPTS_DIR_PY'])
from continuation_policy import should_continue

parts = []

# 1. Pipeline em andamento (comportamento historico do hook).
try:
    with open(r'$STATE_FILE_PY') as f:
        state = json.load(f)
    if should_continue(state):
        tid = state.get('task_id', 'unknown')
        cls = state.get('classification', 'unknown')
        step = state.get('current_step') or (state['pipeline'][0] if state['pipeline'] else 'none')
        pipe = ' -> '.join(state['pipeline'])
        gate = state.get('pending_gate')
        instruction = (
            f'Pending human gate: {gate}. Invoke harness-workflow skill to resolve it.'
            if gate else
            'Invoke harness-workflow skill to continue where you left off.'
        )
        parts.append(
            f'HARNESS v3 RESUMING: Scoped pipeline {cls} (task {tid}). '
            f'Current step: {step}. Pipeline: {pipe}. '
            f'{instruction}'
        )
except Exception:
    pass

# 2. Digest do vault AI-Brain, ja calculado acima e valido nos tres caminhos de saida.
digest = os.environ.get('VAULT_DIGEST', '').strip()
if digest:
    parts.append(digest)
arsenal = os.environ.get('ARSENAL_DIGEST', '').strip()
if arsenal:
    parts.append(arsenal)

if parts:
    print('\n\n'.join(parts))
" 2>/dev/null | _harness_emit session_start || true

exit 0

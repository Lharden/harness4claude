#!/usr/bin/env bash
# harness-classify.sh — UserPromptSubmit hook for Harness v3
# Classifies tasks as L0/L1/L2, detects type, manages pipeline state.
# Reads JSON from stdin (field: user_message or content).
# Emits <harness-classification> or <harness-continuation> blocks.

set -euo pipefail

# Force UTF-8 for all Python subprocesses (fix for charmap codec bug on Windows)
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG=C.UTF-8

HARNESS_DIR="$HOME/.claude/harness"
STATE_FILE="$HARNESS_DIR/state.json"
COUNTER_FILE="$HARNESS_DIR/.session-files-count"

# Ensure harness dir exists
mkdir -p "$HARNESS_DIR"

# Acquire exclusive lock on state.json before any read/modify/write.
# Without this, parallel sessions in Claude Code Desktop App can corrupt state.
HOOK_DIR_REL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_LIB="${HOOK_DIR_REL}/../scripts/state-lock.sh"
if [[ -f "$LOCK_LIB" ]]; then
  # shellcheck source=../scripts/state-lock.sh
  source "$LOCK_LIB"
  if ! acquire_state_lock; then
    # Lock timeout — fail closed (no classification this turn). Prompt
    # passes through unmodified. Better than corrupted state.json.
    exit 0
  fi
  trap release_state_lock EXIT
fi

# ---------------------------------------------------------------------------
# 1. Read input JSON and extract message
# ---------------------------------------------------------------------------
INPUT="$(cat)"

# Single Python call: extract message + normalize unicode
# Errors logged to debug file instead of silently swallowed
MSG_LOWER="$(printf '%s' "$INPUT" | python -c "
import sys, json, unicodedata
try:
    data = json.load(sys.stdin)
    msg = data.get('prompt', data.get('user_prompt', data.get('user_message', data.get('content', ''))))
    if not msg or not msg.strip():
        sys.exit(0)
    text = msg.lower().strip()
    nfkd = unicodedata.normalize('NFKD', text)
    clean = ''.join(c for c in nfkd if not unicodedata.combining(c))
    print(clean)
except Exception as e:
    import os
    debug = os.path.join(os.path.expanduser('~'), '.claude', 'harness', 'debug-classify.log')
    with open(debug, 'a', encoding='utf-8') as f:
        f.write(f'{e}\n')
    sys.exit(1)
" || echo "")"

if [ -z "$MSG_LOWER" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. Delegate everything to a single Python script via env vars
# ---------------------------------------------------------------------------
# Convert MSYS paths to Windows paths for Python
if command -v cygpath &>/dev/null; then
    export HARNESS_STATE_FILE="$(cygpath -w "$STATE_FILE")"
    export HARNESS_COUNTER_FILE="$(cygpath -w "$COUNTER_FILE")"
else
    export HARNESS_STATE_FILE="$STATE_FILE"
    export HARNESS_COUNTER_FILE="$COUNTER_FILE"
fi
export HARNESS_MSG_LOWER="$MSG_LOWER"
export PYTHONUTF8=1

python << 'PYEOF'
import os, re, json
from datetime import datetime, timezone

msg = os.environ["HARNESS_MSG_LOWER"]
state_file = os.environ["HARNESS_STATE_FILE"]
counter_file = os.environ["HARNESS_COUNTER_FILE"]

# ============================================================================
# Task-switch detection
# ============================================================================
SWITCH_PATTERNS = (
    r'nova tarefa|new task|cancela|outra coisa|switch to|esquece isso|'
    r'deixa pra la|nevermind|forget that|muda de assunto'
)
is_task_switch = bool(re.search(SWITCH_PATTERNS, msg, re.IGNORECASE))

# ============================================================================
# Check active pipeline in state.json
# ============================================================================
has_active = False
try:
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    if state.get("status") == "active" and state.get("pipeline"):
        has_active = True
except Exception:
    pass

# If active pipeline and NOT a task switch → emit continuation and exit
if has_active and not is_task_switch:
    task_id = state.get("task_id", "unknown")
    classification = state.get("classification", "unknown")
    current_step = state.get("current_step")
    pipeline = state.get("pipeline", [])
    step_display = current_step if current_step else (pipeline[0] if pipeline else "none")
    pipe_display = ' -> '.join(pipeline)
    output = json.dumps({
        "systemMessage": (
            f"HARNESS v3 CONTINUING: {classification} (task {task_id}). "
            f"Current step: {step_display}. Pipeline: {pipe_display}. "
            f"Continue the active pipeline by invoking skill='harness-workflow'."
        )
    })
    print(output)
    raise SystemExit(0)

# ============================================================================
# Keyword lists (bilingual PT+EN)
# ============================================================================

# ---- L0 ----
l0_questions = [
    r'\?', r'\bexplique\b', r'\bexplain\b', r'\bo que e\b', r'\bwhat is\b',
    r'\bcomo funciona\b', r'\bhow does\b', r'\bpor que\b', r'\bwhy\b',
    r'\bqual a diferenca\b', r'\bme diga\b', r'\btell me\b', r'\bdescreva\b',
    r'\bdescribe\b', r'\bmostre\b', r'\bshow\b', r'\bliste\b', r'\blist\b',
]
l0_cosmetic = [
    r'\brenomeie\b', r'\brename\b', r'\bformate\b', r'\bformat\b',
    r'\bcorrija typo\b', r'\bfix typo\b', r'\bajuste indentacao\b',
    r'\bfix indent\b', r'\bmude o nome\b', r'\batualize comentario\b',
    r'\bupdate comment\b', r'\btraduza\b',
]
l0_meta = [
    r'\blembre\b', r'\bremember\b', r'\besqueca\b', r'\bforget\b',
    r'\bsalve na memoria\b', r'\bcommit\b', r'\bpush\b',
]
l0_all = l0_questions + l0_cosmetic + l0_meta

# ---- L2 ----
l2_scope = [
    r'\bfeature\b', r'\bfuncionalidade\b', r'\bsistema completo\b',
    r'\bsistema\b', r'\bnew system\b', r'\bmodulo novo\b', r'\bnew module\b',
    r'\bservico\b', r'\bservice\b', r'\bendpoint novo\b', r'\bnew endpoint\b',
    r'\bnovo componente\b', r'\bnew component\b', r'\bintegracao\b',
    r'\bintegration\b', r'\bapi nova\b', r'\bnew api\b',
]
l2_architecture = [
    r'\barquitetura\b', r'\barchitecture\b', r'\bredesign\b',
    r'\breestrutura\b', r'\brestructure\b', r'\bmigracao\b',
    r'\bmigration\b', r'\bmigrar\b', r'\bmigrate\b', r'\breescreve\b',
    r'\brewrite\b', r'\bdo zero\b', r'\bfrom scratch\b',
    r'\bsubstituir sistema\b', r'\breplace system\b',
]
l2_flow = [
    r'\bpipeline\b', r'\bworkflow\b', r'\borquestracao\b',
    r'\borchestration\b', r'\bfluxo completo\b', r'\bfull flow\b',
]
l2_planning = [
    r'\bplano\b', r'\bplan\b', r'\bprd\b', r'\bspec\b', r'\bdesign\b',
    r'\bproposta\b', r'\bproposal\b', r'\bestrategia\b', r'\bstrategy\b',
    r'\bplaneje\b', r'\bdesenhe\b', r'\bprojete\b', r'\barquitete\b',
    r'\belabore\b', r'\barchitect\b',
]
l2_scale = [
    r'\btoda a base\b', r'\bentire codebase\b', r'\btodo o projeto\b',
    r'\bwhole project\b', r'\brefatora tudo\b', r'\brefactor everything\b',
    r'\bem todos os\b', r'\bacross all\b', r'\bbase inteira\b',
    r'\bde ponta a ponta\b', r'\bend-to-end\b',
]
l2_composite = [
    r'\bcri[ae] um\b', r'\bbuild an app\b', r'\bconstrua\b',
    r'\bcriar um\b', r'\bimplemente do zero\b', r'\bimplement from scratch\b',
    r'\bmonte um\b', r'\bset up\b',
]
l2_multidomain = [
    r'banco.*api.*tela', r'database.*api.*ui',
    r'frontend.*backend', r'schema.*endpoint',
]
l2_all = (l2_scope + l2_architecture + l2_flow + l2_planning
          + l2_scale + l2_composite + l2_multidomain)

# ---- L1 ----
l1_bug = [
    r'\bbug\b', r'\bfix\b', r'\berro\b', r'\berror\b', r'\bquebrou\b',
    r'\bbroke\b', r'\bfalha\b', r'\bfailure\b', r'\btraceback\b',
    r'\bexception\b', r'\bcrash\b', r'\bnao funciona\b', r'\bnot working\b',
    r'\bparou de funcionar\b', r'\bstopped working\b', r'\bdeu ruim\b',
    r'\bcomportamento errado\b', r'\bwrong behavior\b', r'\binesperado\b',
    r'\bunexpected\b', r'\bregressao\b', r'\bregression\b',
]
l1_refactor = [
    r'\brefatora\b', r'\brefactor\b', r'\blimpa\b', r'\bclean\b',
    r'\bmelhora\b', r'\bimprove\b', r'\bsimplifica\b', r'\bsimplify\b',
    r'\bextrai\b', r'\bextract\b', r'\bsepara\b', r'\bseparate\b',
    r'\bdesacopla\b', r'\bdecouple\b', r'\breorganiza\b', r'\breorganize\b',
    r'\breduz duplicacao\b', r'\breduce duplication\b', r'\bmove para\b',
    r'\bmove to\b', r'\botimiza\b', r'\boptimize\b',
]
l1_small_feature = [
    r'\badiciona\b', r'\badd\b', r'\binclui\b', r'\binclude\b',
    r'\bimplementa\b', r'\bimplement\b',
]
l1_all = l1_bug + l1_refactor + l1_small_feature

# ============================================================================
# Classification logic
# ============================================================================
def any_match(patterns, text):
    return any(re.search(p, text) for p in patterns)

has_l0 = any_match(l0_all, msg)
has_l1 = any_match(l1_all, msg)
has_l2 = any_match(l2_all, msg)

# L0 only if NO L1/L2 keywords present
if has_l0 and not has_l1 and not has_l2:
    level = "L0"
elif has_l2:
    # L2 wins on tie with L1
    level = "L2"
elif has_l1:
    level = "L1"
else:
    # Default when nothing matches
    level = "L1"

# ============================================================================
# Type classification
# ============================================================================
has_bug = any_match(l1_bug, msg)
has_refactor = any_match(l1_refactor, msg)
has_arch = any_match(l2_architecture, msg)

if has_bug:
    task_type = "bug"
elif has_refactor:
    task_type = "refactor"
elif has_arch:
    task_type = "architecture"
else:
    task_type = "feature"

classification = f"{level}-{task_type}"

# ============================================================================
# Pipeline mapping
# ============================================================================
PIPELINES = {
    # Harness v3 — SDD pipelines. Nomes = FASES (estaveis); o harness-workflow
    # mapeia cada fase ao mecanismo real (skill direta OU Workflow de fan-out).
    # Zero skills fantasma: removidos triage-issue, request-refactor-plan,
    # improve-codebase-architecture, prd-to-plan, execucao.
    "L1-feature":      ["write-spec-light", "tdd", "verify-against-spec"],
    "L1-bug":          ["systematic-debugging", "tdd", "verify"],
    "L1-refactor":     ["write-spec-light", "tdd", "verify-against-spec"],
    "L2-feature":      ["discuss", "brainstorming", "write-spec", "grill-me", "design-doc", "validate-plan", "tdd", "verify-against-spec"],
    "L2-bug":          ["systematic-debugging", "grill-me", "tdd", "verify"],
    "L2-refactor":     ["discuss", "write-spec", "grill-me", "design-doc", "validate-plan", "tdd", "verify-against-spec"],
    "L2-architecture": ["discuss", "brainstorming", "write-spec", "grill-me", "design-doc", "validate-plan", "tdd", "verify-against-spec"],
}
# L0 has no pipeline
pipeline = PIPELINES.get(classification, [])

# ============================================================================
# Generate task_id and timestamps
# ============================================================================
now = datetime.now(timezone.utc)
task_id = now.strftime("t-%Y%m%d-%H%M%S")
started_at = now.isoformat()

# ============================================================================
# Build and write state.json
# ============================================================================
status = "done" if level == "L0" else "active"

# classification_meta: camada regex (suggested). Para L0 o regex decide sozinho
# (final=suggested). Para L1+ a confirmacao semantica (wf-classify-semantic)
# preenche final/source/agreed depois; ate la final=None. agreed=None em ambos
# pois so a camada semantica avalia concordancia (entra no loop de accuracy).
classification_meta = {
    "suggested": classification,
    "final": classification if level == "L0" else None,
    "source": "regex",
    "confidence": None,
    "agreed": None,
}

new_state = {
    "task_id": task_id,
    "schema_version": 3,
    "classification": classification,
    "classification_meta": classification_meta,
    "status": status,
    "pipeline": pipeline,
    "current_step": None,
    "artifacts_so_far": [],
    "started_at": started_at,
}

with open(state_file, "w", encoding="utf-8") as f:
    json.dump(new_state, f, indent=2, ensure_ascii=False)

# ============================================================================
# Reset counter file
# ============================================================================
counter = {"count": 0, "files": [], "task_id": task_id}
with open(counter_file, "w", encoding="utf-8") as f:
    json.dump(counter, f, indent=2, ensure_ascii=False)

# ============================================================================
# Emit classification block
# ============================================================================
pipeline_display = " -> ".join(pipeline) if pipeline else "none"

if level == "L0":
    # L0: simple tag, no workflow needed
    print(f"""<harness-classification>
task_id: {task_id}
classification: {classification}
level: {level}
type: {task_type}
status: {status}
pipeline: {pipeline_display}
started_at: {started_at}
</harness-classification>""")
else:
    # L1+: emit systemMessage JSON to force workflow activation
    output = json.dumps({
        "systemMessage": (
            f"HARNESS v3 CLASSIFIED: {classification}. "
            f"Pipeline: {pipeline_display}. "
            f"Task ID: {task_id}. "
            f"You MUST invoke the harness-workflow skill NOW using the Skill tool "
            f"with skill='harness-workflow'. Do NOT skip this step. "
            f"Do NOT answer the user directly — invoke the skill FIRST."
        )
    })
    print(output)
PYEOF

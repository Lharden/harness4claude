#!/usr/bin/env bash
# harness-classify.sh — UserPromptSubmit hook for Harness v3
# Classifies tasks as L0/L1/L2, detects type, manages pipeline state.
# Reads JSON from stdin (field: user_message or content).
# Emits <harness-classification> or <harness-continuation> blocks.

set -euo pipefail

# Interpretador nomeado (master-harness). Sem marcador, `python` — o de sempre.
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
PY="python"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND="$(cat "$_MH_MARCA" 2>/dev/null | tr -d '\r\n')"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi

# Force UTF-8 for all Python subprocesses (fix for charmap codec bug on Windows)
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LANG=C.UTF-8

: "${HARNESS_DIR:=$HOME/.claude/harness}"
export HARNESS_DIR   # necessario: o python inline abaixo le via os.environ
STATE_FILE="$HARNESS_DIR/state.json"
COUNTER_FILE="$HARNESS_DIR/.session-files-count"

# ---------------------------------------------------------------------------
# Heartbeat de disparo
# ---------------------------------------------------------------------------
# Registra que o CLI HOST chamou este hook. O smoke-test do health-check prova
# que os hooks FUNCIONAM quando executados; nao prova que ainda sao CHAMADOS. Se
# o host renomear um evento, os hooks ficam inertes e todo diagnostico continua
# verde — a falha silenciosa que originou esta auditoria, um nivel acima.
#
# Fica ANTES de qualquer guard: o que se mede aqui e a chamada, nao o trabalho.
# Sem processo — EPOCHSECONDS e builtin do bash 5, entao isto nao adiciona
# latencia a um hook que roda a cada prompt.
# Leitura e veredito: scripts/check_hook_liveness.py.
{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/UserPromptSubmit"; } 2>/dev/null || true

# Ensure harness dir exists
mkdir -p "$HARNESS_DIR"

# REQ-F12 (mitiga R10): um HARNESS_DIR vazado do ambiente redirecionaria o
# estado de producao em silencio. O override e legitimo, mas nunca invisivel.
if [ "$HARNESS_DIR" != "$HOME/.claude/harness" ]; then
    printf 'HARNESS_DIR override ativo: %s (default: %s/.claude/harness)\n' \
        "$HARNESS_DIR" "$HOME" >> "$HARNESS_DIR/debug-classify.log" 2>/dev/null || true
fi

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

# Single Python call: extract session + cwd + message + normalize unicode.
# Formato: primeira linha = session_id, segunda = cwd, resto = mensagem.
# O cwd vem primeiro porque nunca contem quebra de linha, enquanto a mensagem
# pode — inverter a ordem tornaria o split ambiguo.
# Errors logged to debug file instead of silently swallowed
EXTRACT="$(printf '%s' "$INPUT" | "$PY" -c "
import sys, json, unicodedata
try:
    data = json.load(sys.stdin)
    msg = data.get('prompt', data.get('user_prompt', data.get('user_message', data.get('content', ''))))
    if not msg or not msg.strip():
        sys.exit(0)
    print((data.get('session_id') or '').replace('\n', ' '))
    print((data.get('cwd') or '').replace('\n', ' '))
    text = msg.lower().strip()
    nfkd = unicodedata.normalize('NFKD', text)
    clean = ''.join(c for c in nfkd if not unicodedata.combining(c))
    print(clean)
except Exception as e:
    import os
    _hd = os.environ.get('HARNESS_DIR') or os.path.join(os.path.expanduser('~'), '.claude', 'harness')
    debug = os.path.join(_hd, 'debug-classify.log')
    with open(debug, 'a', encoding='utf-8') as f:
        f.write(f'{e}\n')
    sys.exit(1)
" || echo "")"

if [ -z "$EXTRACT" ]; then
    exit 0
fi

# Split por expansao de parametro, NAO por pipe para head/tail: com `set -o
# pipefail`, `head -n 1` fecha o pipe e o `printf` de um prompt grande morre com
# SIGPIPE, derrubando o hook com exit 141 (visto com o prompt de ~160KB do
# sumarizador). Sem pipe tambem economiza dois processos por prompt.
case "$EXTRACT" in *$'\n'*$'\n'*) ;; *) exit 0 ;; esac
SESSION_ID="${EXTRACT%%$'\n'*}"
SESSION_ID="${SESSION_ID%$'\r'}"
EXTRACT_REST="${EXTRACT#*$'\n'}"
SESSION_CWD="${EXTRACT_REST%%$'\n'*}"
# print() do Python no Windows emite \r\n. Sem tirar o \r, o cwd vira um caminho
# que nao existe: find_repo_root falha, cai no cwd cru, e a raiz de um repo e um
# subdiretorio dele geram buckets DIFERENTES — o estado de um projeto fragmenta.
SESSION_CWD="${SESSION_CWD%$'\r'}"
MSG_LOWER="${EXTRACT_REST#*$'\n'}"

if [ -z "$MSG_LOWER" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. Delegate everything to a single Python script via env vars
# ---------------------------------------------------------------------------
# Convert MSYS paths to Windows paths for Python
SCRIPTS_DIR="${HOOK_DIR_REL}/../scripts"
HOOKS_DIR="${HOOK_DIR_REL}"
if command -v cygpath &>/dev/null; then
    export HARNESS_STATE_FILE="$(cygpath -w "$STATE_FILE")"
    export HARNESS_COUNTER_FILE="$(cygpath -w "$COUNTER_FILE")"
    export HARNESS_SCRIPTS_DIR="$(cygpath -w "$SCRIPTS_DIR")"
    export HARNESS_HOOKS_DIR="$(cygpath -w "$HOOKS_DIR")"
    export HARNESS_ROOT_DIR="$(cygpath -w "$HARNESS_DIR")"
else
    export HARNESS_STATE_FILE="$STATE_FILE"
    export HARNESS_COUNTER_FILE="$COUNTER_FILE"
    export HARNESS_SCRIPTS_DIR="$SCRIPTS_DIR"
    export HARNESS_HOOKS_DIR="$HOOKS_DIR"
    export HARNESS_ROOT_DIR="$HARNESS_DIR"
fi
export HARNESS_MSG_LOWER="$MSG_LOWER"
export HARNESS_SESSION_CWD="$SESSION_CWD"
export HARNESS_SESSION_ID="$SESSION_ID"
export PYTHONUTF8=1

"$PY" << 'PYEOF'
import os, re, json, sys
from datetime import datetime, timezone


def _atomic_write_json(path, data):
    """Escreve JSON de forma atomica: tmp no mesmo dir -> flush+fsync -> os.replace.

    Evita state.json/counter corrompido se o processo morrer no meio do dump (a
    janela existia porque o release do lock e via trap EXIT). os.replace e rename
    atomico no mesmo filesystem (NTFS via Git Bash, ext4, APFS).
    """
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _falar(kind, texto):
    """Emite pelo canal que chega ao modelo.

    Ate 2026-09-01 tudo aqui saia por `systemMessage`, que e canal de UI:
    `HARNESS v3 CLASSIFIED` foi emitido 81 vezes em 47 sessoes e
    `Skill(harness-workflow)` foi invocada em zero. O emissor central resolve
    o canal pelo evento e registra em emissions.jsonl.
    """
    try:
        import importlib.util
        _hooks = os.environ.get("HARNESS_HOOKS_DIR") or ""
        spec = importlib.util.spec_from_file_location(
            "harness_emit", os.path.join(_hooks, "emit.py"))
        if spec is None or spec.loader is None:
            raise ImportError
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Emitter("UserPromptSubmit", hook="classify",
                    session_id=os.environ.get("HARNESS_SESSION_ID") or "",
                    cwd=os.environ.get("HARNESS_SESSION_CWD") or ""
                    ).add(kind, texto + _bloco_de_presenca()).flush()
    except Exception:
        # Sem o emissor, o canal provado escrito a mao. Perder a classificacao
        # por causa do mensageiro repetiria a falha que isto veio consertar.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": texto + _bloco_de_presenca(),
        }}, ensure_ascii=False))


# ============================================================================
# Presenca: quem mais trabalha neste escopo agora (master-harness)
# ============================================================================
# Duas funcoes, e as duas sao best-effort e SILENCIOSAS. Presenca e informacao
# util; hook que morre por causa dela custaria o turno do usuario, que e pior
# que nao ter presenca nenhuma.
#
# O `mh` NAO e importado diretamente: o caminho vem do marcador
# `~/.master-harness/mh-root`, mesmo padrao do `plugin-root` deste harness.
# Dependencia dura de um pacote que pode nao estar instalado transformaria
# "nao ha presenca" em "o hook morreu"; com o marcador, a ausencia do `mh` e
# simplesmente a ausencia do marcador.


def _mh():
    """Importa `mh.presenca` pelo marcador, ou devolve (None, None)."""
    try:
        casa = os.environ.get("MASTER_HARNESS_HOME") or os.path.join(
            os.path.expanduser("~"), ".master-harness")
        with open(os.path.join(casa, "mh-root"), encoding="utf-8") as fh:
            raiz = fh.readline(4096).strip()
        if not raiz or not os.path.isdir(raiz):
            return (None, None)
        if raiz not in sys.path:
            sys.path.insert(0, raiz)
        from mh import presenca as _p
        return (_p, casa)
    except Exception:
        return (None, None)


def _marcar_presenca():
    """Anuncia esta sessao. Roda ANTES de qualquer saida antecipada.

    Medido em 2026-09-05: `classified` 63 contra `continuing` 61 — metade dos
    prompts sai em `raise SystemExit(0)` antes de chegar ao fim deste arquivo.
    Marcar la embaixo faria a baliza envelhecer em toda sessao que continua um
    pipeline, que sao justamente as sessoes que estao trabalhando.
    """
    try:
        _p, casa = _mh()
        if _p is None:
            return
        _p.marcar(
            casa,
            host="claude",
            session_id=os.environ.get("HARNESS_SESSION_ID") or "",
            escopo=_escopo_atual(),
        )
    except Exception:
        pass


def _escopo_atual():
    """O escopo desta sessao, pela fonte vendorizada, sem importar o `mh`.

    Antes esta funcao montava o prefixo a mao:

        ("repo:" if _raiz(_cwd) else "dir:") + _slug(_cwd)

    Duas coisas erradas nisso. A primeira e que era a quarta reimplementacao da
    mesma regra. A segunda e que ela **ignorava `HARNESS_SCOPE=global`**: com a
    variavel ligada, o `mh quem` procurava balizas em `global:maquina` enquanto
    este hook as escrevia em `repo:<slug>`, e a presenca reportava ninguem em
    silencio. `_escopo.de_caminho` honra a variavel, entao os dois passam a
    olhar para o mesmo lugar.

    `_escopo.py` e copia entregue por `mh identidade semear`, e `mh identidade
    check` reprova se ela derivar da fonte.

    **O `sys.path` e montado aqui dentro, e nao herdado.** Em 2026-09-06 a
    presenca ficou horas sem funcionar em producao por causa disto: esta funcao
    e chamada por `_marcar_presenca()` na linha 282, e o
    `sys.path.insert(0, HARNESS_SCRIPTS_DIR)` do hook so acontece na 292 — dez
    linhas depois. O `from harness_paths import ...` levantava `ModuleNotFound`,
    o `except Exception: pass` de quem chama engolia, e nao havia baliza.

    Nao apareceu em teste porque os testes passavam `PYTHONPATH` no ambiente do
    subprocess. Em producao ele esta VAZIO — conferido no ambiente desta sessao,
    no `settings.json` e no `settings.local.json`. O teste media uma condicao
    que a producao nao tem: blindava em vez de detectar, que e exatamente o
    enxerto que os juizes exigiram do painel.

    Montar o caminho aqui torna a funcao independente de onde ela e chamada, que
    e mais forte que mover a chamada para depois da linha 292.
    """
    _scripts = os.environ.get("HARNESS_SCRIPTS_DIR") or ""
    if _scripts and _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    import _escopo
    return _escopo.de_caminho(os.environ.get("HARNESS_SESSION_CWD") or "").valor


def _bloco_de_presenca():
    """A linha da vizinhanca, ou "" quando nao ha o que afirmar.

    **So faz afirmacao positiva.** `sozinho` e `nao_verificado` saem vazios, e
    isso nao e violacao de L-09: o hook nunca diz "voce esta sozinho". Quem
    distingue os tres estados e `mh quem`, que sai 0/1/2 — esta superficie so
    fala quando ha alguem.
    """
    try:
        _p, casa = _mh()
        if _p is None:
            return ""
        r = _p.vizinhos(casa, _escopo_atual(), host="claude")
        if r.resposta != _p.ACOMPANHADO:
            return ""
        return "\n\nHARNESS v3 VIZINHANCA: " + r.linha()
    except Exception:
        return ""


msg = os.environ["HARNESS_MSG_LOWER"]

_marcar_presenca()

# ============================================================================
# Escopo do estado: bucket do projeto (default) ou raiz global
# ============================================================================
# Ate 2026-07-28 havia UM state para toda a maquina: o contador global chegou a
# 130 arquivos sob um unico task_id, misturando dois projetos, e a promocao
# L0->L1 de um repo era disparada por edicoes em outro. Ver scripts/harness_paths.py.
# Fallback para a raiz: se a resolucao falhar, o comportamento antigo e melhor
# que nao classificar.
sys.path.insert(0, os.environ["HARNESS_SCRIPTS_DIR"])
try:
    from harness_paths import ensure_state_dir
    _sd = str(ensure_state_dir(os.environ.get("HARNESS_ROOT_DIR") or None,
                               os.environ.get("HARNESS_SESSION_CWD") or None,
                               session_id=os.environ.get("HARNESS_SESSION_ID") or None))
    state_file = os.path.join(_sd, "state.json")
    counter_file = os.path.join(_sd, ".session-files-count")
except Exception:
    state_file = os.environ["HARNESS_STATE_FILE"]
    counter_file = os.environ["HARNESS_COUNTER_FILE"]

# ============================================================================
# Guard de automação por ASSINATURA (incidente 2026-06-12, t-20260612-155238)
# ============================================================================
# Sessões headless (ex.: sumarizador do remember) começam com um preâmbulo
# DETERMINÍSTICO que nenhum humano digita como tarefa. Esse prompt tem ~16KB e
# caía na "dead zone" entre MAX_SWITCH_LEN (1500) e MAX_CLASSIFY_LEN (30000):
# passava o guard de comprimento e, sem pipeline ativo, criava task fantasma no
# state.json GLOBAL. Assinatura > comprimento: pega o sumarizador em QUALQUER
# tamanho e sai ANTES de ler/escrever o state — nunca cria/toca task, independente
# do status (msg já vem lowercased + NFKD-normalizado do extrator).
#
# 2026-09-03: a mesma classe de defeito, por outra porta. Quando um comando de
# background termina, o host reentrega a notificação pelo caminho de um prompt
# humano — e o regex, que classifica por forma e comprimento, viu um bloco XML
# de ~470 chars e abriu pipeline de ONZE fases para um evento que ninguém
# pediu. Pior que a task fantasma: abrir task nova marca a anterior como
# abandonada, e nesta máquina isso já aconteceu com trabalho verificado.
#
# O critério para entrar nesta lista é estreito: a assinatura tem de ser texto
# que o HOST emite e que nenhum humano digitaria como pedido. Mensagem de outra
# sessão (`<cross-session-message`) NÃO entra — ela pode carregar trabalho real.
AUTOMATION_SIGNATURES = (
    "you are summarizing a claude code session",
    "<task-notification>",
    "[system notification - not user input]",
    "[cross-session idle notice]",
)

# Assinaturas que so valem no INICIO do prompt, testadas com startswith.
#
# 2026-09-04: colhidos 1.195 pares (prompt, arquivos escritos no turno) de 357
# transcripts — `scripts/harvest_classify_labels.py`. Dos 1.040 prompts que o
# regex classificou L1+, 713 (69%) nao produziram arquivo nenhum: pipeline
# aberto em vazio. A hipotese de que isso se concentrava no prompt curto humano
# foi medida e MORREU — a faixa 0-20 chars tem a MENOR taxa (0.544) e a de
# 2000+ a maior (0.908); nenhum corte por comprimento passou de precisao 0.669
# contra uma taxa base de 0.686, ou seja, comprimento nao carrega informacao
# nenhuma sobre o rotulo.
#
# O que a amostragem achou no lugar foi outra automacao sem assinatura: harness
# cientifico de outro projeto ("You are running screening stage 1...", ~21KB,
# passa o backstop de 30000) e texto que o HOST reentrega pelo caminho do
# prompt humano. Estas seis, medidas sobre o mesmo corpus: precisao 1.000,
# ZERO falso positivo em 1.040 pares, cobertura conjunta 0.224.
#
# Por que prefixo e nao substring: a lista acima usa `in` porque `<task-
# notification>` e inequivoco em qualquer posicao. "you are running" nao e —
# cabe no meio de uma frase humana. Ancorar deu a MESMA cobertura medida (101
# disparos das duas formas) com menos superficie, entao nao ha o que trocar.
#
# `you are extracting` e `you are evaluating` foram testados e NAO entraram:
# zero disparos. Padrao que nunca dispara nao custa nada e nao serve para nada.
AUTOMATION_PREFIXES = (
    "[request interrupted by user]",
    "continue from where you left off",
    "you are running",
    "you are screening",
    "you are auditing",
    "you are estimating",
)
if any(sig in msg for sig in AUTOMATION_SIGNATURES) or msg.startswith(AUTOMATION_PREFIXES):
    raise SystemExit(0)

# ============================================================================
# Guards anti-automação por COMPRIMENTO (incidente t-20260612-034438)
# ============================================================================
# Backstop para automações sem assinatura conhecida que colam blobs gigantes:
# - MAX_CLASSIFY_LEN: acima disso é colagem/automação — nunca escrever state.
# - MAX_SWITCH_LEN: derrubar pipeline ativo exige comando humano CURTO.
MAX_CLASSIFY_LEN = 30000
MAX_SWITCH_LEN = 1500

if len(msg) > MAX_CLASSIFY_LEN:
    raise SystemExit(0)

# ============================================================================
# Task-switch detection
# ============================================================================
# \b obrigatório: 'cancela' sem boundary casava 'cancelamento'; num extrato
# de conversa qualquer frase aparece ('ou seguimos para outra coisa?').
SWITCH_PATTERNS = (
    r'\bnova tarefa\b|\bnew task\b|\bcancela\b|\bcancele\b|\boutra coisa\b|'
    r'\bswitch to\b|\besquece isso\b|\bdeixa pra la\b|\bnevermind\b|'
    r'\bforget that\b|\bmuda de assunto\b|\bmudar de assunto\b'
)
is_task_switch = (
    len(msg) <= MAX_SWITCH_LEN
    and bool(re.search(SWITCH_PATTERNS, msg, re.IGNORECASE))
)

# ============================================================================
# TTL: disjuntor do pipeline abandonado
# ============================================================================
# Auditoria 2026-07-28: o bloco de continuacao logo abaixo sai ANTES de
# classificar sempre que status == "active". Como nada devolvia o state para
# "idle", uma task de 24/07 ficou ativa 4 dias e bloqueou TODA classificacao
# nova em TODOS os projetos (o state e global).
#
# Posicao no arquivo e deliberada: DEPOIS dos guards de automacao e de
# comprimento. Prompt de automacao (sumarizador, colagem gigante) nunca pode
# mutar o state — nem para expirar. Rodar isto antes dos guards reintroduziria
# exatamente o incidente t-20260612-034438 que aqueles guards fecham.
try:
    from expire_stale_pipeline import default_ttl_hours, expire
    expire(os.path.dirname(state_file), default_ttl_hours(),
           signals_dir=os.environ.get("HARNESS_ROOT_DIR") or None)
except Exception:
    pass  # TTL e best-effort: falhar aqui nunca pode bloquear o prompt

# ============================================================================
# Check active pipeline in state.json
# ============================================================================
has_active = False
try:
    from continuation_policy import should_continue
    with open(state_file, encoding='utf-8') as f:
        state = json.load(f)
    if should_continue(state):
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
    gate_display = state.get('pending_gate')
    gate_instruction = (
        f" Resolve pending human gate {gate_display} through skill='harness-workflow'."
        if gate_display else
        " Continue the active pipeline by invoking skill='harness-workflow'."
    )
    _falar("continuing", (
        f"HARNESS v3 CONTINUING: {classification} (task {task_id}). "
        f"Current step: {step_display}. Pipeline: {pipe_display}. "
        f"{gate_instruction}"
    ))
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
from classify_prompt import classify_prompt

level, task_type = classify_prompt(msg)
classification = f"{level}-{task_type}"

# ============================================================================
# Pipeline mapping
# ============================================================================
PIPELINES = {
    # Harness v3 — SDD pipelines. Nomes = FASES (estaveis); o harness-workflow
    # mapeia cada fase ao mecanismo real (skill direta OU Workflow de fan-out).
    # Zero skills fantasma: removidos triage-issue, request-refactor-plan,
    # improve-codebase-architecture, prd-to-plan, execucao.
    "L0-question":     [],
    "L1-feature":      ["write-spec-light", "tdd", "verify-against-spec"],
    "L1-bug":          ["systematic-debugging", "tdd", "verify"],
    "L1-refactor":     ["write-spec-light", "tdd", "verify-against-spec"],
    "L1-review":       ["code-review", "verify"],
    "L1-docs":         ["source-selection", "documentation", "verify"],
    "L2-feature":      ["discuss", "brainstorming", "graph-context", "write-spec", "grill-me", "approve-spec", "design-doc", "validate-plan", "approve-plan", "tdd", "verify-multimodel"],
    "L2-bug":          ["systematic-debugging", "graph-context", "grill-me", "tdd", "verify"],
    "L2-refactor":     ["discuss", "graph-context", "write-spec", "grill-me", "approve-spec", "design-doc", "validate-plan", "approve-plan", "tdd", "verify-multimodel"],
    "L2-architecture": ["discuss", "brainstorming", "graph-context", "write-spec", "grill-me", "approve-spec", "design-doc", "validate-plan", "approve-plan", "tdd", "verify-multimodel"],
    "L2-review":       ["graph-context", "code-review", "verify-multimodel"],
    "L2-docs":         ["source-selection", "graph-context", "documentation", "verify-against-spec"],
}
# Fonte unica: a arvore de contrato que o `arvore_do_contrato` resolver — a
# mesma que o adaptador e o `confirm_classification.py` leem.
#
# Ate 2026-09-05 este bloco lia `HARNESS_SCRIPTS_DIR/pipelines.json`, e o
# comentario aqui dizia "fonte unica". Nao era: havia uma copia byte-identica em
# `contract/pipelines.json` (md5 97ab5894...) com outros leitores, e o caminho
# que de fato moldava o comportamento — este — nunca tocava `contract/`. Duas
# copias, e a autoridade decidida por um comentario.
#
# O literal acima permanece como fallback, e a razao nao mudou: num install
# quebrado, classificar com o pipeline conhecido vale mais do que nao classificar.
try:
    import sys as _sys
    _sys.path.insert(0, os.environ["HARNESS_SCRIPTS_DIR"])
    from contract_adapter import arvore_do_contrato as _arvore
    with open(os.path.join(str(_arvore()[0]), "pipelines.json"), encoding="utf-8") as _f:
        _loaded = json.load(_f).get("pipelines")
    if _loaded:
        PIPELINES = _loaded
except Exception:
    pass
# L0 has no pipeline
pipeline = PIPELINES.get(classification, [])
# Guard defensivo: um {level}-{type} L1+ sem pipeline mapeado nao deve seguir
# silenciosamente (status ficaria 'active' sem nenhuma fase a executar). Hoje
# inalcancavel (qualquer keyword de arquitetura forca L2), mas protege contra
# novos types/keywords adicionados no futuro.
pipeline_unmapped = level != "L0" and not pipeline

# ============================================================================
# Generate task_id and timestamps
# ============================================================================
now = datetime.now(timezone.utc)
# Microssegundos (%f) SEM traco extra: garante unicidade entre prompts no mesmo
# segundo (evita colisao de task_id + sobrescrita destrutiva no record_signal.py)
# e ainda casa o pattern do state.schema.json (^t-[0-9]{8}-[0-9A-Za-z]+$).
task_id = now.strftime("t-%Y%m%d-%H%M%S%f")
started_at = now.isoformat()

# ============================================================================
# Build and write state.json
# ============================================================================
status = "done" if level == "L0" else "active"

# classification_meta: camada regex (suggested). Para L0 o regex decide sozinho
# (final=suggested). Para L1+ quem preenche final/source/agreed e a skill
# harness-workflow, chamando scripts/confirm_classification.py no passo 2 do
# protocolo; ate la final=None. agreed=None em ambos, pois so a camada semantica
# avalia concordancia — e e ela que alimenta avg_classify_accuracy.
# (Ate 2026-07-28 este comentario citava um "wf-classify-semantic" que nunca
# existiu, e o protocolo mandava editar o JSON a mao — o que nao era cumprido:
# 100% das tasks tinham agreed=null e a metrica de accuracy nunca saiu de zero.)
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
    # Cache do prompt (normalizado) que originou a classificação — fonte única
    # legítima para auditoria e reclassificação. Tool output JAMAIS entra aqui.
    "prompt_len": len(msg),
    "prompt_excerpt": msg[:300],
}

_atomic_write_json(state_file, new_state)

# Dual-write transacional: state.json continua sendo a projecao legivel pelos
# hooks legados; harness.db valida unicidade de scope, revisao, gates e evidencia.
try:
    from transactional_state import HarnessDatabase
    transactional = HarnessDatabase(os.path.dirname(state_file)).start_task(
        scope_id=os.path.dirname(state_file),
        legacy_level=classification,
        tier=level,
        kind=task_type,
        pipeline=pipeline,
        prompt=msg,
        task_id=task_id,
    )
    new_state.update({
        "revision": transactional["revision"],
        "code_revision": transactional["code_revision"],
        "owner_epoch": transactional["owner_epoch"],
        "verified": transactional["verified"],
        "pending_gate": transactional["pending_gate"],
        "scope_id": transactional["scope_id"],
    })
    _atomic_write_json(state_file, new_state)
except Exception as exc:
    # O prompt continua; health-check e contract adapter tornam a degradacao visivel.
    try:
        with open(os.path.join(os.path.dirname(state_file), "transactional-state-error.log"), "a", encoding="utf-8") as f:
            f.write(f"{started_at} {exc}\n")
    except Exception:
        pass

# ============================================================================
# Espelho de coordenacao (master-harness, degrau `store_mode: dual_write`)
# ============================================================================
# O painel de tres arquiteturas escolheu spool append-only justamente porque a
# unica escrita que Claude e Codex executam de forma identica e anexar a um
# arquivo: sem lock, sem rede, sem API de host. Abrir uma segunda conexao SQLite
# aqui seria o desenho obvio e o errado — custo no turno comum e contencao entre
# os dois hosts na mesma maquina.
#
# NAO importa `mh`. O escopo e cunhado com `harness_paths`, que ja esta no path
# deste hook, e o valor sai byte-identico ao que `mh.escopo` produziria — isso e
# provado em master-harness por `test_preludio_spool.py`, nao suposto aqui.
#
# Silencioso e best-effort por construcao: hook que morre por causa do canal de
# coordenacao e pior que hook sem canal nenhum.
#
# Sob pytest, a casa PADRAO nao recebe. Medido em 2026-09-05, minutos depois de
# o espelho entrar: as suites dos dois harness escreveram 44 linhas no spool de
# producao, com `scope_id: "dir:unknown"` e `session_id: null`. Evento falso num
# ledger de coordenacao e pior que evento ausente — ele parece trabalho de
# verdade. Casa EXPLICITA continua recebendo, porque os testes de integracao do
# canal precisam escrever em algum lugar; o que se recusa e o caso em que a
# suite polui sem ter pedido.
try:
    _degraus = ["shadow", "dual_read", "dual_write", "new_primary", "legacy_ro"]
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("MASTER_HARNESS_HOME"):
        raise RuntimeError("suite sem MASTER_HARNESS_HOME proprio: nao polui o spool de producao")
    _casa = os.environ.get("MASTER_HARNESS_HOME") or os.path.join(os.path.expanduser("~"), ".master-harness")
    with open(os.path.join(_casa, "flags.json"), encoding="utf-8") as _f:
        _degrau = ((json.load(_f) or {}).get("flags") or {}).get("store_mode") or _degraus[0]
    if _degraus.index(_degrau) >= _degraus.index("dual_write"):
        import uuid as _uuid
        from harness_paths import find_repo_root as _raiz, project_slug as _slug, session_slug as _ss
        _cwd = os.environ.get("HARNESS_SESSION_CWD") or ""
        _especie = "repo" if _raiz(_cwd) else "dir"
        _sess = _ss(os.environ.get("HARNESS_SESSION_ID") or "")
        _reg = {
            "dados": {"kind": task_type, "level": level, "pipeline": pipeline, "task_id": task_id},
            "epoch": int(new_state.get("owner_epoch") or 1),
            "event_id": _uuid.uuid4().hex,
            "host": "claude",
            "scope_id": f"{_especie}:{_slug(_cwd)}",
            "session_id": f"claude:{_sess}" if _sess else None,
            "tipo": "task.start",
            "ts": started_at,
        }
        # Mesma regra de `mh.spool.caminho_outbox(casa, "claude", <slug nu>)`:
        # o prefixo do host ja esta no nome do arquivo, entao o slug entra nu.
        #
        # Sem sessao, cai no pid do ESCRITOR e nao num rotulo fixo. `sem-sessao`
        # colocava TODAS as sessoes sem id no mesmo arquivo, e e justamente a
        # exclusividade do arquivo que torna `open(path,'a')` seguro sem lock —
        # a garantia deixava de valer em silencio. Medido: 9,4% das emissoes
        # desta maquina nao tem session_id (todas do session_start).
        _token = _sess or f"pid-{os.getpid()}"
        _nome = "".join(c if c.isalnum() or c in "-._" else "-" for c in f"claude-{_token}")[:120]
        _out = os.path.join(_casa, "spool", "outbox", _nome + ".ndjson")
        os.makedirs(os.path.dirname(_out), exist_ok=True)
        # Retentativa contra a janela do `os.replace` do DRENO. Medido por teste
        # de carga em 2026-09-06: com 12 sessoes escrevendo e um dreno em
        # paralelo, 0,56% a 1,11% dos appends morriam com `PermissionError`
        # [Errno 13] — e o evento sumia em silencio, porque quem chama ignora.
        import time as _time
        _linha = json.dumps(_reg, ensure_ascii=False, sort_keys=True) + "\n"
        for _espera in (0.0, 0.002, 0.01):
            if _espera:
                _time.sleep(_espera)
            try:
                with open(_out, "a", encoding="utf-8", newline="\n") as _f:
                    _f.write(_linha)
                break
            except OSError:
                continue
except Exception:
    pass

# ============================================================================
# Reset counter file
# ============================================================================
counter = {"count": 0, "files": [], "task_id": task_id}
_atomic_write_json(counter_file, counter)

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
</harness-classification>{_bloco_de_presenca()}""")
    # Aqui a presenca sai por stdout CRU, e nao pelo emissor — o caminho L0 nao
    # chama `_falar`. O canal esta certo mesmo assim: o CLAUDE.md separa stdout
    # cru (vira `content`, sem marca de proveniencia, "so para DADO") de
    # `additionalContext` (rotulado, "para toda INSTRUCAO"), e "ha outra sessao
    # neste repositorio" e um FATO, nao uma ordem. Continua sendo um unico
    # `print` — dois objetos no stdout do mesmo hook quebrariam o parse.
elif pipeline_unmapped:
    # Classificado L1+ porem sem pipeline mapeado: avisa em vez de seguir vazio.
    _falar("warning", (
        f"HARNESS v3 WARNING: classificacao '{classification}' (task {task_id}) "
        f"nao tem pipeline mapeado. Trate como L1-feature ou confirme o tipo "
        f"manualmente — nao ha fases a executar. Nao prossiga em silencio."
    ))
else:
    # L1+: ativa o workflow. O texto manda CONFIRMAR antes de executar porque
    # o classificador aqui e regex: `aggregates.classify` desta maquina mede
    # proxy_regex_vs_observado = 0.297, ou seja ele acerta o nivel observado em
    # ~30% dos casos, com 64 tasks sem confirmacao. Enquanto o canal estava
    # morto isso nao custava nada; com o canal vivo, mandar executar direto
    # seria auto-disparar pipeline sobre um palpite.
    _falar("classified", (
        f"HARNESS v3 CLASSIFIED: {classification}. "
        f"Pipeline: {pipeline_display}. "
        f"Task ID: {task_id}. "
        f"Invoque a skill 'harness-workflow' antes de responder ao usuario. "
        f"A classificacao acima vem de regex e acerta ~30% das vezes: a skill "
        f"deve CONFIRMAR ou CORRIGIR o nivel semanticamente antes de executar "
        f"qualquer fase do pipeline. Se o nivel real for L0, diga e siga direto."
    ))
PYEOF

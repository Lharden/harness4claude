#!/usr/bin/env bash
# sync-machine.sh — Replica as configs host-local do Harness v3 + Obsidian + Graphify
# numa nova maquina, de forma portavel (Windows/Git Bash, macOS, Linux).
#
# O plugin (skills/hooks/scripts) ja viaja via marketplace. O que NAO viaja e a
# fiacao host-local que faz o pipeline disparar: seção do CLAUDE.md global, env +
# marketplace no settings.json, e os MCP servers Obsidian em ~/.claude.json. Este
# script mescla esses pedacos de forma ADITIVA, sempre com backup timestamped.
#
# Uso:
#   bash scripts/sync-machine.sh                 # aplica (auto-merge + backup)
#   bash scripts/sync-machine.sh --dry-run       # mostra o que mudaria, sem escrever
#   VAULT_PATH="/path/Obsidian Vault" bash scripts/sync-machine.sh
#   bash scripts/sync-machine.sh --vault-root "/path/Obsidian Vault" --no-clone
#
# Segredos: a API key do Obsidian REST NUNCA e escrita por este script. O MCP usa
# ${OBSIDIAN_API_KEY} resolvido do ambiente — exporte-a no seu shell/perfil.
#
# PRE-REQUISITOS MANDATORIOS (verificados no fim; o script sai != 0 se faltarem,
# salvo --skip-verify): OBSIDIAN_API_KEY exportada, cert do Local REST API no
# lugar, e o plugin 'superpowers' instalado. Sem eles o pipeline NAO sobe igual.
set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Resolucao portavel de interpretador, OS e caminhos
# ---------------------------------------------------------------------------
PY="$(command -v python3 || command -v python || true)"
[ -z "$PY" ] && { echo "[erro] Python 3 nao encontrado no PATH"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TPL_DIR="$REPO_DIR/sync/templates"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) OS="windows" ;;
  Darwin)               OS="macos" ;;
  *)                    OS="linux" ;;
esac

CLAUDE_HOME="$HOME/.claude"
PLUGIN_LOCAL_DIR="$CLAUDE_HOME/plugins/local"
PLUGIN_DIR="$PLUGIN_LOCAL_DIR/harness4claude"
SETTINGS="$CLAUDE_HOME/settings.json"
GLOBAL_CLAUDE_MD="$CLAUDE_HOME/CLAUDE.md"
CLAUDE_JSON="$HOME/.claude.json"

# Vault root: VAULT_PATH (env) > default por OS (sem hardcode de usuario).
# Pode ser sobrescrito por --vault-root no parse de flags abaixo.
DEFAULT_VAULT="$HOME/Documents/Obsidian Vault"
VAULT_ROOT="${VAULT_PATH:-$DEFAULT_VAULT}"
CERT_PATH="${NODE_EXTRA_CA_CERTS:-$CLAUDE_HOME/obsidian-config/obsidian-local-rest-api.crt}"
REPO_URL="git@github.com:Lharden/harness4claude.git"

DRY_RUN=0
CLONE=1
SKIP_VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY_RUN=1 ;;
    --no-clone)    CLONE=0 ;;
    --skip-verify) SKIP_VERIFY=1 ;;
    --vault-root)  shift; VAULT_ROOT="$1" ;;
    --repo)        shift; REPO_URL="$1" ;;
    *) echo "[erro] flag desconhecida: $1"; exit 2 ;;
  esac
  shift
done

TS="$(date +%Y%m%d%H%M%S)"
say()  { echo "  $*"; }
note() { echo "[$1] $2"; }

echo "== Harness sync-machine =="
say "OS............: $OS"
say "Plugin dir....: $PLUGIN_DIR"
say "Vault root....: $VAULT_ROOT"
say "Cert REST.....: $CERT_PATH"
say "Modo..........: $([ $DRY_RUN = 1 ] && echo 'DRY-RUN (sem escrita)' || echo 'APLICAR (merge + backup)')"
echo ""

backup() { # backup <file>
  [ -f "$1" ] || return 0
  [ $DRY_RUN = 1 ] && { note dry "backup de $1 -> $1.bak-sync-$TS"; return 0; }
  cp "$1" "$1.bak-sync-$TS"; note ok "backup: $(basename "$1").bak-sync-$TS"
}

# ---------------------------------------------------------------------------
# 1. Garantir o repo do plugin presente em ~/.claude/plugins/local/harness4claude
# ---------------------------------------------------------------------------
if [ "$CLONE" = 1 ]; then
  if [ -d "$PLUGIN_DIR/.git" ]; then
    note ok "repo ja presente; git pull --ff-only"
    [ $DRY_RUN = 0 ] && git -C "$PLUGIN_DIR" pull --ff-only || true
  else
    note info "clonando $REPO_URL -> $PLUGIN_DIR"
    [ $DRY_RUN = 0 ] && { mkdir -p "$PLUGIN_LOCAL_DIR"; git clone "$REPO_URL" "$PLUGIN_DIR"; }
  fi
fi

# ---------------------------------------------------------------------------
# 2. Merge aditivo de JSON (settings.json e ~/.claude.json) via Python
# ---------------------------------------------------------------------------
# merge_json <arquivo_alvo> <arquivo_template> <subst k=v ...>
merge_json() {
  local target="$1" template="$2"; shift 2
  backup "$target"
  DRY="$DRY_RUN" TARGET="$target" TEMPLATE="$template" SUBST="$*" "$PY" - <<'PYEOF'
import json, os, sys

target, template = os.environ["TARGET"], os.environ["TEMPLATE"]
dry = os.environ.get("DRY") == "1"
subst = dict(p.split("=", 1) for p in os.environ["SUBST"].split() if "=" in p) if os.environ.get("SUBST") else {}

raw = open(template, encoding="utf-8").read()
for k, v in subst.items():
    raw = raw.replace(k, v.replace("\\", "/"))
tpl = json.loads(raw)
tpl.pop("_comment", None)

cur = {}
if os.path.exists(target):
    try:
        cur = json.load(open(target, encoding="utf-8"))
    except Exception as e:
        print(f"[erro] {target} nao e JSON valido: {e}", file=sys.stderr); sys.exit(1)

added = []
def merge(dst, src, path=""):
    for k, v in src.items():
        p = f"{path}.{k}".lstrip(".")
        if isinstance(v, dict):
            node = dst.setdefault(k, {})
            if isinstance(node, dict):
                merge(node, v, p)
            continue
        if "__" in str(v):  # placeholder nao resolvido -> nao escreve
            continue
        if k not in dst:     # aditivo: nunca clobbera valor existente do usuario
            dst[k] = v; added.append(f"{p} = {v}")

merge(cur, tpl)
if not added:
    print("[ok] " + os.path.basename(target) + ": nada a adicionar (idempotente)")
else:
    for a in added:
        print(("[dry] +" if dry else "[ok] +") + a)
    if not dry:
        json.dump(cur, open(target, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("[ok] " + os.path.basename(target) + " atualizado")
PYEOF
}

echo ""; echo "-- settings.json --"
merge_json "$SETTINGS" "$TPL_DIR/settings.snippet.json" \
  "__VAULT_ROOT__=$VAULT_ROOT" "__CERT_PATH__=$CERT_PATH" "__PLUGIN_LOCAL_DIR__=$PLUGIN_LOCAL_DIR"

echo ""; echo "-- ~/.claude.json (MCP Obsidian) --"
merge_json "$CLAUDE_JSON" "$TPL_DIR/mcp.obsidian.snippet.json" \
  "__VAULT_ROOT__=$VAULT_ROOT"

# ---------------------------------------------------------------------------
# 3. CLAUDE.md global: inserir/atualizar bloco entre marcadores (idempotente)
# ---------------------------------------------------------------------------
echo ""; echo "-- CLAUDE.md global (bloco Harness) --"
backup "$GLOBAL_CLAUDE_MD"
DRY="$DRY_RUN" TARGET="$GLOBAL_CLAUDE_MD" SNIPPET="$TPL_DIR/claude-md.harness.snippet.md" "$PY" - <<'PYEOF'
import os, re
target = os.environ["TARGET"]
snippet = open(os.environ["SNIPPET"], encoding="utf-8").read().strip()
dry = os.environ.get("DRY") == "1"
begin, end = "<!-- HARNESS4CLAUDE:BEGIN", "HARNESS4CLAUDE:END -->"
cur = open(target, encoding="utf-8").read() if os.path.exists(target) else ""
pat = re.compile(re.escape("<!-- HARNESS4CLAUDE:BEGIN") + r".*?" + re.escape("HARNESS4CLAUDE:END -->"), re.S)
if pat.search(cur):
    new = pat.sub(snippet, cur)
    action = "bloco substituido (atualizado)"
else:
    sep = "" if cur.endswith("\n\n") or not cur else ("\n" if cur.endswith("\n") else "\n\n")
    new = cur + sep + snippet + "\n"
    action = "bloco inserido (novo)"
if new == cur:
    print("[ok] CLAUDE.md: bloco ja atualizado (idempotente)")
elif dry:
    print(f"[dry] CLAUDE.md: {action}")
else:
    open(target, "w", encoding="utf-8").write(new)
    print(f"[ok] CLAUDE.md: {action}")
PYEOF

# ---------------------------------------------------------------------------
# 4. Estado do harness + health-check
# ---------------------------------------------------------------------------
echo ""; echo "-- estado + health-check --"
if [ $DRY_RUN = 0 ]; then
  [ -f "$SCRIPT_DIR/init-state.sh" ] && bash "$SCRIPT_DIR/init-state.sh" || true
  [ -f "$SCRIPT_DIR/health-check.sh" ] && bash "$SCRIPT_DIR/health-check.sh" || true
else
  note dry "rodaria init-state.sh + health-check.sh"
fi

# ---------------------------------------------------------------------------
# 5. Passos MANDATORIOS que exigem decisao humana / segredos / GUI
# ---------------------------------------------------------------------------
cat <<EOF

== PASSOS MANDATORIOS (segredo / GUI / install de pacote) ==
  1. [MANDATORIO] Obsidian REST: instale o plugin "Local REST API" no Obsidian,
     copie o cert para "$CERT_PATH" e exporte a chave (adicione ao perfil do shell):
       export OBSIDIAN_API_KEY="<sua-chave>"
  2. [MANDATORIO] Plugin 'superpowers' (o Harness delega brainstorming/TDD/debug):
       /plugin install harness4claude   (e confirme superpowers via /plugin list)
  3. mcpvault: a 1a execucao baixa @bitbonsai/mcpvault via npx (requer Node/npm).
  4. Graphify (install de pacote = decisao humana):
       bash "$SCRIPT_DIR/setup-graphify.sh"
  5. Reinicie o Claude Code para recarregar settings.json e ~/.claude.json.

Backups criados com sufixo .bak-sync-$TS. Detalhes: docs/SYNC.md
EOF

# ---------------------------------------------------------------------------
# 6. Gate de verificacao MANDATORIO (sai != 0 se faltar item critico)
# ---------------------------------------------------------------------------
if [ $DRY_RUN = 1 ] || [ $SKIP_VERIFY = 1 ]; then
  note info "verificacao mandatoria pulada ($([ $DRY_RUN = 1 ] && echo dry-run || echo --skip-verify))"
  exit 0
fi

echo ""; echo "== Verificacao mandatoria =="
mandatory_fail=0
check() { # check <ok?> <label> <dica>
  if [ "$1" = 1 ]; then note ok "$2"; else note FALTA "$2 -> $3"; mandatory_fail=1; fi
}

# OBSIDIAN_API_KEY exportada no ambiente
check "$([ -n "${OBSIDIAN_API_KEY:-}" ] && echo 1 || echo 0)" \
  "OBSIDIAN_API_KEY exportada" \
  "export OBSIDIAN_API_KEY=<chave do Local REST API> no perfil do shell"

# Cert do Local REST API presente
check "$([ -f "$CERT_PATH" ] && echo 1 || echo 0)" \
  "cert REST em $CERT_PATH" \
  "copie obsidian-local-rest-api.crt para esse caminho"

# Plugin superpowers habilitado em settings.json
SP_OK=$("$PY" - "$SETTINGS" <<'PYEOF'
import json, sys, os
p = sys.argv[1]
try:
    d = json.load(open(p, encoding="utf-8"))
    ep = d.get("enabledPlugins", {})
    print("1" if any(k.startswith("superpowers@") and v for k, v in ep.items()) else "0")
except Exception:
    print("0")
PYEOF
)
check "$SP_OK" "plugin 'superpowers' habilitado" "/plugin install superpowers e reabra o Claude Code"

echo ""
if [ $mandatory_fail = 1 ]; then
  note erro "pre-requisitos MANDATORIOS faltando (acima). Resolva e rode de novo,"
  note erro "ou use --skip-verify para ignorar conscientemente. Exit 1."
  exit 1
fi
note ok "todos os pre-requisitos mandatorios presentes."

#!/bin/bash
# Setup Graphify — bootstrap executado PELO USUÁRIO (instalação de pacote = decisão humana;
# o classificador de segurança do Claude Code nega pip install autônomo, por design).
#
# Cadeia de confiança: graphify.net (fonte dada pelo usuário) → repo oficial safishamsi/graphify
# → PyPI "graphifyy" (duplo y — o README v3 alerta que outros graphify* no PyPI são typosquats).
#
# Uso: bash ~/.claude/plugins/local/harness4claude/scripts/setup-graphify.sh
#      GRAPHIFY_VERSION=x.y.z bash setup-graphify.sh   # opcional: pin de versão
set -euo pipefail

echo "== Graphify setup =="
TS=$(date +%Y%m%d%H%M%S)
cp ~/.claude/settings.json ~/.claude/settings.json.bak-graphify-"$TS"
[ -f ~/.claude/CLAUDE.md ] && cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.bak-graphify-"$TS"
echo "[ok] backups (settings.json, CLAUDE.md) com sufixo .bak-graphify-$TS"

pip install "graphifyy${GRAPHIFY_VERSION:+==$GRAPHIFY_VERSION}"
echo "[ok] instalado: $(pip show graphifyy | head -2 | tr '\n' ' ')"
graphify --version

graphify install          # skill /graphify (Windows auto-detectado)
graphify claude install   # diretiva CLAUDE.md + hook PreToolUse (Glob/Grep)

echo ""
echo "== Revise os diffs (graphify claude install mexe em): =="
echo "   ~/.claude/settings.json   (hook PreToolUse)"
echo "   CLAUDE.md                 (seção 'leia GRAPH_REPORT.md antes de arquitetura')"
echo ""
echo "== Smoke test =="
echo "   cd ~/.claude/plugins/local/harness4claude"
echo "   graphify .   # passe 1 (AST) é local e sem LLM"
echo "   graphify query \"estrutura geral\" --graph graphify-out/graph.json"
echo ""
echo "== Espelho no vault AI-Brain =="
echo "   /graphify . --obsidian --obsidian-dir \"C:/Users/Leonardo/Documents/Obsidian Vault/AI-Brain/wiki/graphs/harness4claude\""

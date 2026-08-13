#!/usr/bin/env bash
# harness-arsenal-gate.sh — PreToolUse:Bash|PowerShell hook
#
# A unica barreira dura do sistema. Instalar ou habilitar um plugin exige
# decisao previa no arsenal e orcamento que caiba.
#
# Por que so aqui: relatorio que ninguem le e igual a nao ter relatorio, e gate
# que dispara em tudo vira ruido ignorado. Todo o resto do arsenal (staleness,
# prova vencida, colisao, candidatos) apenas REPORTA. So instalacao bloqueia,
# porque so ela cobra tokens de toda sessao futura sem pedir licenca de novo.
#
# Medido em 2026-08-12: o roster tinha 63% de peso morto, acumulado uma
# instalacao de cada vez, sem que nenhuma delas parecesse cara sozinha. E o
# catalogo tem plugin de 17.793 tokens — tres vezes o roster inteiro de hoje.
#
# Exit 2 = BLOQUEIA, Exit 0 = PASSA (com aviso opcional)

set -euo pipefail

: "${HARNESS_DIR:=$HOME/.claude/harness}"

{ mkdir -p "$HARNESS_DIR/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
    > "$HARNESS_DIR/heartbeats/PreToolUse"; } 2>/dev/null || true

INPUT=$(cat)

# STATUS na primeira linha, comando no resto. Mesma licao do harness-git-guard:
# colapsar "nao havia comando" e "payload em formato desconhecido" na mesma
# string vazia faz o guard devolver exit 0 para tudo numa mudanca de schema do
# host, e parar de bloquear sem emitir um unico sinal.
#
# Aqui o extrator olha `command` (Bash) E as chaves que o PowerShell usa, porque
# `claude plugin install` roda pelos dois no Windows.
EXTRACT=$(printf '%s' "$INPUT" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
if not isinstance(d, dict):
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
ti = d.get('tool_input')
if not isinstance(ti, dict):
    print('SHAPE_UNKNOWN'); print(''); raise SystemExit(0)
for key in ('command', 'script', 'cmd'):
    if key in ti:
        print('OK'); print(str(ti.get(key) or '').replace(chr(10), ' ')); raise SystemExit(0)
print('NO_COMMAND_KEY'); print('')
" 2>/dev/null || printf 'SHAPE_UNKNOWN\n\n')

STATUS="${EXTRACT%%$'\n'*}"
STATUS="${STATUS%$'\r'}"
COMMAND="${EXTRACT#*$'\n'}"
COMMAND="${COMMAND%$'\r'}"

# Formato desconhecido: NUNCA bloquear (travar todo Bash da maquina numa mudanca
# de schema seria pior que o problema), mas nunca em silencio. Rate-limit de 1h
# porque aviso a cada chamada de Bash vira ruido, e ruido vira alarme ignorado.
if [[ "$STATUS" != "OK" ]]; then
  MARKER="$HARNESS_DIR/.arsenal-gate-blind"
  NOW=$(date +%s 2>/dev/null || echo 0)
  LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
  case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
  if [ "$NOW" -eq 0 ] || [ $((NOW - LAST)) -ge 3600 ]; then
    mkdir -p "$HARNESS_DIR" 2>/dev/null || true
    printf '%s\n' "$NOW" > "$MARKER" 2>/dev/null || true
    echo "## Harness Warning: arsenal-gate nao reconheceu o payload do PreToolUse ($STATUS)."
    echo "   O gate de instalacao de plugin esta INATIVO ate isto ser corrigido."
    echo "   Rode: bash scripts/health-check.sh"
  fi
  exit 0
fi

[[ -z "$COMMAND" ]] && exit 0

# So interessa `claude plugin install|enable`. `disable`, `uninstall`, `list` e
# `marketplace` passam direto: tirar coisa nunca precisa de permissao.
echo "$COMMAND" | grep -qE 'claude\s+plugin\s+(install|enable)\b' || exit 0

# Alvo: primeira palavra depois de install/enable que nao comeca com '-'.
# `nome@marketplace` vira `nome` — o registry usa nome curto porque e o que
# `claude plugin disable` aceita no rollback.
TOOL=$(echo "$COMMAND" | python -c "
import re, sys
m = re.search(r'claude\s+plugin\s+(?:install|enable)\s+((?:-\S+\s+)*)(\S+)', sys.stdin.read())
print((m.group(2).split('@')[0] if m else '').strip('\"' + chr(39)))
" 2>/dev/null || echo "")
TOOL="${TOOL%$'\r'}"

[[ -z "$TOOL" ]] && exit 0

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULT=$(cd "$PLUGIN_DIR" && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
  python tools/arsenal.py gate --tool "$TOOL" 2>/dev/null) && GATE_OK=1 || GATE_OK=0

# Gate que nao consegue decidir NAO bloqueia — mas diz que nao decidiu. Vault
# ausente ou registry ilegivel nao podem virar trava na instalacao de plugin.
if [[ -z "$RESULT" ]]; then
  echo "## Harness Warning: arsenal-gate nao conseguiu avaliar '$TOOL' (vault ou registry indisponivel)."
  echo "   Instalacao liberada sem conferencia de orcamento. Rode: python tools/arsenal.py budget"
  exit 0
fi

if [[ "$GATE_OK" == "0" ]]; then
  printf '%s' "$RESULT" | python -c "
import json, sys
d = json.load(sys.stdin)
motivo = (d.get('errors') or ['sem decisao no arsenal'])[0]
como = (d.get('resumo') or {}).get('como_resolver', '')
print(json.dumps({'decision': 'block',
                  'reason': 'BLOQUEADO pelo arsenal: ' + motivo + ' | ' + como}, ensure_ascii=False))
" >&2 2>/dev/null || echo '{"decision":"block","reason":"BLOQUEADO pelo arsenal: sem decisao registrada."}' >&2
  exit 2
fi

# Passou. Se veio aviso (custo fora do catalogo), mostra sem travar.
printf '%s' "$RESULT" | python -c "
import json, sys
d = json.load(sys.stdin)
for w in (d.get('warnings') or []):
    print('## Harness: ' + w)
r = d.get('resumo') or {}
if 'sobra_depois' in r:
    print(f\"## Arsenal: {r['alvo']} custa {r['custo']} tok; sobram {r['sobra_depois']} do teto {r['teto']}.\")
" 2>/dev/null || true

exit 0

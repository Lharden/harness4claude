#!/usr/bin/env bash
# harness-git-guard.sh — PreToolUse:Bash|PowerShell hook
# Protege contra operacoes git destrutivas. A decisao mora em
# harness_git_guard.py; este arquivo so escolhe o interpretador e o chama.
# Spec: docs/specs/git-guard-prazo-sob-carga-spec-light.md
#
# Sem fork de proposito (medido em 2026-10-07): a versao anterior criava ~15
# processos por chamada (`cat`, `tr`, `date`, `mkdir`, subshells e 4 Pythons),
# custava 2,1 s ocioso e 3,8 s com a maquina carregada, e 531 chamadas
# passaram do prazo de 10 s do host entre 20/09 e 07/10 — o host mata o hook e
# a ferramenta segue, sem checagem. Aqui so ha builtins e UM processo Python,
# que le o payload direto da stdin (passar o comando por argv do bash para o
# Python nativo quebrava aspas e quebras de linha: 973 falsos "nao conseguiu
# analisar").
#
# Exit 2 = BLOQUEIA, Exit 0 = PASSA (com aviso opcional)

set -uo pipefail

# Interpretador nomeado (master-harness). Sem marcador valido, `python`.
# `read` em vez de `cat | tr`: aceita marcador sem newline final, e o CR e o
# BOM saem por expansao de parametro.
PY="python"
_MH_MARCA="${MASTER_HARNESS_HOME:-$HOME/.master-harness}/interpretador"
if [ -r "$_MH_MARCA" ]; then
    _MH_CAND=""
    IFS= read -r _MH_CAND < "$_MH_MARCA" || true
    _MH_CAND="${_MH_CAND#$'\xef\xbb\xbf'}"
    _MH_CAND="${_MH_CAND%$'\r'}"
    [ -n "$_MH_CAND" ] && [ -x "$_MH_CAND" ] && PY="$_MH_CAND"
fi

_HD="${HARNESS_DIR:-$HOME/.claude/harness}"

# Heartbeat de disparo — ver harness-classify.sh para o porque. Fica aqui, e
# nao no Python, para medir a CHAMADA mesmo quando o Python nao parte. O
# `mkdir` (unico fork) so roda na primeira chamada da maquina.
{ printf '%s\n' "${EPOCHSECONDS:-0}" > "$_HD/heartbeats/PreToolUse"; } 2>/dev/null \
  || { mkdir -p "$_HD/heartbeats" && printf '%s\n' "${EPOCHSECONDS:-0}" \
       > "$_HD/heartbeats/PreToolUse"; } 2>/dev/null || true

# Diretorio deste arquivo por expansao de parametro (barra ou contrabarra:
# os testes chamam com caminho do Windows). Sem separador, o diretorio atual.
_EU="${BASH_SOURCE[0]}"
_HOOK_DIR="${_EU%[/\\]*}"
[ "$_HOOK_DIR" = "$_EU" ] && _HOOK_DIR="."

"$PY" "$_HOOK_DIR/harness_git_guard.py"
RC=$?

# Contrato com harness_git_guard.py: 0 passa, 3 bloqueia. Qualquer outro
# codigo e falha de infraestrutura — inclusive o 2 que o Python devolve quando
# nao acha o script, que NUNCA pode virar bloqueio de toda a ferramenta.
case "$RC" in
  0) exit 0 ;;
  3) exit 2 ;;
esac

# Falha de infraestrutura: nunca bloquear, nunca em silencio. Rate-limit de
# 1 h no mesmo marcador do aviso de payload desconhecido.
MARKER="$_HD/.git-guard-blind"
NOW="${EPOCHSECONDS:-0}"
LAST=0
[ -r "$MARKER" ] && { IFS= read -r LAST < "$MARKER" || true; }
LAST="${LAST%$'\r'}"
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
if [ "$NOW" -eq 0 ] || [ $((NOW - LAST)) -ge 3600 ]; then
  { printf '%s\n' "$NOW" > "$MARKER"; } 2>/dev/null \
    || { mkdir -p "$_HD" && printf '%s\n' "$NOW" > "$MARKER"; } 2>/dev/null || true
  echo "## Harness Warning: git-guard falhou (rc=$RC, interpretador $PY)."
  echo "   O bloqueio de operacoes git destrutivas esta INATIVO nesta chamada. Rode: bash scripts/health-check.sh"
fi
exit 0

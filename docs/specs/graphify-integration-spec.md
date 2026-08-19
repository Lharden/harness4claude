---
applies_to:
  - skills/graph-context/**
  - skills/harness-workflow/SKILL.md
  - scripts/setup-graphify.sh
  - hooks/harness-graphify-autosetup.sh
  - scripts/health-check.sh
---

# Spec — Integração Graphify ao Harness v3 (graphify-integration)

**Status:** aprovada em modo autônomo (Discretion — ver docs/CONTEXT.md) · **Task:** t-20260611-204025 · **Data:** 2026-06-12

## Objetivo

Dar ao harness uma camada de **contexto estrutural persistente**: grafos de conhecimento por repositório (graphify) consultáveis antes de qualquer exploração de codebase, com espelhamento no vault Obsidian AI-Brain para virar conhecimento permanente e navegável.

## User Stories

- **US1 (P1)** Como Leonardo, quero que o Claude consulte o knowledge graph do repo antes de varrer arquivos, para gastar menos tokens e acertar mais em perguntas de arquitetura.
- **US2 (P1)** Como harness, quero uma fase `graph-context` nos pipelines L2 que leia `graphify-out/GRAPH_REPORT.md` (ou rode fallback `wf-context-scan`), para alimentar write-spec/design-doc com estrutura real.
- **US3 (P2)** Como Leonardo, quero os grafos exportados para `AI-Brain/wiki/graphs/{repo}/` no vault, para navegar o conhecimento do código no Obsidian (graph view, wikilinks, Smart Connections).
- **US4 (P3)** Como Leonardo, quero o health-check acusando a presença/ausência do graphify, para diagnosticar o setup em 1 comando.

## Acceptance Criteria

- **AC1** (US1) Given `graphifyy` instalado e `graphify claude install` executado, When Claude faz Glob/Grep num repo com `graphify-out/graph.json`, Then o hook PreToolUse injeta o lembrete de ler GRAPH_REPORT.md.
- **AC2** (US2) Given pipeline L2 iniciado num repo com grafo, When a fase de contexto roda, Then a skill `graph-context` lê GRAPH_REPORT.md e (se a pergunta exigir) roda `graphify query` focada, sem varredura bruta inicial.
- **AC3** (US2) Given repo sem `graphify-out/`, When `graph-context` roda, Then ela sugere `/graphify` e usa `wf-context-scan` como fallback — pipeline nunca trava.
- **AC4** (US3) Given grafo gerado no repo harness4claude, When exportado com `--obsidian-dir`, Then notas md com wikilinks existem em `AI-Brain/wiki/graphs/harness4claude/` e o vault-bridge documenta a convenção.
- **AC5** (US4) Given health-check executado, Then linha `graphify` reporta OK (instalado) ou WARN (ausente, opcional) — nunca FAIL.
- **AC6** (geral) Given suite pytest do plugin, When executada após as mudanças, Then 100% pass (sem regressão nos 123 testes).

## Boundaries

- **ALWAYS**: backup antes de tocar `~/.claude/settings.json`, `~/.claude.json`, `CLAUDE.md`; pacote PyPI exclusivamente `graphifyy`; graphify-out/ no .gitignore do repo analisado.
- **NEVER**: instalar plugins do Obsidian programaticamente (negado pelo classificador — rota é a UI oficial); remover mcpvault antes da migração REST validada; subir `graph.json` de repos privados para fora da máquina.
- **ASK**: ver CONTEXT.md A1/A2.

## Success criteria

- `graphify query` responde no repo harness4claude usando só o grafo.
- Pipeline L2 documentado com a fase graph-context no harness-workflow/SKILL.md.
- Vault contém o grafo exportado + nota índice linkada.

# CONTEXT — Incorporação Graphify + Obsidian ao Harness (t-20260611-204025)

> Gerado em modo autônomo (usuário ausente). Decisões Discretion são revisáveis — nenhuma é irreversível.

## Locked (decididas pelo usuário no prompt)

- L1. Harness local deve estar sincronizado com `github.com/Lharden/harness4claude` (**feito**: ff 7a8a47a → 2074d5b).
- L2. Aplicar nesta máquina o setup do doc `Downloads/obsidian-replicacao.md` (Local REST API + MCP nativo HTTP), direcionando o que não puder ser automatizado.
- L3. Incorporar o Graphify (graphify.net / safishamsi/graphify) ao harness de workflow.
- L4. Usar o Obsidian como camada de estruturação/aceleração do gerenciamento de contexto e conhecimento do Claude.

## Discretion (decididas pelo agente, justificadas)

- D1. **Coexistência MCP, não substituição**: `obsidian` → MCP nativo REST (como manda o doc) e mcpvault renomeado para `obsidian-fs` como fallback headless. Motivo: vault-bridge e sessões cron dependem de acesso com o app fechado (mcpvault é filesystem); o REST nativo exige Obsidian aberto.
- D2. **Pacote oficial `graphifyy`** (PyPI, duplo y) — alerta de typosquatting do próprio README. Instalação via pip global (Python 3.11.9 ✓).
- D3. **Hook PreToolUse do graphify ativado** (`graphify claude install`): alinhado ao objetivo "acelerar contexto" (grafo consultado antes de Glob/Grep). Reversível com `graphify claude uninstall`.
- D4. **Convenção de export Obsidian**: `AI-Brain/wiki/graphs/{repo-slug}/` via `--obsidian-dir`. Grafos de código viram parte do segundo cérebro (wikilinks + graph view).
- D5. **Fase de contexto do pipeline L2**: skill `graph-context` consulta `graphify-out/` primeiro; `wf-context-scan` vira fallback quando não há grafo.

## Deferred (registradas, NÃO fazer agora)

- DF1. `python -m graphify.serve` como MCP server de grafo — adiar até haver demanda de queries repetidas (já há muitos MCP servers ativos).
- DF2. Migrar vault-bridge para nomes de tools do REST nativo — só após a migração Camada B ser concluída e validada pelo usuário.
- DF3. Advisor Strategy / execução não-interativa (scope F6) — fora deste escopo.

## ASK (exigem o usuário — direcionamentos)

- A1. **Instalar plugin `obsidian-local-rest-api` no Obsidian** (Settings → Community plugins → Browse). Download automatizado foi negado pelo classificador de segurança (código executável de terceiro) — instalação via UI oficial é a rota correta. Depois: rodar `bash ~/.claude/obsidian-config/finish-obsidian-migration.sh`.
- A2. Revisar decisões D1–D5 e os diffs em `~/.claude/settings.json` / `CLAUDE.md` feitos por `graphify claude install`.

## Achados da auditoria (contexto)

- Health-check harness: ALL PASSED. Working tree limpo. Classificação regex deste prompt corrigida semanticamente (L2-bug → L2-feature, `agreed: false`).
- Vault desta máquina: `C:/Users/Leonardo/Documents/Obsidian Vault` (23 plugins, sync remotely-save/github-sync) — **sem** `obsidian-local-rest-api`; porta 27124 morta; Obsidian rodando.
- Plugin context-mode: hooks ativos (bloqueiam WebFetch/orientam sandbox) mas servidor MCP **não conectado** nesta sessão — tools `ctx_*` inexistentes. Inconsistência a reportar.
- firecrawl CLI v1.12.2 presente porém **não autenticado** (sem FIRECRAWL_API_KEY).

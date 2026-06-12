# Design — Integração Graphify (graphify-integration)

## Arquitetura (3 camadas)

```
┌─ Nível repo ───────────────────────────────────────────┐
│ /graphify (skill oficial) → graphify-out/              │
│   graph.json + GRAPH_REPORT.md + cache/ (SHA256)       │
│ Hook PreToolUse (Glob/Grep) → "leia o grafo primeiro"  │
├─ Nível harness (este plugin) ──────────────────────────┤
│ skill graph-context (nova)                             │
│   1. graphify-out/ existe? → ler GRAPH_REPORT.md       │
│   2. pergunta específica? → graphify query "..."       │
│   3. sem grafo? → sugerir /graphify + wf-context-scan  │
│ harness-workflow: fase contexto L2 = graph-context     │
│ health-check: check WARN-only de graphify              │
├─ Nível conhecimento (Obsidian AI-Brain) ───────────────┤
│ /graphify <src> --obsidian --obsidian-dir              │
│   "AI-Brain/wiki/graphs/{repo-slug}/"                       │
│ vault-bridge: seção Knowledge Graphs + convenção       │
└────────────────────────────────────────────────────────┘
```

## Componentes alterados/criados

| Item | Caminho | Mudança |
|---|---|---|
| skill graph-context | `skills/graph-context/SKILL.md` | **novo** — protocolo de consulta ao grafo |
| harness-workflow | `skills/harness-workflow/SKILL.md` | linha na tabela fase→mecanismo + nota |
| health-check | `scripts/health-check.sh` | bloco "Graphify (opcional)" WARN-only |
| vault-bridge (fora do repo) | `~/.claude/skills/vault-bridge/SKILL.md` | seção graphs/ + nota transição MCP |
| migração Obsidian (fora do repo) | `~/.claude/obsidian-config/finish-obsidian-migration.sh` | **novo** — completa Camada B pós-instalação do plugin |

## Decisões técnicas

1. **Hook do graphify, não hook custom**: `graphify claude install` mantém o hook atualizado pelo upstream; harness não duplica.
2. **graph-context é skill, não Workflow**: leitura de GRAPH_REPORT.md é barata (1 arquivo); fan-out só no fallback (wf-context-scan já existe).
3. **WARN-only no health-check**: graphify é acelerador opcional — ausência não pode reprovar o setup (mesmo padrão do autoresearch).
4. **Export Obsidian por repo, slug = nome do diretório git**: previsível para o vault-bridge indexar.

## Test strategy

- pytest existente do plugin: sem regressão (AC6).
- Validação funcional: grafo gerado no próprio repo harness4claude (pass AST, sem custo LLM) + 1 `graphify query` real (AC1/AC2 parcial).
- health-check rodado antes/depois (AC5).
- AC2/AC3 completos exigem um pipeline L2 real — verificação documentada como pendência de uso (primeira tarefa L2 após merge).

## Risks

- R1. `graphify claude install` sobrescrever seção de CLAUDE.md de forma intrusiva → mitigação: backup + diff + ajuste manual.
- R2. Hook PreToolUse adicionar latência a todo Glob/Grep → mitigação: hook é no-op sem graph.json; remoção = `graphify claude uninstall`.
- R3. Pacote PyPI comprometido → mitigação: nome oficial `graphifyy` confirmado no README do repo canônico; pin de versão no install.

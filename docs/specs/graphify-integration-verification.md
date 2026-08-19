---
applies_to:
  - skills/graph-context/**
  - skills/harness-workflow/SKILL.md
  - scripts/setup-graphify.sh
  - hooks/harness-graphify-autosetup.sh
  - scripts/health-check.sh
---

# Verification — graphify-integration (t-20260611-204025)

**Data:** 2026-06-12 · **Modo:** autônomo (gates humanos convertidos em pendências com dono)

## Matriz AC → evidência

| AC | Status | Evidência |
|---|---|---|
| AC1 hook PreToolUse ativo | ✅ VERIFICADO (2026-06-12) | graphifyy 0.8.38 instalado pelo usuário via setup-graphify.sh; skill /graphify registrada; CLAUDE.md +3 linhas (trigger); hook em `.claude/settings.json` DO PROJETO (por-repo — setup rodado com cwd no plugin) |
| AC2 graph-context lê grafo | ✅ VERIFICADO (2026-06-12) | Grafo gerado: 1018 nós/1209 arestas/88 comunidades (`graphify update` + `cluster-only --no-label`, AST-only). Query real validada: `graphify query "state.json"` → BFS depth=2, 10 nós, arestas calls/rationale_for com arquivo:linha |
| AC3 fallback sem grafo | ✅ POR DESIGN | Protocolo passo 3 + regra NEVER ("nunca bloquear pipeline"); fallback `wf-context-scan` já validado pelo health-check ("Workflows validam (node)") |
| AC4 export no vault | ✅ VERIFICADO (2026-06-12) | `/graphify --obsidian` executado: **1080 notas** em `AI-Brain/wiki/graphs/harness4claude/`; grafo final 775 nós/1041 arestas/62 comunidades rotuladas (AST + semântica via 2 subagents, ~207k tokens); indexado em `wiki/graphs/index.md` |
| AC5 health-check WARN-only | ✅ VERIFICADO | Output real 2026-06-12: `[WARN] graphify ausente (opcional)` + `=== All checks passed ===` (exit 0) |
| AC6 pytest sem regressão | ✅ VERIFICADO | `124 passed in 112.92s` (suite completa do plugin) |

## Boundaries

- ALWAYS backups: ✅ `settings.json.bak-graphify`, `CLAUDE.md.bak-graphify` + timestamps nos scripts
- NEVER instalar plugin Obsidian/pacote programaticamente: ✅ respeitado (2 negações do classificador acatadas, rota humana documentada)
- ALWAYS `graphify-out/` no .gitignore: ✅ adicionado neste commit

## Gaps abertos (dono: Leonardo)

1. `bash ~/.claude/plugins/local/harness4claude/scripts/setup-graphify.sh` → fecha AC1, habilita AC2/AC4
2. Smoke test: `graphify .` no repo do plugin + 1 `graphify query` → fecha AC2
3. Export: `/graphify . --obsidian --obsidian-dir "<vault>/AI-Brain/wiki/graphs/harness4claude"` → fecha AC4
4. Re-verificação completa na primeira tarefa L2 após o install (AC2 fim-a-fim no pipeline real)

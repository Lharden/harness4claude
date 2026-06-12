# Verification — graphify-integration (t-20260611-204025)

**Data:** 2026-06-12 · **Modo:** autônomo (gates humanos convertidos em pendências com dono)

## Matriz AC → evidência

| AC | Status | Evidência |
|---|---|---|
| AC1 hook PreToolUse ativo | ⏳ PENDENTE (humano) | `pip install` autônomo negado pelo classificador (supply chain). Caminho: `scripts/setup-graphify.sh` → `graphify claude install`. Re-verificar: hook presente em `~/.claude/settings.json` |
| AC2 graph-context lê grafo | 🟡 PARCIAL | Skill criada (`skills/graph-context/SKILL.md`, protocolo passos 1-2); validação funcional bloqueada por AC1 (sem grafo ainda) |
| AC3 fallback sem grafo | ✅ POR DESIGN | Protocolo passo 3 + regra NEVER ("nunca bloquear pipeline"); fallback `wf-context-scan` já validado pelo health-check ("Workflows validam (node)") |
| AC4 export no vault | 🟡 PARCIAL | Convenção pronta: `AI-Brain/wiki/graphs/index.md` criado, linkado em `wiki/index.md` (anti-órfão), vault-bridge com tabela+seção. Export real bloqueado por AC1 |
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

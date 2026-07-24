# Design — Skill Router Híbrido + Gestão de Plugins/Skills (skill-router) — harness4claude v3.3

> Fase de pesquisa concluída em 2026-07-23. Decisões de direção tomadas pelo usuário:
> poda moderada · roteador híbrido (regex + embeddings locais) · ancorado no harness4claude.
> Este doc é o deliverable da fase de descoberta; P1/P2 só começam após review deste doc.

## 1. Diagnóstico (medido em 2026-07-23)

| Métrica | Valor |
|---|---|
| Plugins habilitados | 43 (antes da poda P0) |
| Skills injetadas por sessão | 299 (297 de plugins + 2 pessoais) |
| Custo das descriptions no catálogo residente | ~123K chars ≈ **~31–38K tokens/sessão** |
| Skills alguma vez usadas (`skillUsage` em `.claude.json`) | **~10 de 299** (brainstorming 13×, graphify 4×, harness 1-2×) |
| Servidores MCP | 29 locais — já *deferred* via ToolSearch (schemas fora do prefixo; otimizado) |
| Maiores contribuintes | data-agent-kit-starter-pack 56 skills + 11 MCP stdio · data-engineering 35 · huggingface-skills 26 · growthbook 25 · datarobot 14 · hunter 13 |
| Cruft | 12 dirs de versão stale no cache · 2 dirs `marketplaces/temp_*` · langfuse duplicado (`langfuse` + `langfuse-observability`) · harness4claude em 2 cópias (cache 3.2.0 = ativa; clone local = dev) |

Conclusão: o catálogo de skills é a fonte dominante de peso no início de sessão; ~290 skills pagam
imposto de contexto sem nunca terem sido invocadas. MCP não é o problema (já deferred).

## 2. Pesquisa — técnicas validadas (com fontes)

| Técnica | Evidência | Status |
|---|---|---|
| Progressive disclosure (só frontmatter carrega; ~50 tok/skill) | anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | Oficial |
| `skillListingBudgetFraction` (default 1% ≈ 2K tok) + `skillListingMaxDescChars` — trunca descriptions das skills menos usadas | claudefa.st/blog/guide/mechanics/skill-listing-budget | Oficial (knob) |
| Tool Search / `defer_loading` p/ MCP (>85% de redução em definição de tools; precisão MELHORA acima de 30-50 tools) | platform.claude.com/docs/.../tool-search-tool | Oficial — **já ativo aqui** |
| Roteador semântico de skills: índice de embeddings de nome+description → injeta top-K via hook. **~456× economia, top-5 87,5%, top-1 62,5%**, sub-segundo | hackernoon.com/how-semantic-routers-cut-claude-code-skill-tokens-by-456x | Comunidade (comprovado) |
| Higiene de description diretiva ("ALWAYS invoke when...") — ativação **~20% → ~84%** | dev.to/oluwawunmiadesewa (e outros) | Comunidade (alto ROI, quase grátis) |
| Duas camadas: regex primeiro (0ms), semântica só como refinamento — padrão KG-first/LLM-fallback | arXiv 2505.03275 (RAG-MCP), 2506.01056 (MCP-Zero), 2603.22455 (SkillRouter), 2605.01582 | Pesquisa |
| crune — minera JSONL de sessões (grafo semântico, Louvain, betweenness) para achar skills reusáveis e podar mortas | dev.to/chigichan24 · `npx @chigichan24/crune` | Comunidade |
| Hook de ativação de skills via `skill-rules.json` (keywords + intentPatterns) | claudefa.st/blog/tools/hooks/skill-activation-hook | Comunidade — análogo do nosso classify |
| Context editing + memory tool (−84% em tarefas longas) | platform.claude.com/docs/en/build-with-claude/context-editing | Oficial beta (API; fora de escopo v3.3) |

Antipadrão identificado na pesquisa: **nenhum roteamento compensa catálogo morto** — poda vem antes
de roteador (por isso P0 executa primeiro).

## 3. Arquitetura v3.3

```
UserPromptSubmit (hooks do mesmo evento rodam em PARALELO → latência = max, não soma)
 ├─► harness-classify.sh      [INTOCADO — hardened por incidentes; dono do state lock]
 │      └─ state.json + systemMessage "HARNESS v3 CLASSIFIED…"
 └─► harness-skill-router.sh → skill_router.py                    [NOVO, sem lock]
        1. Guards: assinatura de automação · 20 ≤ len ≤ 30000 ·
           pipeline ativo (leitura SEM lock de state.json) · dedupe por sessão
        2. Camada A (<5ms): match exato de nome + aliases curados vs skills-index.json
        3. Camada B (30-250ms warm): POST /api/embed Ollama
           (nomic-embed-text-v2-moe, JÁ INSTALADO; prefixo "search_query: ")
           → dot product vs embeddings.f16.bin → boost por uso
           [timeout 1200ms → degrada p/ Camada A; falha total → exit 0 silencioso]
        4. Threshold + merge → hookSpecificOutput.additionalContext "[skill-hint]…"
           (≤ ~90 tokens) + registro em ~/.claude/harness/router/session-{id}.json

SessionStart ─► harness-router-warmup.sh [NOVO]
        fingerprint stale? → rebuild em background (fallback: lazy via marker .stale)
        ping warm no Ollama (keep_alive=30m) — nunca bloqueia, cap 3s

PostToolUse matcher "Skill" ─► harness-skill-feedback.sh [NOVO, P2]
        tool_input.skill vs dicas da sessão → router-log.jsonl (hit/miss/spontaneous)
        → aggregates.router em signals.json (via recompute_aggregates)

Build offline: build_skills_index.py [NOVO]
        varre SKILL.md de plugins habilitados E desabilitados + skills pessoais
        junta usage_count/lastUsedAt do skillUsage (leitura de .claude.json, READ-ONLY)
        → ~/.claude/harness/skills-index/{skills-index.json, embeddings.f16.bin, meta.json}
```

### Decisões técnicas

1. **Hook separado, não extensão do classify**: paralelo (latência = max), classify tem histórico de
   incidentes e é dono do lock — o router é read-only sobre state.json e jamais pode bloquear um
   prompt (todo caminho de erro = `exit 0` + linha em `router/debug-router.log`).
2. **Router não consome L0/L1/L2**: classificação decide *profundidade de pipeline*; roteamento decide
   *domínio de skill*. Ambiguidade do regex não é gate para a Camada B.
3. **Camada B dispara em todo prompt elegível** (não só em ambiguidade): os guards já removem o ruído
   de alto volume; controle de ruído fica no *threshold de injeção* (cosine ≥ 0.45 + margem ≥ 0.05),
   não no gate de computação. Escape hatch de 1 linha: "só quando Camada A vazia".
4. **Modelo de embedding: `nomic-embed-text-v2-moe`** (já em `ollama list`, 957MB, ~100 idiomas incl.
   PT, 768-dim, ~1GB VRAM). bge-m3 pontua marginalmente melhor mas é 2,2GB — com 299 docs curtos o
   gargalo é a qualidade das descriptions, não o modelo. Golden set dá A/B grátis se quisermos depois.
5. **Índice inclui skills de plugins desabilitados** — o router pode sugerir
   `/plugin enable X` (bar mais alto: cosine ≥ 0.60 e ≥ 0.08 acima do melhor hit habilitado; máx. 1
   por dica; nunca auto-habilita). É a rede de segurança que torna a poda de baixo arrependimento.
6. **Sem numpy no hot path**: embeddings f16 L2-normalizados no build → dot product puro-Python sobre
   299 linhas ≈ 20-40ms (~460KB de arquivo). stdlib-only, padrão do `diagnose_ollama.py`.
7. **Rebuild nunca síncrono em hook**: fingerprint (mtime/hash) no SessionStart → background; como
   background em git-bash/Windows é frágil, fallback lazy: marker `.stale`, router serve índice velho
   (quase sempre correto) e re-tenta o spawn.
8. **Obsidian/graphify: deliberadamente mínimo no v3.3.** O catálogo é metadado plano — grafo não
   agrega sobre lista ranqueada. Só uma nota-resumo opcional `AI-Brain/skills-catalog.md` via flag
   `--vault-note` (default off), aproveitando o vault_sync existente. Grafo completo: adiado.

### Schema do índice (`skills-index.json`, resumo)

```json
{ "schema_version": 1, "model": "nomic-embed-text-v2-moe", "dim": 768,
  "fingerprint": {"enabled_plugins_hash": "…", "skill_files_hash": "…"},
  "skills": [{ "id": "harness4claude:write-spec", "plugin": "…", "source": "local-plugin",
    "enabled": true, "path": "…", "description": "…", "aliases": ["spec"],
    "usage_count": 0, "last_used_at": null, "vec_row": 212 }] }
```

Texto embedado: `search_document: {name}. {description}` · query: `search_query: {prompt[:1500]}`
(prefixos exigidos pelo nomic-v2). `embeddings.f16.bin`: row-major f16, L2-normalizado no build.

### Formato de injeção

```
[skill-hint] Skills possivelmente relevantes (ranqueadas):
1. slb-presentations — decks .pptx no padrão SLB
2. anthropic-skills:pptx — criar/editar PowerPoint
3. growthbook:flag-create (plugin desabilitado — sugira `/plugin enable growthbook` se for isto)
Se alguma se aplica, invoque com o Skill tool ANTES de responder. Se nenhuma, ignore este bloco.
```

Dedupe: mesma skill sugerida no máx. 2× por sessão; nunca o mesmo conjunto em prompts consecutivos;
máx. 1 bloco por prompt; arquivos de sessão podados no SessionStart (>7 dias).

## 4. Componentes novos/alterados

| Item | Caminho | Mudança |
|---|---|---|
| Router hook | `hooks/harness-skill-router.sh` + `hooks/skill_router.py` | **novo** (P1) — shim bash + lógica Python, padrão do classify (PYTHONUTF8, cygpath, `|| exit 0`), timeout hooks.json 5000ms |
| Warmup | `hooks/harness-router-warmup.sh` | **novo** (P1) — SessionStart: staleness + ping Ollama |
| Builder do índice | `scripts/build_skills_index.py` | **novo** (P1) — scanner + embedder batch (299 docs ≈ 1-3s na RTX 5000); `--no-embed` mantém Camada A viva sem Ollama |
| Feedback | `hooks/harness-skill-feedback.sh` | **novo** (P2) — PostToolUse matcher `Skill` → router-log.jsonl append-only |
| CLI de manutenção | `scripts/skill_catalog.py` + `skills/skill-catalog/SKILL.md` | **novo** (P2) — status / rebuild / stats / prune-report (mineração de transcripts) / hygiene-report |
| Registro | `hooks/hooks.json` | +3 blocos autocontidos (router, warmup, feedback) — rollback = deletar bloco |
| Health check | `scripts/health-check.sh` | checks: índice fresco, Ollama alcançável, modelo presente (WARN-only) |
| Aggregates | `scripts/migrate_state.py` | (P2) bloco aditivo `aggregates.router` em signals.json (version continua 3) |
| Higiene | `skills/*/SKILL.md` (5 arquivos) | (P2) reescrever descriptions: **harness-workflow** (diz "v2" — stale; trigger enterrado), compress-memory, verify-against-spec, discuss, validate-plan |
| Testes | `tests/router/golden-prompts.json` + `test_router.py` + `scripts/bench-router.sh` | **novo** (P1) — ~20 prompts PT+EN com esperados + negativos; latência e degradação |
| Versão | `.claude-plugin/plugin.json` | 3.2.0 → 3.3.0 |

**Intocáveis**: `harness-classify.sh`, `harness-reclassify.sh`, `state-lock.sh`, schemas,
`record_signal.py` (feedback do router usa JSONL próprio), `.claude.json` (read-only sempre),
hooks/env/model do `settings.json`.

## 5. Poda P0 (decidida e executada em 2026-07-23)

| Plugin | Skills | Racional | Decisão do usuário |
|---|---|---|---|
| data-agent-kit-starter-pack | 56 (+11 MCP stdio) | Stack de dados GCP — usuário confirmou que não usa | **Desabilitar** |
| growthbook | 25 | A/B testing SaaS — fora de domínio, zero uso | **Desabilitar** |
| datarobot-agent-skills | 14 | Plataforma DataRobot — zero uso | **Desabilitar** |
| hunter | 13 (+~100 tools MCP) | Prospecção de vendas — fora de domínio | **Desabilitar** |
| azure-sql-developer | 11 | Usuário confirmou que não trabalha com Azure SQL | **Desabilitar** |
| langfuse-observability | ~1 | Duplicata do `langfuse` 1.4.1 | **Desabilitar** |
| data-engineering | 35 | Em domínio (dados/AI) | Manter |
| 12 dirs de versão stale + 2 `marketplaces/temp_*` | — | Não carregados (só disco) | Remover (cosmético) |

Efeito esperado: **~120 skills fora do catálogo residente (~12–18K tokens/sessão)**, tudo reversível
com `/plugin enable X` (cache intocado; o índice do router continua indexando os desabilitados).
`skillListingBudgetFraction`/`skillListingMaxDescChars`: **não tunar ainda** — só com medição pós-poda.

## 6. Roadmap faseado (cada fase shippável e com rollback)

- **P0 — Poda + limpeza** (sem código no hot path): executado nesta fase. Rollback: `/plugin enable`.
- **P1 — Índice + router MVP** (v3.3.0-beta): builder, router, warmup, health-check, golden set.
  Gates: top-3 ≥ 80% no golden set e zero injeção nos negativos · p95 warm < 500ms · Ollama-down →
  stdout vazio < 100ms · suíte existente verde e classify byte-idêntico · 1 pipeline L2 manual e2e.
  Rollback: deletar 2 blocos do hooks.json (índice vira dado inerte).
- **P2 — Feedback + manutenção** (v3.3.0 final): feedback hook, aggregates.router, boost de
  uso/recência (peso ≤ 0.1 — semântica domina), skill-catalog, higiene de descriptions,
  `--vault-note` opcional. Rollback: remover bloco PostToolUse.

## 7. Riscos

| Risco | Mitigação |
|---|---|
| Cold start Ollama (unload → 1º embed 1-3s) | `keep_alive=30m` em toda chamada + warm ping no SessionStart + timeout 1200ms → Camada A |
| Ollama fora | Degrada p/ aliases; `--no-embed` mantém índice de metadados; health-check reporta WARN |
| Regressão de latência de hook | Hook paralelo separado; bench p95 como gate de P1; sem state lock |
| Windows/git-bash/Python | Reusar padrões comprovados: `PYTHONUTF8=1`, `cygpath -w`, `MSYS_NO_PATHCONV=1`, `os.replace` atômico |
| Child de background morre com o hook (MSYS) | Lazy rebuild via marker `.stale`; índice velho é quase sempre correto |
| Drift cache × clone local | Marketplace é GitHub (`Lharden/harness4claude`): ship = push + `/plugin update`; dev/teste no clone (hooks resolvem `CLAUDE_PLUGIN_ROOT` com fallback `dirname $0/..`) |
| Fadiga de dica / poluição de prompt | Threshold + margem, dedupe por sessão, ≤90 tokens, frase explícita "ignore se não se aplica" |
| Torn read de state.json (sem lock) | Escritas são `os.replace` atômico; falha de parse → tratar como "sem pipeline ativo" |

## 8. Decisões em aberto (para o review deste doc)

1. **context7-trigger.py** (dormente): recomendação = *fold* da lista LIBS na camada de aliases em P2
   (um hook a menos). Alternativas: manter dormente ou registrar como está.
2. **Política de disparo da Camada B**: recomendação = "sempre em prompt elegível"; reavaliar com uma
   semana de telemetria de P1 (flip de 1 linha).
3. **Dim do embedding**: 768 (ship) vs Matryoshka-256 (3× menor/mais rápido) — só se a latência doer.
4. **Benchmark bge-m3** no golden set em P2: opcional, default = pular.

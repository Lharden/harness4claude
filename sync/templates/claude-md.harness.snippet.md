<!-- HARNESS4CLAUDE:BEGIN — gerenciado por scripts/sync-machine.sh. Nao edite entre os marcadores; rode o sync para atualizar. -->
## Harness v3 SDD (MANDATORY)
- Hook classifica cada prompt como L0/L1/L2 (regex = `classification_meta.suggested`); harness-workflow confirma/corrige semanticamente (`classification_meta.final`/`agreed`). Loop de accuracy em signals.json (`aggregates.classify`)
- "HARNESS v3 CLASSIFIED" L1/L2 no `additionalContext` -> **julgar primeiro o nivel real, sem carregar a skill.** A classificacao vem de regex e acerta ~30% (`aggregates.classify.proxy_regex_vs_observado = 0.297`)
  - **L0 real:** so registrar e seguir direto: `python "<PR>/scripts/confirm_classification.py" --final "L0-question" --expect-task "<task_id do hook>" --harness-dir "<balde>"`. `<PR>` = conteudo de `~/.claude/harness/plugin-root`; `<balde>` = saida de `python "<PR>/scripts/harness_paths.py" --cwd "<cwd>" --session-id "<session_id>"`, resolvido uma vez por sessao. Uma chamada por linha, caminho literal
  - **L1/L2 real:** invocar `Skill(skill="harness-workflow")` ANTES de responder e seguir o protocolo dela (confirmacao, fases, gates)
  - Motivo (2026-09-24): carregar a skill (~10 mil tokens) so para rebaixar a L0 era o maior custo fixo do harness
- "HARNESS v3 CONTINUING" ou "HARNESS v3 RESUMING" -> invocar harness-workflow para continuar/retomar
- L0: executar diretamente, sem pipeline
- State: `~/.claude/harness/state.json` | CLAUDE.md tem prioridade absoluta

### Skills SDD v3
- `write-spec` — spec formal completa (user stories P1/P2/P3, AC Given/When/Then, [NEEDS CLARIFICATION], boundaries)
- `write-spec-light` — spec enxuta ~50 linhas para L1 (overhead humano ~2 min)
- `design-doc` — design tecnico separado (arch, data model, API contracts, test strategy, risks) — entre grill-me e validate-plan em L2
- `verify-against-spec` — verifica cobertura item-por-item spec<->implementacao com evidencias concretas

### Pipelines v3
- **L1-feature**: `write-spec-light` -> `tdd` -> `verify-against-spec`
- **L2-feature**: `discuss` -> `brainstorming` -> `write-spec` -> `grill-me` -> `design-doc` -> `validate-plan` -> `tdd` -> `verify-against-spec`
- **L2-architecture**: inclui `write-spec` + `grill-me` + `design-doc` entre brainstorming e validate-plan
- **L2-refactor**: adiciona `write-spec` + `design-doc` apos grill-me
- **L1/L2-bug**: inalterados (bug nao precisa spec formal)

### Artefatos SDD
- `docs/specs/{feature-slug}-spec.md` (ou `-spec-light.md`)
- `docs/specs/{feature-slug}-design.md`
- `docs/specs/{feature-slug}-verification.md`

### Principios SDD
- AI gera 90%+ das specs; humano valida e decide ambiguidades
- `[NEEDS CLARIFICATION]` forca decisoes explicitas (nunca assumir)
- AC Given/When/Then alimentam TDD (1 AC = 1 teste)
- Spec e living doc (spec-anchored para L2)

### Health check
- Rodar `bash "$(cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root")/scripts/health-check.sh"` para verificar dependencies, state, hooks e skills
- Requer `python` (unica dependencia dura — todo hook parseia JSON com `python -c` inline; nenhum usa `jq`)

### Escopo e TTL do estado
- `state.json`, `.session-files-count` e traces sao por projeto, em `~/.claude/harness/projects/<slug>/`; `signals.json` fica agregado na raiz
- `HARNESS_SCOPE=global` volta ao state unico da maquina
- Pipeline ativo expira apos `HARNESS_PIPELINE_TTL_H` horas (default 24) e e registrado como abandonado
- `HARNESS_ROUTER=1` liga o skill-router semantico (desligado por padrao; exige Ollama)

## Branch Keeper (ramificacao passiva)
- Sensor em `UserPromptSubmit` + `Stop`: camada A (regex PT/EN) + camada B (embedding vs ancora da sessao). Ramo exige A **e** B; sem Ollama, A sozinha oferece marcada como degradada
- `HARNESS v3 BRANCH SIGNAL` chega no `additionalContext` (rotulado "hook additional context"). **Ele NAO manda oferecer** — manda OLHAR: julgue se o que apareceu tem vida propria; se sim, consulte `may-offer` e so entao invoque `branch-out`; se nao, ignore EM SILENCIO, sem mencionar o sinal
- **Ramo** = ideia com vida propria -> oferece abrir sessao nova (`wt` + PS7, `claude --session-id <uuid>`) com prompt-semente. **Deriva** = conversa escorregando -> uma frase, nunca janela
- Autocheck: se eu mesmo abrir assunto paralelo, ofereco ramo sem esperar o hook. O sensor e rede, nao substituto
- Sinal nascido no `Stop` e guardado em `pending-signal.json` e entregue no proximo `UserPromptSubmit` — no Stop o turno ja acabou e "antes de responder" chegaria tarde
- Tema ramificado fica **parkeado** no pai (`<harness-parked>` a cada turno): nao desenvolver la; `/branch recall <slug>` desfaz. **So aparece depois que existir ramo aceito** — sem `branches.json` o bloco e vazio
- "Agora nao" **parkeia**, nunca descarta. So descarte explicito apaga
- Estado: `~/.claude/harness/projects/<slug>/branches.json` + sementes/launchers em `branches/`. Telemetria no bloco `branch` de `signals.json`
- Config (nomes REAIS lidos pelo codigo — os curtos que estavam aqui antes nao existiam e setar `FLOOR=0.7` nao fazia nada): `HARNESS_BRANCH=0` desliga; `HARNESS_BRANCH_HOST=none` nao abre janela; `HARNESS_BRANCH_MAX_OFFERS=2`, `HARNESS_BRANCH_MAX_OPEN=3`, `HARNESS_BRANCH_COOLDOWN_TURNS=8`, `HARNESS_BRANCH_FLOOR=0.55`, `HARNESS_BRANCH_DRIFT_FLOOR=0.35`, `HARNESS_BRANCH_DRIFT_SAMPLE=2` (camada B so roda com marcador ou na amostragem — embed em todo prompt custaria ~1s)
- **Calibrado em 2026-09-02, e o resultado mudou o desenho** (703 pares rotulados por supervisao distante):
  - **Camada A**: precisao ~0.10. `e se` e o unico padrao que ja acertou (2 de 20 disparos); 12 dos 16 nunca dispararam. 19 candidatos testados, nenhum aprovado
  - **Camada B**: DESLIGADA por default (`HARNESS_BRANCH_LAYER_B=0`). Melhor F1 das 4 metricas: 0.209 contra 0.108 do acaso, e a direcao saiu invertida
  - **Consequencia**: o sinal deixou de mandar oferecer e passou a pedir julgamento. Falso positivo deve ser ignorado EM SILENCIO. O autocheck e o `/branch` sao os caminhos que valem; o hook virou orcamento, dedupe e parking
  - Tabelas em `~/.claude/harness/calib/`; refazer com `scripts/calibrate_branch_layer_a.py` e `calibrate_branch_floor.py`

## Canais de hook (medido 2026-09-01)
- `systemMessage` **nao chega ao modelo** — e canal de UI. Nos 343 transcripts, 100% das linhas com systemMessage no stdout tem `content` vazio. Custo real: 81 `CLASSIFIED` em 47 sessoes, 0 invocacoes de `harness-workflow`
- Chegam: **stdout cru** (vira `content`, sem marca de proveniencia — so para DADO), **`hookSpecificOutput.additionalContext`** (rotulado — para toda INSTRUCAO), e **`{"decision":"block","reason":...}` no Stop** (interrompe — so para gate)
- Todo hook emite via `hooks/emit.py`, que escolhe o canal e registra em `~/.claude/harness/emissions.jsonl`. Auditar com `python scripts/check_hook_liveness.py --delivery`

## Obsidian (vault-bridge)
- Vault root via `env.VAULT_PATH`; sub-vault de espelhamento = `<VAULT_PATH>/AI-Brain` (ou `AI_BRAIN_PATH`)
- MCP servers `obsidian-fs` (mcpvault) e `obsidian` (REST https) em `~/.claude.json`; segredo do REST via `${OBSIDIAN_API_KEY}`
- `harness-precompact.sh` chama `vault_sync.py` no handoff; degrada graceful se o vault nao existir

## graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) — qualquer input vira knowledge graph. Trigger: `/graphify`
- Para perguntas sobre codebase: rodar `graphify query "<pergunta>"` quando `graphify-out/graph.json` existir; `graphify path "<A>" "<B>"` para relacoes; `graphify explain "<conceito>"` para conceitos
- Apos modificar codigo: `graphify update .` (AST-only, sem custo de API)
- Setup por maquina: `bash "$(cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root")/scripts/setup-graphify.sh"`
<!-- HARNESS4CLAUDE:END -->

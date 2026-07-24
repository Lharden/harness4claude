---
title: Estratégia Autoevolve — Autorreforma do Harness4Claude
system: Harness4Claude
document_type: strategy
status: approved
version: 1.0
created: 2026-07-24
base_plan: PLANO_AUTOREFORMA_HARNESS4CLAUDE.md v1.0
base_commit: a56ee80
---

# Estratégia Autoevolve — Autorreforma do Harness4Claude

## 0. O que este documento é

O `PLANO_AUTOREFORMA_HARNESS4CLAUDE.md` define **o quê** e **os gates**. Este documento define **em que ordem real, com qual código real, e como o harness executa a si mesmo** sem quebrar o objeto que está medindo.

O plano é normativo: suas proibições (§3.2), stop conditions (§3.3) e critérios de aceitação (§20) continuam integralmente em vigor. Esta estratégia não relaxa nenhum gate — ela ajusta a sequência ao estado real e substitui ferramentas por equivalentes de potência comparável dentro da stack permitida.

## 1. Estado real que motiva os desvios

Levantamento sobre `a56ee80` (v3.3.0-beta.1), 2026-07-24. Detalhamento completo em [INVENTORY.md](INVENTORY.md).

Cinco achados alteram materialmente o plano:

1. **O sistema que roda não é o sistema que se edita.** O Claude Code carrega `~/.claude/plugins/cache/harness4claude/harness4claude/3.2.0` (commit `24c1812`, 2026-06-17). Main está 5 semanas à frente. O skill-router v3.3.0-beta.1 está mergeado e **inativo em runtime**. Qualquer baseline coletado hoje mediria um sistema que main já não descreve.
2. **Os testes operam sobre o estado de produção.** `test_harness.py` (56 testes) faz backup/restore de `~/.claude/harness` real em `setUpClass`. Isso impede crash-injection, gera flakiness e cria risco direto durante a reforma.
3. **O maior escritor do state em runtime é o LLM.** A skill `harness-workflow` instrui edição direta de `state.json` via `Edit` tool — fora de lock, atomicidade, schema-check e de qualquer API. É o risco mais grave do sistema e **não consta do plano**.
4. **A telemetria nunca funcionou.** `signals.json` real: `tasks: []`, agregados zerados, desde sempre. O disparo depende do LLM lembrar de executar `record_signal.py` no DONE. Toda métrica de qualidade do plano (§4.2) é hoje inobservável.
5. **Escrita de estado é heterogênea.** `_atomic_write_json` existe em 3 cópias; `harness-reclassify.sh` escreve com lock mas sem atomicidade; `migrate_state.py` sem lock nem atomicidade; heredocs de bootstrap sem lock.

Evidência colhida no bootstrap desta própria reforma: o `state.json` continha uma task `L2-feature` **órfã** de 2026-07-24T17:06, de uma sessão não relacionada (prompt sobre preparação de apresentação), ainda `status: active`. Confirma simultaneamente o risco do singleton compartilhado (R8) e a ausência de fechamento confiável de task (R5).

## 2. Decisões estruturantes

| # | Decisão | Racional | ADR |
|---|---|---|---|
| 1 | **Pré-fase P-1 antes da Fase 0** | O plano assume um sistema único, observável e testável em isolamento. Nenhuma das três condições existe. Sem P-1, a Fase 0 mede o objeto errado com instrumentos que corrompem produção. | — |
| 2 | **Fases 2 e 3 fundidas** | Zero código de escopo existe hoje. Construir `scope_id` sobre arquivos JSON para depois migrar a SQLite é big-bang disfarçado de duas migrações. O store novo nasce SQLite + scope_id + fencing; a escada A–E da Fase 2 aplica-se diretamente a ele. | [ADR-001](ADR/ADR-001-store-fundido.md) |
| 3 | **A escada de migração vira feature flag em runtime** | `~/.claude/harness/flags.json` com `store_mode: shadow\|dual_read\|dual_write\|new_primary\|legacy_ro`. Promoção de etapa = mudança de flag com gate humano; rollback = voltar o flag. Satisfaz o requisito de feature flag da Fase 13 desde a Onda 2, sem re-ship por etapa. | [ADR-001](ADR/ADR-001-store-fundido.md) |
| 4 | **Guard anti-`Edit` antecipado para a Onda 1** | O risco R1 ataca o store **legado** também. Não faz sentido esperar o SQLite para proteger o que já existe. | — |
| 5 | **Substitutos densos, não cortes** | TLA+, pm4py, mutation completo e HNSW são substituídos por implementações stdlib com potência equivalente **na escala real do sistema** — em dois casos, com potência superior. GraphBLAS é o único corte real. | [ADR-002](ADR/ADR-002-substitutos-densos.md) |
| 6 | **O gap de deployment vira o mecanismo de canário** | Cache do plugin = canal estável pinado. Promoção de onda = ship deliberado com verificação de proveniência. O runtime nunca muda no meio de uma onda. | [ADR-000](ADR/ADR-000-objeto-de-medicao.md) |

## 3. Substitutos densos

Detalhamento, matemática e critérios de reabertura em [ADR-002](ADR/ADR-002-substitutos-densos.md). Resumo:

| ID | Substitui | Implementação | Ganho/perda vs. original |
|---|---|---|---|
| **D1** | TLA+/PlusCal | Model checker explicit-state em stdlib (~300 L) com **twin-execution**: a successor function executa `harness_lib/store.py` real contra SQLite `:memory:`. Symmetry reduction, partial-order reduction (Mazurkiewicz), counter abstraction, safety sobre os 8 invariantes, liveness limitada por detecção de lasso (Tarjan/SCC). | **Superior**: elimina o drift modelo↔código, a fraqueza estrutural do TLA+. Perde propriedades temporais em espaço ilimitado — os 8 invariantes do plano são todos de safety. |
| **D2** | pm4py | Métricas canônicas sobre a tabela `events`: **fitness por alignments** (A*/Dijkstra no produto síncrono trace×FSM), **precisão por escaping edges** (ETConformance), **DFG + cortes do Inductive Miner** (self-join SQL + componentes conexos). | **Equivalente**: os mesmos números da literatura, especializados para modelo declarado, sem pandas/numpy. |
| **D3** | Mutation testing completo | Engine própria via `ast` stdlib (ROR/COR/AOR/constantes/deleção/retorno) + **coverage-guided selection** por `sys.monitoring`: cada mutante roda só os testes que cobrem a linha mutada. O(M×T) → O(M×T_cov). ~100–200 mutantes em minutos. Mais os 8 mutantes semânticos do §14 como testes dirigidos (cobrem bash↔Python, que engines não alcançam). | **Equivalente em cobertura útil, viável em Windows.** mutmut com suíte de 231 s levaria dias. |
| **D4** | HNSW/GraphBLAS | FTS5/BM25 para candidatos → re-rank por **cosine exato** (f16 cacheado) → fusão por **RRF**. **Personalized PageRank reabilitado** (power iteration em dict-of-dicts; 775 nós/1.041 arestas ≈ sub-ms), viabilizando a fórmula de ranking completa do plano §12. | **Superior a HNSW nesta escala** (exato não perde recall; o gargalo medido é a chamada Ollama, não o dot product). GraphBLAS cortado. |
| **D5** | *(reforço, não substituição)* | Rigor estatístico transversal: **bootstrap CIs** e **Mann-Whitney U** em toda decisão de benchmark; **isotonic regression via PAV** + **Brier score** + posterior **Beta-Binomial** para a calibração de reviewers da Fase 9. | Dá matemática real ao campo `posterior` do §14, hoje um threshold fixo de 0.5. |

## 4. Pré-fase P-1 — "Chão de fábrica"

Não prevista no plano. Obrigatória antes da Fase 0.

### P-1.a — Fechar o gap de deployment

**Ação.** `marketplace.json` e `plugin.json` → `3.3.0`; os 10 caminhos hardcoded para o clone dev → `${CLAUDE_PLUGIN_ROOT}`; `VERSION_STAMP` (versão + commit) emitido por todo hook; bloco de proveniência no `health-check.sh` comparando cache × git HEAD × marketplace; procedimento documentado em [SHIP.md](SHIP.md).

**Gate.** `health-check.sh` prova `cache == main == marketplace`; um evento de hook real emite o stamp esperado.

### P-1.b — Testes herméticos

**Ação.** Env var `HARNESS_DIR` (default `~/.claude/harness`) nos ~10 arquivos que hardcodam o path; fixture em `tests/conftest.py` criando diretório temporário por teste, com **assert de segurança** que falha a suíte se qualquer teste resolver para o path real; migrar `test_harness.py` do padrão backup/restore.

**Precedência.** É pré-condição técnica das Fases 0 (baseline reprodutível), 3 (crash tests), 10 (stress) e do próprio modo autoevolve — as sessões de reforma rodam com o harness ativo, e testes não-herméticos escreveriam no state da própria sessão.

**Gate.** Suíte verde 3× consecutivas com hash de `~/.claude/harness` idêntico antes e depois.

### P-1.c — Lib Python compartilhada

**Ação.** `scripts/harness_lib/` (stdlib puro):
- `state_io.py` — `read_state`, `atomic_write_json` (write→fsync→`os.replace`) e o ponto de injeção `HARNESS_CRASH_POINT` que o plano §5 exige para crash tests;
- `lock.py` — wrapper do mkdir-mutex preservando a semântica de stale/owner do `state-lock.sh`;
- `paths.py` — resolução de `HARNESS_DIR` + cygpath, eliminando o Python inline duplicado nos hooks.

E `scripts/harnessctl.py` — CLI mínimo (`state read`, `state write --patch`, `signal record`). Ainda **não** é a API de transição da Fase 4; é o funil único de escrita.

**Gate.** Zero cópias de atomic-write fora da lib (verificado por grep-gate); escritores de reclassify e migrate atômicos sob teste de kill.

### P-1.d — Higiene de versão e docs

Num sistema onde o LLM lê documentação como instrução de runtime, **doc errado é bug de runtime**. `harness-workflow/SKILL.md` ainda se declara "v2" e cita skills inexistentes; o README descreve pipelines que divergem do `PIPELINES` real e manda rodar um teste que não existe.

**Gate.** Nenhum artefato citado em skill ou doc que não exista no repo (checker adicionado ao health-check).

## 5. Ondas

Esforço em sessões L2 (≈ um pipeline `write-spec`→`verify-against-spec` completo). Total estimado: 33–45.

| Onda | Conteúdo | Fases do plano | Gate de saída | Sessões |
|---|---|---|---|---|
| **O0** Chão de fábrica + baseline | P-1.a–d; INVENTORY; RISK_REGISTER; BASELINE; golden set de classificação; `bench_hooks.py` com bootstrap CIs; corpus adversarial do git-guard medindo a taxa de bypass real | 0 | cache==main==marketplace; suíte hermética verde 3×; zero atomic-write fora da lib; BASELINE com métricas mensuráveis preenchidas e as demais marcadas `N/A — instrumentação inexistente`; nenhum código estrutural novo ativo | 4–6 |
| **O1** Observabilidade + 1º guardrail | `harness_lib/telemetry.py` (JSONL versionado, sequence monotônica por task, redaction, rotação, falha nunca propaga); instrumentar os 8 hooks; `harness-state-guard.sh` em **observe**; root-cause e correção do signals vazio (disparo por hook **Stop**, não por disciplina do LLM); log de confidence bruta de todos os findings | 1 | Task L2 reconstruível apenas do JSONL; overhead p95 dentro do limite documentado; segredos sintéticos ausentes; guard registrando edições | 3–4 |
| **O2** Store novo em shadow | `harness_lib/store.py`: SQLite em `~/.claude/harness/db/`, schema §8 + `scope_id` §7, WAL, `BEGIN IMMEDIATE`, CAS por revision/owner_epoch; `flags.json` em `shadow`; **produção instrumentada é o canário-shadow**; dual-read; crash tests nos 9 pontos do §8; testes de escopo (10k combinações, case do Windows, worktree removido); WAL validado em NTFS | 2+3 (A–B), 13-c1 | N tasks reais em shadow sem divergência inexplicada; atomicidade sob kill; stale owner nunca escreve; legado intocado como autoridade | 5–7 |
| **O3** Dual-write + API + FSM | `dual_write` com hash normalizado por escrita; `harnessctl` completo (§9); `schemas/fsm.json` derivada dos campos já presentes em `state.schema.json`; skill reescrita — **LLM proibido de editar state**, guard em **deny**; 8 mutantes críticos; **D1** model checker | 2-C, 4, 5 | Zero divergência de hash no canário; grep estático sem edição direta de store; gate humano não-forjável; model checker sem contraexemplo; wrappers legados equivalentes | 5–7 |
| **O4** New-primary + propriedades + stress | `new_primary` (legado vira espelho/fallback); propriedades §15 em hypothesis stateful; stress 2/4/8/16 workers, 10k transições, kill aleatório, disk-full; canário L0 real | 2-D, 10, 13-c2/c3 | Nenhum invariante violado; p99 documentado; rollback drill por flag em ambas as direções; zero cross-scope em uso real | 4–5 |
| **O5** Guardas + qualidade densa *(paralelizável)* | Shell-guard `shlex` observe→dual-decision com corpus adversarial e `rm -rf`, etapa C só com zero-bypass; **D4** retrieval híbrido; **D3** engine de mutação; **D2** conformance; **D5** calibração; Graphify com manifesto/stale/fallback medidos | 6, 7, 8, 9, 11 | Zero bypass no corpus obrigatório; FPs abaixo do limite; mutation score crítico 100%; fitness/precision computados; primeiro relatório de calibração | 6–8 |
| **O6** Hot paths + fechamento | Otimizações §17 com protocolo hipótese/baseline/Mann-Whitney/decisão; `legacy_ro`; rollback drill completo; RELATÓRIO FINAL §21; corpus da comparação com Codex preparado (execução adiada) | 12, 2-E, 13 | Critérios §20 aplicáveis, com pendências datadas; zero regressão proibida vs. BASELINE | 3–4 |

### Paralelismo

- **O0**: P-1.a ∥ P-1.b (árvores disjuntas). P-1.c depende de P-1.b.
- **O1**: telemetria ∥ state-guard.
- **O5**: os quatro blocos são independentes entre si.
- **Nunca**: duas sessões de reforma simultâneas enquanto o state for singleton (até O4).

## 6. Modo de execução autoevolve

### Bootstrap

```bash
git tag -a pre-reform-base <HEAD> -m "Baseline da autorreforma"
git worktree add worktrees/harness4claude-self-reform -b self-reform/main
# branches por onda: self-reform/w0-chao-de-fabrica, self-reform/w1-telemetria, ...
```

Backup de `~/.claude/harness/` em `docs/self-reform/claude/backups/<data>/` — diretório **gitignored** por design: contém estado de sessão, não deve entrar no histórico. Hashes registrados em [MIGRATION_LOG.md](MIGRATION_LOG.md).

### Congelamento do objeto medido

Resolve o paradoxo do plano §5 ("não alterar o objeto que está sendo medido durante a medição"):

- O runtime é **sempre** o plugin em cache, pinado no commit da última promoção de onda.
- Sessões de reforma editam o **worktree** — nunca o cache, nunca o clone que alimenta o cache.
- Promoção = merge `self-reform/wN-*` → `main` → ship → health-check de proveniência → tag `reform-wN-shipped`. Só em fronteira de onda, só com gate humano.
- Rollback de onda = `/plugin update` para a tag anterior, ou reversão de flag. **Nunca** `git reset --hard` (proibição §3.2).
- Shadow e dual-write rodam **em produção instrumentada** — não existe ambiente sintético separado. A autoridade permanece no legado até o gate da O4.

### Cada incremento é uma task L2 do próprio harness

```
write-spec → grill-me → [GATE humano] → design-doc → validate-plan → [GATE humano]
→ tdd (no worktree) → verify-against-spec + wf-verify-multimodel → [GATE humano no ship]
```

Dogfooding deliberado: cada onda gera traces e signals reais de pipeline L2 — que são exatamente os dados de calibração que a Fase 9 precisa. A reforma alimenta a própria evidência.

**Disciplinas de bootstrap** (Ondas 0–1 rodam com o harness antigo se auto-modificando):
- o LLM nunca executa scripts do worktree contra o `HARNESS_DIR` real — testes usam o fixture hermético;
- `record_signal.py --expect-task` obrigatório em todo fechamento de task de reforma;
- se uma sessão quebrar o state real: restaurar do backup — é a stop condition "perda de estado" do §3.3.

### Invariantes operacionais

Colar no topo de cada spec de onda:

1. Runtime = cache pinado; muda só em fronteira de onda, com gate humano e health-check de proveniência.
2. Legado é autoridade até o gate da O4; nenhuma escrita nova sem espelho no legado até a O6.
3. **Nenhum invariante novo pode depender de disciplina do LLM** — sempre hook, CLI ou teste. Lição direta do signals vazio e do `--expect-task`.
4. Proibições §3.2 e stop conditions §3.3 integralmente em vigor.
5. Uma sessão de reforma por vez até `new_primary`.
6. Todo corte tem ADR com critério de reabertura. Cortar não é esquecer.

## 7. Artefatos

```
docs/self-reform/claude/
  STRATEGY.md          este documento
  INVENTORY.md         estado real levantado (Fase 0 §4.1)
  BASELINE.md          métricas de partida (Fase 0 §4.2)
  RISK_REGISTER.md     riscos do plano §4.3 + R1–R9 do estado real
  SHIP.md              procedimento de promoção e verificação de proveniência
  MIGRATION_LOG.md     diário de bordo: cada promoção, flag, rollback
  TEST_MATRIX.md       cobertura por invariante
  ADR/                 decisões arquiteturais
  waves/wN-<nome>/     spec.md, design.md, verify.md por onda
  backups/<data>/      gitignored — estado pré-onda
```

## 8. Primeiro incremento

1. Bootstrap: tag, worktree, backup. **Feito** — ver [MIGRATION_LOG.md](MIGRATION_LOG.md).
2. Commitar o plano (estava untracked) e os documentos de governança iniciais.
3. Task L2 **P-1.b testes herméticos** pelo pipeline do próprio harness.
4. Task L1 paralela **P-1.d higiene de docs**.
5. **Nenhum ship nesta sessão.** P-1.a só é promovido no fim da O0, em commit único, com gate humano.

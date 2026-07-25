---
adr: 002
title: Substitutos densos para TLA+, pm4py, mutation completo e HNSW
status: accepted
date: 2026-07-24
deciders: Leonardo, Harness4Claude
---

# ADR-002 — Substitutos densos, não cortes

## Contexto

O plano invoca quatro ferramentas pesadas: TLA+/PlusCal (§10), process mining (§16), mutation testing (§14) e busca aproximada com HNSW/GraphBLAS (§12). A restrição de stack é Python stdlib + bash + SQLite, sem dependências pesadas, com Windows como plataforma primária e uma equipe de um desenvolvedor mais um agente.

A direção dada foi explícita: **não enxugar nem enfraquecer o projeto de melhoria densa** — buscar as possibilidades matemáticas e computacionais que se equiparem, ou cheguem perto, das features substituídas.

O ponto de partida é que cada uma dessas ferramentas resolve um problema em uma escala específica, e a escala real deste sistema é conhecida: FSM de ~10 estados, 2–3 sessões concorrentes, grafo de 775 nós e 1.041 arestas, índice de 276 skills, núcleo Python de 1–2k LOC após P-1.c, suíte de 231 s no Windows. Em várias dessas dimensões, o algoritmo exato é mais forte que o aproximado.

## Decisão

Quatro substituições e um corte.

---

### D1 — Model checker próprio com twin-execution (substitui TLA+/PlusCal)

**O que o plano pede.** Spec TLA+/PlusCal mínima para duas sessões, duas tasks, aquisição e expiração de lease, CAS de revision, approval, verify, finish, stale owner, crash e recovery. Model checker sem contraexemplo para oito invariantes: `NoCrossScope`, `NoPrematureDone`, `OnlyLeaseOwnerMutates`, `RevisionMonotonic`, `ApprovalCannotBeForged`, `VerifiedRequiresEvidence`, `TerminalStateStable`, `ArtifactBelongsToTask`.

**O que será construído.** Um model checker explicit-state em stdlib (~300 L), com as técnicas de redução que o TLC também usa:

- **Espaço de estados** — produto síncrono de N sessões × FSM (`schemas/fsm.json`) × tabela de leases × store. BFS com visited-set por hash canônico.
- **Symmetry reduction** — sessões são intercambiáveis; canonicalizar por ordenação antes do hash reduz o espaço por um fator fatorial em N.
- **Partial-order reduction** — transições sobre escopos disjuntos comutam; explorar um representante por classe de equivalência de Mazurkiewicz.
- **Counter abstraction** — `revision` importa apenas como ordem relativa; o domínio concreto colapsa num domínio abstrato pequeno.
- **Safety** — os oito invariantes checados em todo estado alcançável.
- **Liveness limitada** — detecção de lasso (ciclo alcançável sem transição de progresso) via SCC de Tarjan, cobrindo "task iniciada eventualmente atinge estado terminal sob weak fairness".

**A vantagem sobre TLA+.** A successor function não é um modelo escrito à parte: ela **executa a implementação real** de `harness_lib/store.py` contra um SQLite `:memory:`. O modelo *é* o código. Isso elimina o drift modelo↔código — a fraqueza estrutural de qualquer verificação formal sobre especificação separada, e um custo de manutenção permanente que este time não teria como pagar.

**Viabilidade.** 2–3 sessões × ~10 estados de FSM × leases × revisions abstratas fica na ordem de 10⁴–10⁶ estados. BFS trivial em Python. O TLC é igualmente explicit-state; nesta escala, potência comparável.

**O que se perde.** Propriedades temporais em espaço não-limitado e liveness com fairness forte. Impacto baixo: os oito invariantes do plano são todos de safety.

**Critério de reabertura.** FSM crescer além do enumerável (mais de ~8 sessões modeladas, ou leases em tempo contínuo), ou um bug de concorrência escapar para produção sem ter sido pego pelo checker.

---

### D2 — Process mining canônico em SQL + stdlib (substitui pm4py)

**O que o plano pede.** Fitness, precisão do modelo, fases puladas, loops, rework, retries, tempo por fase, waiting em gate humano, taxa de rollback, divergência de classificação, hit rate do Graphify, compressão de contexto, findings por custo, falsos blockers, escaped defects (§16).

**O que será construído.** As métricas canônicas da literatura, não contagens simplificadas:

- **Fitness por alignments** — o padrão-ouro. O alinhamento ótimo entre trace e modelo é o caminho de custo mínimo no produto síncrono do autômato do trace com a FSM; resolvido por A*/Dijkstra em stdlib (~150 L). O custo conta movimentos assíncronos (log-only e model-only), e `fitness = 1 − custo / custo_do_pior_caso`. É exatamente a métrica do pm4py, especializada para o caso de modelo conhecido.
- **Precisão por escaping edges** (ETConformance) — em cada estado do modelo alcançado pelo log, comparar as transições permitidas com as observadas; `precision = 1 − média das arestas de escape`.
- **Discovery por DFG com cortes do Inductive Miner** — o Directly-Follows Graph anotado com frequência e duração é um self-join sobre `events`; os cortes sequence/XOR/parallel/loop saem de análise de componentes conexos do DFG, que é como o IMd (Inductive Miner directly-follows) opera. O DFG anotado é exportável como mermaid para o vault.
- Métricas operacionais do §16 por SQL direto sobre `events`.

**Por que não pm4py.** Arrastaria pandas e numpy, violando a restrição de stack, para *descobrir* um processo que aqui é **declarado** em `fsm.json`. Conformance contra modelo conhecido é o caso fácil do problema.

**O que se perde.** Descoberta automática de fluxos de facto radicalmente diferentes do declarado. Impacto baixo a médio — e o próprio DFG cobre boa parte disso.

**Critério de reabertura.** Fitness sistematicamente baixo com padrões que as queries não explicam.

---

### D3 — Engine de mutação própria, coverage-guided (substitui mutation testing completo)

**O que o plano pede.** Criar mutantes do harness e a suíte deve matar os críticos, com mutation score crítico de 100% (§14). O plano nomeia oito: inverter exit code, ignorar owner epoch, aceitar DONE sem artefato, permitir bypass de gate, deixar state singleton, não invalidar cache, liberar comando destrutivo, não verificar graph HEAD.

**O que será construído.** Duas camadas.

*Camada 1 — engine real via `ast` stdlib* (~200 L), com os operadores clássicos: ROR (relacionais), COR (booleanos), AOR (aritméticos), substituição de constantes, deleção de statement, mutação de valor de retorno. Aplicada a `scripts/harness_lib/`, o núcleo Python que P-1.c cria.

*A matemática que a torna viável — coverage-guided selection.* Mapear teste → linhas executadas via `sys.monitoring` (Python 3.12+, stdlib, overhead baixo). Para cada mutante, rodar **apenas os testes que cobrem a linha mutada**. O custo cai de O(M×T) para O(M×T_cov), com T_cov ≪ T: da ordem de 100–200 mutantes × 2–5 testes × ~1 s cada, ou seja **minutos**. Rodar mutmut contra a suíte de 231 s levaria dias — inviável no Windows, e por isso mutation testing "completo" nunca aconteceria de fato.

*Camada 2 — os oito mutantes semânticos do §14 como testes dirigidos* (`tests/test_critical_mutants.py`), por fault-injection e monkeypatch. Cobrem os caminhos cross-language (bash ↔ Python, exit codes de hooks, bypass de gate por edição de arquivo) que nenhuma engine de mutação de Python alcança. O gate do plano é preservado ao pé da letra: **mutation score crítico = 100%**.

**Propriedade emergente.** A migração progressiva de bash para a lib (P-1.c em diante) expande a superfície mutável — a cobertura de mutação **cresce com a reforma**.

**O que se perde.** Descoberta de pontos fracos desconhecidos fora de `harness_lib/`. Impacto médio, mitigado pelos property-based tests da Onda 4.

**Critério de reabertura.** Após a Onda 6, experimento limitado com mutmut apenas sobre `harness_lib/` — barato e focado, para calibrar a engine própria contra uma referência externa.

---

### D4 — Retrieval híbrido exato com PPR (substitui HNSW; reabilita PPR)

**O que o plano pede.** FTS5/BM25 para memória textual, índices estruturados, busca híbrida com filtros, Personalized PageRank local no subgrafo, embeddings/HNSW *apenas se o benchmark justificar*, GraphBLAS *apenas se o tamanho do grafo justificar* (§12). A fórmula de ranking: `Score = w_t·Text + w_g·Graph + w_c·Community + w_e·Evidence − w_h·HubPenalty − w_s·Staleness`.

**O que será construído.**

- **FTS5/BM25 nativo do SQLite** para geração de candidatos (top-50) sobre eventos, artefatos, títulos de finding, headings de spec e memória textual.
- **Re-rank por cosine exato** sobre os candidatos, com embeddings f16 cacheados — o padrão já provado em `build_skills_index.py`. Brute-force sobre centenas ou poucos milhares de itens é da ordem de milissegundos.
- **Fusão por RRF** (Reciprocal Rank Fusion, `Σ 1/(k + rank)`): combina os rankings de BM25 e de cosine sem precisar calibrar escalas de score entre si. Robusto e trivial em stdlib.
- **Personalized PageRank reabilitado** — power iteration em dict-of-dicts sobre o grafo real: 775 nós, 1.041 arestas, ~50 iterações, sub-milissegundo. Seeds são os nós casados por FTS5 e por embedding. Isso viabiliza a fórmula completa do §12, com todos os termos computáveis em stdlib e os pesos registrados antes do benchmark, conforme a regra do plano.

**Por que HNSW seria pior aqui.** HNSW é uma aproximação: troca recall por velocidade em corpora de centenas de milhares a milhões de vetores. Nesta escala, o exato já é instantâneo, então a aproximação **só poderia perder recall**. Além disso, o gargalo medido do router não é o produto escalar — é a chamada de embedding ao Ollama (Camada B p95 ~1,4–1,5 s contra ~500 ms da Camada A).

**Único corte real: GraphBLAS.** Álgebra linear esparsa sobre semirings paga em grafos de milhões de arestas. Com 1.041 arestas, o overhead de montar a matriz supera o cálculo inteiro.

**Critério de reabertura.** HNSW: corpus acima de ~50k itens indexados, ou p95 do re-rank exato acima do orçamento definido no BASELINE. GraphBLAS: grafo acima de ~100k arestas.

---

### D5 — Rigor estatístico transversal (reforço, não substituição)

O plano exige registro de variância (§5), intervalo/variância por otimização (§17) e calibração por faixa de confiança (§14), sem prescrever método. Isto endurece esses pontos:

- **Bootstrap de intervalos de confiança** (stdlib `random`) e **teste de Mann-Whitney U** (~30 L em stdlib) para toda decisão de aceitar ou rejeitar uma otimização da Fase 12. Nada é promovido por diferença dentro do ruído.
- **Calibração real dos reviewers** na Fase 9: **Brier score** e reliability diagram por revisor × dimensão; **isotonic regression via PAV** (Pool Adjacent Violators, ~40 L stdlib) mapeando confidence bruta → posterior calibrado; posterior **Beta-Binomial** com suavização de Laplace e intervalos de credibilidade para revisores com poucas observações.

Isso dá conteúdo matemático ao campo `posterior` do §14, que hoje é um threshold fixo de 0,5 — e que, pior, **descarta** os dados que permitiriam calibrá-lo (risco R9). O log de confidence bruta começa na Onda 1 justamente para que a Onda 5 tenha histórico.

---

## Consequências

**Positivas.** Nenhum gate de segurança do plano é afrouxado — o que muda é a ferramenta que prova o gate. Em dois casos (D1 twin-execution, D4 exato sobre aproximado) o substituto é tecnicamente superior na escala real. Toda a stack permanece stdlib + bash + SQLite; a única dependência nova em todo o programa é `hypothesis` (dev-only, Onda 4), que entra como complemento do D1.

**Negativas.** Quatro componentes de engenharia não-trivial passam a ser código próprio a manter (~800 L somados). Mitigado por: cada um é auto-contido, testável isoladamente, e opera sobre artefatos que a reforma já produz (`fsm.json`, tabela `events`, `harness_lib/`). O risco de bug no verificador é real e é mitigado pela camada 2 do D3 — os testes dirigidos não dependem da engine.

**Registro de honestidade.** Estes substitutos são adequados **para esta escala**. Os critérios de reabertura acima são as condições objetivas sob as quais deixam de ser — e devem ser reavaliados a cada onda, não apenas no fim.

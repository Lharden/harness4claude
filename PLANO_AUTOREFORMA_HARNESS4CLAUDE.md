---
title: Plano de Autorreforma Segura e Otimização Máxima — Harness4Claude
system: Harness4Claude
document_type: execution-plan
status: ready-for-execution
version: 1.0
created: 2026-07-23
owner: Harness4Claude
comparison_peer: Harness4Codex
priority: safety-first-performance
---

# Plano de Autorreforma Segura e Otimização Máxima — Harness4Claude

## 0. Mandato

Este documento deve ser executado pelo próprio **Harness4Claude**, no repositório que contém seu código, hooks, skills, workflows e tooling. O objetivo é reformar o sistema sem comprometer a operação atual, elevando simultaneamente:

1. confiabilidade;
2. isolamento entre sessões;
3. consistência transacional;
4. velocidade dos hooks;
5. eficiência de contexto e tokens;
6. precisão de classificação;
7. qualidade da verificação;
8. segurança operacional;
9. capacidade de auditoria;
10. capacidade de comparação objetiva com o Harness4Codex.

A reforma não deve ser tratada como uma reescrita integral. Ela deve ocorrer por **migração incremental, reversível, mensurável e protegida por gates**.

O Harness4Claude não pode declarar sucesso apenas porque a nova arquitetura “parece melhor”. Cada ganho deve ser demonstrado por evidência reproduzível, e nenhuma melhoria de desempenho será aceita quando acompanhada de regressão em segurança, corretude, isolamento ou recuperabilidade.

---

# 1. Estado atual que deve ser preservado

O sistema atual possui capacidades relevantes que não devem ser descartadas durante a reforma:

- classificação rápida em hook;
- confirmação semântica pelo agente;
- pipelines L0, L1 e L2;
- classificação por tipo;
- artefatos SDD;
- gates humanos;
- verificação especializada;
- revisão paralela em múltiplas dimensões;
- adjudicação adversarial;
- telemetria e sinais;
- snapshots no `PreCompact`;
- integração com vault;
- tooling de migração, doctor, manutenção, sync, Graphify e Context7;
- bloqueios Git;
- circuit breakers;
- registro idempotente por `task_id`;
- validação `--expect-task` no fechamento.

A reforma deve considerar como riscos arquiteturais prioritários:

1. o estado atual é singleton por máquina;
2. duas janelas podem competir pela identidade lógica de `state.json`;
3. transições instrucionais feitas pelo agente podem escapar do mutex;
4. locks por PID e idade não impedem completamente um processo antigo de voltar e escrever;
5. o limiar da revisão multimodelo ainda não é demonstrado como calibrado;
6. o Graphify é operacional, mas seu uso ainda precisa produzir evidência mensurável;
7. traces e memória são úteis, mas ainda não formam um mecanismo unificado de conformance e aprendizado.

---

# 2. Princípio de otimização

A otimização deve ser **lexicográfica**, não apenas uma soma ponderada.

## 2.1 Ordem obrigatória

1. **Segurança e invariantes**
2. **Corretude funcional**
3. **Recuperabilidade e rollback**
4. **Precisão da coordenação**
5. **Latência**
6. **Throughput**
7. **Uso de tokens e contexto**
8. **Uso de CPU, memória e disco**
9. **Complexidade de manutenção**

Uma versão mais rápida que viole um invariante é automaticamente rejeitada.

## 2.2 Função de comparação

Depois que todos os hard constraints forem satisfeitos, calcular:

\[
J =
w_l L_n +
w_t T_n +
w_c C_n +
w_m M_n +
w_r R_n
\]

onde:

- \(L_n\): latência normalizada;
- \(T_n\): tokens/contexto normalizados;
- \(C_n\): custo computacional normalizado;
- \(M_n\): memória/disco normalizados;
- \(R_n\): retrabalho normalizado.

O objetivo é minimizar \(J\), mas somente dentro da região em que:

\[
Safety = 1,\quad Correctness = 1,\quad Recoverability = 1
\]

Os pesos devem ser registrados antes do benchmark final. Não alterar pesos depois de ver os resultados.

---

# 3. Regras não negociáveis de execução

## 3.1 Isolamento da reforma

Antes de qualquer alteração:

- criar branch dedicada;
- criar worktree dedicado;
- registrar commit-base;
- criar tag local de recuperação;
- copiar configurações atuais para diretório de backup;
- exportar estado, traces, summaries, signals e configurações;
- executar health-check existente;
- verificar que o repositório está limpo;
- registrar versões de Python, shell, Git, Claude Code, Graphify e dependências;
- não modificar configuração global ativa antes da fase de canário.

Estrutura sugerida:

```text
worktrees/
  harness4claude-self-reform/

docs/self-reform/claude/
  BASELINE.md
  INVENTORY.md
  RISK_REGISTER.md
  ADR/
  TEST_MATRIX.md
  MIGRATION_LOG.md
  PERFORMANCE_REPORT.md
  VALIDATION_REPORT.md
  ROLLBACK_REPORT.md
  CROSS_COMPARISON.md
```

## 3.2 Proibições

Durante a reforma:

- não executar `git push --force`;
- não executar `git reset --hard`;
- não executar `git clean -f`;
- não apagar o store atual antes da migração validada;
- não modificar diretamente o Harness4Codex;
- não desabilitar guardrails para “facilitar testes”;
- não substituir todos os componentes em uma única mudança;
- não fazer upgrade simultâneo de diversas dependências críticas;
- não ativar uma nova política em modo bloqueante antes de modo observação;
- não interpretar melhoria local como prova sistêmica;
- não remover compatibilidade antes de provar a migração e o rollback.

## 3.3 Stop conditions

Interromper imediatamente a fase atual e executar rollback parcial quando ocorrer qualquer um dos eventos:

- perda ou corrupção de estado;
- task de uma sessão visível em outra;
- conclusão sem evidência exigida;
- bypass de gate humano;
- comando Git destrutivo não bloqueado;
- lock antigo conseguindo escrever depois de perder propriedade;
- regressão de testes existentes;
- discrepância não explicada entre store legado e store novo;
- aumento significativo de latência sem ganho funcional demonstrado;
- alteração inesperada em repositório fora do worktree;
- Graphify incluindo arquivos que deveriam estar ignorados;
- erro de migração sem mecanismo determinístico de recuperação.

---

# 4. Artefatos obrigatórios antes de escrever código

O agente deve produzir os seguintes artefatos antes da primeira alteração estrutural:

## 4.1 `INVENTORY.md`

Deve conter:

- árvore relevante do repositório;
- hooks instalados;
- scripts invocados por hook;
- arquivos de estado;
- arquivos de configuração;
- locais de escrita;
- relações entre skills e workflows;
- paths de vault e AI-Brain;
- comandos de teste existentes;
- dependências externas;
- pontos de entrada;
- mecanismos de lock;
- funções que escrevem `state.json`;
- funções que encerram tarefas;
- funções que alteram classificação;
- funções que contam arquivos;
- funções de sync;
- funções de Graphify;
- funções de revisão multimodelo.

## 4.2 `BASELINE.md`

Registrar ao menos:

- p50, p95 e p99 de cada hook;
- tempo total de tarefas L0, L1 e L2 sintéticas;
- número de subprocessos por tarefa;
- leituras e escritas de disco;
- tamanho do store;
- tamanho dos traces;
- tokens estimados antes e depois de `graph-context`;
- número de arquivos abertos por tarefa;
- tempo de classificação;
- taxa de concordância regex–semântica;
- taxa de overrides humanos;
- taxa de Stop bloqueado;
- taxa de falsos blockers;
- número de findings confirmados;
- taxa de falhas no vault sync;
- comportamento com duas, quatro e oito sessões concorrentes;
- comportamento após kill durante escrita;
- comportamento com lock stale;
- comportamento com `state.json` corrompido.

## 4.3 `RISK_REGISTER.md`

Para cada risco:

```text
ID
descrição
causa
efeito
probabilidade
impacto
detecção
mitigação
rollback
owner
status
```

Riscos mínimos:

- perda de compatibilidade;
- duplicação de tasks;
- migração incompleta;
- deadlock;
- starvation;
- WAL crescendo sem checkpoint;
- regressão de hook;
- Graphify stale;
- política local maliciosa;
- falso positivo do shell guard;
- falso negativo do shell guard;
- reviewer calibration incorreta;
- early stopping prematuro;
- cache incoerente;
- vazamento entre projetos;
- inconsistência multi-máquina.

---

# 5. Fase 0 — Congelamento, reprodução e linha de base

## Objetivo

Criar uma base reproduzível e impedir que a autorreforma altere o objeto que está sendo medido durante a medição.

## Tarefas

- identificar todos os testes existentes;
- executar os testes três vezes em ambiente limpo;
- registrar variância;
- criar corpus sintético de prompts;
- criar replay de eventos de hooks;
- criar fixtures de states legados;
- criar fixtures de traces;
- criar dois repositórios mínimos e dois worktrees;
- criar simulador de sessões concorrentes;
- criar mecanismo para injetar falha entre `write`, `fsync` e `replace`;
- criar benchmark sem Graphify;
- criar benchmark com Graphify;
- registrar versões e hashes.

## Corpus mínimo

### Classificação

- L0 simples;
- L1 bug;
- L1 refactor;
- L2 architecture;
- L2 feature multiarquivo;
- prompt bilíngue;
- prompt com acentos;
- prompt com caixa diferente;
- prompt longo;
- prompt de automação conhecida;
- switch humano curto;
- tool output contendo palavras de classificação;
- prompt ambíguo.

### Concorrência

- duas sessões no mesmo `cwd`;
- duas sessões em `cwd` diferentes;
- duas sessões em worktrees diferentes;
- duas sessões fechando quase simultaneamente;
- uma sessão pausada após lock;
- lock declarado stale;
- processo antigo retornando;
- sync acontecendo durante compaction.

### Segurança

- comandos Git destrutivos em formatações variadas;
- comandos multiline;
- wrappers como `env`;
- `bash -c`;
- aliases simulados;
- interpolação;
- comandos seguros parecidos com destrutivos;
- comandos de leitura;
- push normal.

## Gate de saída

A fase só termina quando:

- todos os testes legados passam;
- benchmarks são repetíveis;
- fixtures foram salvas;
- o corpus cobre os principais invariantes;
- nenhum código estrutural novo foi ativado.

---

# 6. Fase 1 — Observabilidade antes da modificação

## Objetivo

Garantir que toda mudança futura possa ser explicada e revertida.

## Tarefas

Adicionar telemetria estruturada para:

- início e fim de cada hook;
- `session_id`;
- `cwd`;
- worktree;
- `task_id`;
- revision;
- owner epoch;
- lock wait;
- tempo de leitura;
- tempo de classificação;
- tempo de escrita;
- transição solicitada;
- transição aceita ou rejeitada;
- artefato exigido;
- artefato encontrado;
- Graphify HEAD;
- Graphify manifest hash;
- query executada;
- número de nós retornados;
- arquivos abertos depois da query;
- reviewer identity;
- reviewer decision;
- finding posterior;
- motivo de early stopping;
- motivo de gate;
- rollback acionado.

## Requisitos

- formato JSONL versionado;
- campos estáveis;
- redaction de segredos;
- limite de tamanho;
- rotação;
- escrita não bloqueante quando possível;
- falha de telemetria não pode corromper estado;
- cada evento deve ter monotonic sequence por task;
- timestamps de parede e monotônicos quando disponíveis.

## Gate de saída

- telemetria adicionada sem alteração de comportamento;
- overhead medido;
- overhead p95 deve permanecer pequeno e documentado;
- logs permitem reconstruir uma task completa;
- segredos sintéticos não aparecem nos logs.

---

# 7. Fase 2 — Identidade por escopo e eliminação do singleton lógico

## Objetivo

Eliminar a competição lógica entre janelas, preservando compatibilidade com o store atual durante a migração.

## Identidade proposta

```text
scope_id =
SHA256(
  session_id
  || "\n"
  || resolved_cwd
  || "\n"
  || git_worktree_root
  || "\n"
  || repository_identity
)
```

Armazenar hash integral no banco e prefixo legível em nomes de arquivos auxiliares.

## Estratégia de migração

### Etapa A — Shadow scope

- manter `state.json` legado como fonte ativa;
- calcular `scope_id` em paralelo;
- registrar qual state seria selecionado pelo novo mecanismo;
- não alterar decisão operacional;
- detectar colisões ou divergências.

### Etapa B — Dual read

- tentar ler state por escopo;
- se ausente, importar do legado;
- registrar origem;
- não apagar legado.

### Etapa C — Dual write

- escrever no novo store;
- escrever no legado por adaptador;
- comparar hashes normalizados;
- qualquer divergência bloqueia promoção da fase.

### Etapa D — New-primary

- novo store se torna fonte primária;
- legado permanece espelho e fallback;
- comparar por período de canário.

### Etapa E — Legacy read-only

- legado deixa de receber novas alterações;
- permanece disponível para rollback.

## Testes

- 10.000 combinações de `session_id`, `cwd` e worktree;
- duas sessões no mesmo repositório;
- duas sessões em repositórios diferentes com mesmo nome;
- ausência de `session_id`;
- symlink de `cwd`;
- paths com Unicode;
- Windows path case;
- worktree removido;
- repo sem Git.

## Gate de saída

- nenhuma interferência entre scopes;
- migração reversível;
- states legados importados corretamente;
- nenhuma task duplicada;
- performance não inferior aos limites definidos.

---

# 8. Fase 3 — Store transacional SQLite com WAL e fencing token

## Objetivo

Substituir read-modify-write de arquivos mutáveis por transações explícitas, sem remover imediatamente os arquivos legados.

## Schema mínimo

```sql
CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL,
  status TEXT NOT NULL,
  level TEXT,
  task_type TEXT,
  current_step TEXT,
  revision INTEGER NOT NULL,
  owner_epoch INTEGER NOT NULL,
  classification_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(scope_id, status) WHERE status = 'active'
);

CREATE TABLE events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(task_id, sequence)
);

CREATE TABLE artifacts (
  artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  path TEXT NOT NULL,
  content_hash TEXT,
  produced_by_step TEXT,
  verified_at TEXT,
  UNIQUE(task_id, artifact_type, path)
);

CREATE TABLE evidence (
  evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  command TEXT,
  exit_code INTEGER,
  output_hash TEXT,
  code_revision TEXT,
  source_event_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE leases (
  scope_id TEXT PRIMARY KEY,
  owner_token TEXT NOT NULL,
  owner_epoch INTEGER NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
```

## Regras transacionais

- `PRAGMA journal_mode=WAL`;
- busy timeout explícito;
- transações de escrita curtas;
- `BEGIN IMMEDIATE` somente em operações que realmente escreverão;
- checkpoint controlado;
- backup consistente;
- migration version table;
- foreign keys ativadas;
- schema versionado;
- nenhuma chamada externa dentro de transação;
- nenhuma execução de ferramenta enquanto o lock transacional estiver ativo.

## Fencing

Toda mutação exige:

```text
task_id
expected_revision
owner_epoch
transition
```

Atualização:

```sql
UPDATE tasks
SET current_step = :next_step,
    revision = revision + 1,
    updated_at = :now
WHERE task_id = :task_id
  AND revision = :expected_revision
  AND owner_epoch = :owner_epoch;
```

Zero linhas atualizadas significa conflito. O agente deve reler, revalidar e decidir; não sobrescrever.

## Crash tests

Injetar kill:

- antes do BEGIN;
- após BEGIN;
- após INSERT de evento;
- antes do UPDATE;
- depois do UPDATE;
- antes do COMMIT;
- depois do COMMIT;
- durante checkpoint;
- durante criação de backup.

## Gate de saída

- atomicidade demonstrada;
- nenhum lost update;
- nenhum stale owner escreve;
- recuperação após crash;
- backup restaurável;
- dual-write consistente;
- p95 de operação dentro do orçamento.

---

# 9. Fase 4 — API única de transição

## Objetivo

Impedir que o agente altere diretamente o estado interno.

## Comandos propostos

```text
harness task start
harness task inspect
harness task classify
harness task confirm-classification
harness task transition
harness artifact record
harness evidence record
harness gate request
harness gate approve
harness gate reject
harness task verify
harness task finish
harness task abandon
harness task rollback
```

## Regra principal

Nenhum skill, workflow ou agente pode editar diretamente:

- state;
- revision;
- current step;
- owner epoch;
- approvals;
- verification status;
- done status.

A escrita direta deve ser detectada em teste e, quando possível, impedida por permissões e organização de paths.

## Compatibilidade

Manter wrappers para comandos legados durante a migração.

## Gate de saída

- busca estática não encontra edições diretas do store;
- testes de integração demonstram que todas as transições passam pela API;
- wrappers legados produzem o mesmo resultado;
- nenhum gate humano pode ser forjado por edição de arquivo.

---

# 10. Fase 5 — Workflow formal e obrigações de artefatos

## Objetivo

Converter os pipelines L0/L1/L2 em máquinas de estado verificáveis.

## Modelo

Representar cada pipeline por:

```text
places
transitions
preconditions
produced artifacts
required evidence
human gates
timeouts
fallbacks
allowed retries
terminal states
```

## Exemplo L2

```yaml
pipeline: L2
initial: classified
terminal:
  - done
  - abandoned
  - rolled_back

transitions:
  - name: acquire_graph_context
    from: classified
    to: graph_context_ready
    requires:
      - graph_fresh_or_exception
    produces:
      - graph-context.json

  - name: produce_spec
    from: graph_context_ready
    to: spec_ready
    produces:
      - spec.md

  - name: approve_spec
    from: spec_ready
    to: spec_approved
    human_gate: true

  - name: produce_design
    from: spec_approved
    to: design_ready
    produces:
      - design.md

  - name: approve_plan
    from: design_ready
    to: implementation_allowed
    human_gate: true

  - name: verify
    from: implemented
    to: verified
    requires:
      - test_evidence
      - review_evidence
      - no_confirmed_blockers

  - name: finish
    from: verified
    to: done
    requires:
      - completion_report
```

## Validações

- nenhuma transição sem precondição;
- nenhuma conclusão sem artefato;
- retries limitados;
- gap closure limitado;
- tasks abandonadas preservam evidência;
- estado terminal é imutável, exceto operação explícita de reopen;
- human gates têm identidade, timestamp e escopo;
- artefatos têm hash e origem.

## Formalização

Criar especificação TLA+/PlusCal mínima para:

- duas sessões;
- duas tasks;
- aquisição e expiração de lease;
- revision CAS;
- approval;
- verify;
- finish;
- stale owner;
- crash e recovery.

Invariantes mínimos:

```text
NoCrossScope
NoPrematureDone
OnlyLeaseOwnerMutates
RevisionMonotonic
ApprovalCannotBeForged
VerifiedRequiresEvidence
TerminalStateStable
ArtifactBelongsToTask
```

## Gate de saída

- model checker não encontra contraexemplo no espaço definido;
- testes do runtime refletem os invariantes;
- workflow legado e workflow formal produzem resultados equivalentes no corpus.

---

# 11. Fase 6 — Graphify seguro, versionado e mensurável

## Objetivo

Transformar Graphify em infraestrutura de contexto com evidência, sem permitir que ele se torne um ponto único de falha.

## Upgrade

Tratar a versão atual como baseline e uma versão mais recente como candidata.

Procedimento:

1. instalar candidato em ambiente isolado;
2. gerar grafo em cópia do repositório;
3. comparar arquivos incluídos;
4. comparar nós;
5. comparar arestas;
6. comparar comunidades;
7. comparar tempos;
8. testar `.gitignore`;
9. testar `.graphifyignore`;
10. testar repositórios semântico e somente AST;
11. testar `query`, `path`, `explain` e update;
12. fixar a versão aprovada.

Não atualizar a instalação global antes do canário.

## Manifesto obrigatório

Cada uso deve registrar:

```json
{
  "repository_head": "...",
  "graph_head": "...",
  "graphify_version": "...",
  "manifest_hash": "...",
  "generated_at": "...",
  "layers": ["ast", "semantic"],
  "stale": false,
  "queries": [],
  "selected_nodes": [],
  "selected_communities": [],
  "candidate_files": []
}
```

## Cache

Chave:

```text
hash(
  repository_head
  + graph_manifest_hash
  + normalized_query
  + query_mode
  + retrieval_configuration
)
```

Invalidar quando qualquer componente mudar.

## Fallback

Se Graphify falhar:

- registrar erro;
- não bloquear tarefa L0;
- para L1, usar fallback textual;
- para L2, exigir decisão explícita: reconstruir, prosseguir com exceção registrada ou bloquear;
- nunca fingir que o grafo está fresco.

## Métricas de utilidade

- redução de arquivos abertos;
- redução de tokens;
- tempo adicional da query;
- precisão dos arquivos candidatos;
- percentual de candidatos realmente editados;
- percentual de dependências relevantes encontradas;
- incidência de contexto omitido;
- taxa de grafo stale;
- taxa de fallback.

## Gate de saída

- nenhum arquivo ignorado indevidamente indexado;
- nenhum segredo sintético no grafo;
- versão fixa;
- rollback testado;
- utilidade positiva no benchmark.

---

# 12. Fase 7 — Recuperação híbrida e memória

## Objetivo

Reduzir custo de busca sem substituir a estrutura gráfica por embeddings.

## Ordem de implementação

1. FTS5/BM25 para memória textual;
2. índices por task, scope, type e timestamp;
3. busca híbrida com filtros;
4. Personalized PageRank local no subgrafo;
5. embeddings/HNSW apenas se benchmark justificar;
6. GraphBLAS apenas se o tamanho do grafo justificar.

## FTS5

Migrar consultas `LIKE` ou buscas lineares para:

```text
event
text
metadata
artifact names
finding titles
spec headings
```

Adicionar filtros estruturados fora do índice textual.

## Ranking

\[
Score(v|q)=
w_t Text(v,q)
+w_g Graph(v,q)
+w_c Community(v,q)
+w_e Evidence(v)
-w_h HubPenalty(v)
-w_s Staleness(v)
\]

Registrar pesos e não ajustá-los silenciosamente durante o benchmark final.

## Limites

- embeddings não decidem segurança;
- similaridade não prova dependência;
- grafo estrutural é árbitro de relações de código;
- memória antiga recebe penalidade temporal;
- resultados devem carregar proveniência.

## Gate de saída

- busca textual superior ao baseline em corpus relevante;
- sem perda de recall crítica;
- tokens reduzidos;
- cache coerente;
- resultados explicáveis.

---

# 13. Fase 8 — Shell AST e policy-as-code

## Objetivo

Reduzir falsos negativos e falsos positivos do guard Git.

## Estratégia gradual

### Etapa A — Observe

- parsear comandos com AST;
- manter regex como decisão ativa;
- comparar decisões;
- registrar divergências.

### Etapa B — Dual decision

- bloquear quando ambos concordarem;
- divergências geram aviso e revisão;
- corpus adversarial obrigatório.

### Etapa C — AST primary

- AST decide;
- regex permanece fallback;
- políticas são avaliadas sobre representação estruturada.

## Representação do comando

```json
{
  "program": "git",
  "subcommand": "push",
  "flags": ["--force-with-lease"],
  "args": [],
  "wrappers": ["env"],
  "shell": "bash",
  "confidence": 1.0
}
```

## Política

Classes:

```text
allow
warn
require_approval
deny
unknown
```

Política global não pode ser enfraquecida por `WORKFLOW.md`.

## Testes

- multiline;
- escaping;
- wrappers;
- shell nested;
- strings;
- substitutions;
- comandos seguros;
- comandos parcialmente analisáveis;
- Windows shells, quando aplicável.

## Gate de saída

- zero bypass no corpus obrigatório;
- falsos positivos abaixo do limite definido;
- unknown falha de forma segura;
- política testada;
- rollback para regex disponível.

---

# 14. Fase 9 — Verificação multimodelo calibrada

## Objetivo

Manter a força da revisão em múltiplas dimensões, reduzindo custo e melhorando calibração.

## Evidência estruturada por finding

```json
{
  "finding_id": "...",
  "reviewer": "...",
  "dimension": "security",
  "file": "...",
  "symbol": "...",
  "line_range": "...",
  "claim": "...",
  "evidence": ["..."],
  "severity": "high",
  "confidence_raw": 0.82,
  "reproduced": true,
  "adjudication": "...",
  "posterior": 0.93
}
```

## Calibração

Manter histórico por revisor e dimensão:

- true positive;
- false positive;
- false negative conhecido;
- precisão;
- recall estimado;
- taxa de abstention;
- calibração por faixa de confiança.

Não usar confiança autodeclarada como único peso.

## Early stopping

Permitido somente quando:

- revisores independentes concordam;
- evidência é concreta;
- teste reproduz;
- nenhum reviewer de dimensão obrigatória está ausente;
- posterior ultrapassa limiar pré-definido;
- regra foi validada em replay offline.

## Regras de segurança

- security review não pode ser removido em L2;
- early stopping nunca reduz cobertura obrigatória;
- blockers críticos exigem confirmação;
- discordância relevante chama adjudicação;
- limiares ficam versionados.

## Mutation testing

Criar mutantes do harness:

- inverter exit code;
- ignorar owner epoch;
- aceitar DONE sem artefato;
- permitir bypass de gate;
- deixar state singleton;
- não invalidar cache;
- liberar comando destrutivo;
- não verificar graph HEAD.

A suíte deve matar os mutantes críticos.

## Gate de saída

- melhoria de custo sem aumento de escaped defects;
- findings continuam reproduzíveis;
- limiares calibrados;
- mutation score crítico de 100%;
- relatório de falsos positivos.

---

# 15. Fase 10 — Property-based, metamorphic e concorrência

## Propriedades obrigatórias

```text
mesmo evento repetido não duplica efeito
revision nunca diminui
scope diferente não compartilha state
task terminal não muda silenciosamente
approval pertence a task e scope
owner antigo não escreve
artefato de outra task não satisfaz gate
Graphify stale nunca é marcado fresh
falha de vault não interrompe state
telemetria falhando não altera decisão
```

## Relações metamórficas

### Classificação

- adicionar acentos não altera classe;
- mudar caixa não altera classe;
- whitespace irrelevante não altera classe;
- conteúdo de ferramenta não altera intenção humana;
- tradução equivalente não reduz nível de risco;
- adicionar descrição de arquitetura não transforma bug crítico em tarefa simples.

### Graphify

- comentário comum não muda estrutura AST;
- arquivo ignorado não altera grafo;
- atualização incremental e rebuild total produzem estrutura equivalente;
- rename completo preserva conectividade esperada.

### Estado

- reprocessar replay produz o mesmo estado;
- ordem de eventos independentes entre scopes não altera resultados;
- crash antes do commit equivale a não aplicar a transição;
- crash depois do commit equivale a aplicar uma vez.

## Stress

- 2, 4, 8, 16 workers;
- 10.000 transições;
- bursts;
- kill aleatório;
- disk full simulado;
- permission errors;
- WAL checkpoint;
- backup concorrente.

## Gate de saída

- nenhum invariante violado;
- resultados reproduzíveis;
- sem perda de evento;
- p99 documentado.

---

# 16. Fase 11 — Process mining e conformance

## Objetivo

Comparar o fluxo declarado com o fluxo efetivamente executado.

## Event log mínimo

```text
case_id = task_id
activity
timestamp
scope
pipeline
step_from
step_to
actor
result
artifact
evidence
duration
```

## Métricas

- fitness;
- precisão do modelo;
- fases puladas;
- loops;
- rework;
- retries;
- tempo por fase;
- waiting em gate humano;
- taxa de rollback;
- divergência entre classificação sugerida e confirmada;
- Graphify hit rate;
- context compression;
- findings por custo;
- falsos blockers;
- escaped defects.

## Uso

- detectar passos mortos;
- detectar regras excessivamente permissivas;
- detectar gargalos;
- propor alterações;
- nunca alterar política automaticamente sem revisão.

---

# 17. Fase 12 — Otimização de hot paths

Somente iniciar depois que fases de integridade estiverem concluídas.

## Candidatos

- evitar spawn de Python por hook quando possível;
- processo persistente somente se isolamento e lifecycle forem comprovados;
- cache de configuração por hash;
- cache de WORKFLOW;
- prepared statements;
- batch de eventos;
- reduzir fsync redundante sem reduzir durabilidade definida;
- lazy loading de memória;
- carregar somente comunidades Graphify relevantes;
- limitar tamanho do contexto injetado;
- usar resumos estruturados;
- paralelizar apenas operações independentes;
- early stopping calibrado;
- compactação incremental;
- checkpoint WAL controlado.

## Regra de benchmark

Cada otimização deve ter:

```text
hipótese
métrica
baseline
mudança
resultado
intervalo/variância
efeitos colaterais
decisão
```

Remover otimizações que complicam o sistema sem ganho mensurável.

---

# 18. Fase 13 — Canário e rollout

## Canário 1 — Shadow

- novo sistema observa;
- legado decide;
- nenhuma divergência pode ficar sem explicação.

## Canário 2 — Projetos de teste

- novo sistema decide em repositórios descartáveis;
- rollback automático disponível.

## Canário 3 — Baixo risco

- tarefas L0 selecionadas;
- monitorar hooks, state e Graphify.

## Canário 4 — L1

- bugs e refactors pequenos;
- comparar com baseline.

## Canário 5 — L2

- somente após aprovação explícita;
- gates humanos ativos;
- relatório completo.

## Rollout final

- feature flag;
- capacidade de voltar ao legado;
- documentação;
- migration doctor;
- backup automático;
- rollback drill concluído.

---

# 19. Protocolo de comparação com Harness4Codex

A comparação ocorrerá somente depois que ambos concluírem sua autorreforma e validação interna.

## 19.1 Restrições

- o Claude não modifica o Codex;
- o Codex não modifica o Claude;
- cada um trabalha em cópia ou checkout read-only do outro;
- benchmark idêntico;
- mesma máquina;
- mesma carga;
- mesmas repetições;
- caches frios e quentes separados;
- versões registradas;
- resultados brutos preservados;
- nenhum ajuste após ver o resultado do concorrente.

## 19.2 Corpus comum

1. classificação simples;
2. bug multiarquivo;
3. refactor;
4. arquitetura;
5. docs;
6. review;
7. duas sessões;
8. oito sessões;
9. crash;
10. stale lock;
11. Graphify stale;
12. Graphify rebuild;
13. comando Git adversarial;
14. artefato faltante;
15. teste com exit 0 sem cobertura relevante;
16. vault/memória indisponível;
17. prompt ambíguo;
18. prompt longo;
19. update incremental;
20. conclusão e rollback.

## 19.3 Métricas

### Hard constraints

- violações de invariantes;
- cross-scope contamination;
- lost updates;
- bypass de gate;
- bypass de Git guard;
- conclusão prematura;
- corrupção;
- rollback inválido.

Qualquer ocorrência reprova a versão.

### Performance

- p50/p95/p99 de hooks;
- throughput;
- CPU;
- memória;
- disco;
- tempo de startup;
- tempo de classificação;
- tempo de Graphify;
- arquivos abertos;
- tokens/contexto;
- tempo de verificação;
- custo da revisão;
- tempo de recovery.

### Qualidade

- precisão da classificação;
- underclassification;
- overclassification;
- cobertura de artefatos;
- findings verdadeiros;
- falsos blockers;
- escaped defects;
- explicabilidade;
- conformance.

## 19.4 Resultado

Produzir:

```text
CROSS_COMPARISON.md
cross-comparison-results.json
raw-benchmarks/
```

O relatório deve declarar vencedor por dimensão. Não forçar um vencedor global quando houver trade-offs.

## 19.5 Transferência de melhorias

Depois da comparação:

- identificar técnicas claramente superiores;
- avaliar compatibilidade arquitetural;
- criar proposta, não copiar diretamente;
- testar em branch separada;
- repetir gates;
- registrar ADR.

---

# 20. Critérios finais de aceitação do Harness4Claude

A autorreforma só pode ser declarada concluída quando:

- [ ] estado singleton deixou de ser risco lógico;
- [ ] cada sessão/worktree possui escopo correto;
- [ ] store transacional está validado;
- [ ] stale owner não consegue escrever;
- [ ] todas as transições passam pela API;
- [ ] workflows validam artefatos e evidências;
- [ ] gates humanos não podem ser forjados;
- [ ] Graphify está versionado, medido e reversível;
- [ ] arquivos ignorados permanecem ignorados;
- [ ] memória textual usa índice adequado;
- [ ] shell guard foi validado contra corpus adversarial;
- [ ] revisão multimodelo está calibrada;
- [ ] mutation testing mata mutantes críticos;
- [ ] property-based e metamorphic tests passam;
- [ ] model checking não encontra violação no modelo adotado;
- [ ] process mining demonstra conformance aceitável;
- [ ] benchmark final não mostra regressão proibida;
- [ ] rollback completo foi executado com sucesso;
- [ ] comparação com Harness4Codex foi concluída;
- [ ] documentação operacional foi atualizada;
- [ ] versão final está pinada e reproduzível.

---

# 21. Formato obrigatório do relatório final

```markdown
# Relatório Final — Harness4Claude

## Versão inicial
## Versão final
## Commit-base
## Commit-final
## Mudanças implementadas
## Mudanças rejeitadas
## Invariantes
## Migração
## Testes
## Model checking
## Segurança
## Graphify
## Memória
## Verificação multimodelo
## Performance
## Rollback
## Limitações
## Comparação com Harness4Codex
## Próximas propostas
## Decisão de rollout
```

Nenhuma afirmação de sucesso deve aparecer sem referência ao teste, benchmark ou artefato correspondente.

---

# 22. Ordem resumida de execução

```text
inventário
→ baseline
→ observabilidade
→ scope por sessão/worktree
→ SQLite shadow
→ dual-write
→ API de transição
→ workflow formal
→ Graphify mensurável
→ FTS5/ranking
→ shell AST/policy
→ verificação calibrada
→ testes formais e adversariais
→ otimização de hot path
→ canário
→ rollback drill
→ comparação com Codex
→ rollout
```

A ordem não deve ser invertida para buscar ganhos prematuros de velocidade.

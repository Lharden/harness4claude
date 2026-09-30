# `tasks.status` escrito por quem não lê `gates` — diagnóstico

Ramo `claude/keen-wu-692338` (worktree `exciting-bhabha-3659cc`), pipeline
L2-bug (regex sugeriu L2-docs; confirmação semântica corrigiu), task
`t-20260930-135532666501`. Base: `main` em `18ec682`, que já contém o conserto
de `transition` (ramo `claude/exciting-allen-825ca2`) e o do `escalation`
(ramo `claude/exciting-bhabha-3659cc`).

Origem: `docs/specs/transition-ignora-portao-diagnostico.md`, seção "Fora do
escopo", item 1.

## Reprodução

Contra `18ec682`, num banco novo, pelos métodos de `HarnessDatabase`. Os três
primeiros casos são os do relato de origem; os outros cinco apareceram ao
listar **todos** os escritores de `status`, não só os três citados.

| # | Cenário | Resultado |
|---|---|---|
| 1 | `open_gate(escalation)`, depois `confirm_classification` para L2-bug | `active`, fase `systematic-debugging`, `pending_gate=escalation` |
| 2 | `open_gate(escalation)`, depois `reclassify` para L1-feature | `active`, fase `write-spec-light`, `pending_gate=escalation` |
| 3 | pipeline `write-spec-light → approve-spec → tdd`; `transition` para `approve-spec`; `create_branch(b-1, explicito=True)`; `resolve_gate(approve-spec, approve)` | `active`, fase `tdd`, `pending_gate=branch-open:b-1` |
| 4 | mesmo pipeline parado em `approve-spec`; `confirm_classification` (mesmo pipeline) | `active`, fase `write-spec-light`, `pending_gate=approve-spec` — e ao voltar a `approve-spec`, `transition` **recusa** com "Nenhum comando resolve `approve-spec` nesta fase". **Deadlock**: o portão órfão bloqueia a entrada na própria fase que o resolveria |
| 5 | `open_gate(escalation)`, depois `confirm_classification` para L0-question | `done`, `pending_gate=escalation` |
| 6 | pipeline `[tdd]`; `create_branch(b-1)`; evidência válida; `complete` | `done` com `branch-open:b-1` pendente. Nova task `t-2` no escopo; `resolve_branch_decision(b-1, park)` → **`IntegrityError: UNIQUE constraint failed: tasks.scope_id`** (tenta pôr `t-1` em `active` ao lado de `t-2`) |
| 7 | task `done`; `open_gate(escalation)` | `awaiting_gate` — task terminal ressuscitada |
| 8 | `open_gate(escalation)`; evidência válida; `complete` | `done` com `escalation` pendente — o `complete` passou por cima da decisão humana |

E `transition` sobre task terminal (não usa portão, mas escreve `status`):
em `abandoned` sem outra task viva → `active`, fase `tdd`; com outra viva →
`IntegrityError` igual ao do caso 6.

Reproduz sempre; não depende de hook, de balde nem de tempo. Scripts do repro
ficaram fora do repositório; os testes da fase TDD são a forma permanente.

## Incidência real

Leitura `mode=ro` dos 200 `harness.db` sob `~/.claude/harness` em 2026-09-30:

| Estado | Portões | Tasks | Caminho que produziu |
|---|---|---|---|
| `done` + `escalation` pendente | 3 | 3 | `complete` com escalation aberto (caso 8): fase final, `verified=1` |
| `done` + `escalation` pendente, `pipeline=[]` | 1 | 1 | `confirm_classification` para L0 (caso 5): última transição `code-review → None` |
| `done` + `escalation` pendente, `verified=0` | 1 | 1 | não reconstruído (dado de 2026-09-02, anterior ao `complete` atual) |
| `done` + `approve-spec` **e** `approve-plan` pendentes | 10 | 5 | `transition` antigo pulando a fase-portão (conserto já em `main`) **e** `complete` sem ler portões |
| L0 `active` com `pipeline=[]` | — | 1 | `create_branch` numa task L0 `done` → `awaiting_gate` → `resolve_branch_decision(approve)` → `active` (science-harness, `t-20260921-171419056379`) |

Nenhuma task viva estava em `active` com portão pendente no momento da
leitura: esse estado dura até o próximo escritor que o sobrescreva, e o dano
dele é o que ele causa enquanto dura (abaixo).

## Causa raiz

`tasks.status` guarda dois fatos numa coluna só:

1. **ciclo de vida** — viva (`active`/`awaiting_gate`) ou terminal
   (`done`/`abandoned`/`superseded`);
2. **para uma task viva, se há portão pendente em `gates`** — `awaiting_gate`
   se há, `active` se não.

O segundo fato é cópia denormalizada da tabela `gates`. Dezessete caminhos de
`HarnessDatabase` escrevem a coluna, e cada um calcula o valor a partir da
própria visão parcial. Nenhum lugar deriva o valor dos dois fatos juntos:

| Escritor | Linha | Lê `gates` antes de escrever? | Preserva terminal? |
|---|---|---|---|
| `start_task` (task nova) | `scripts/transactional_state.py:364` | n/a — task nova não tem portão | n/a |
| `start_task` (tasks anteriores) | `:383` | cancela os pendentes | escreve terminal |
| `confirm_classification` | `:466` | **não** | fixa `active`/`done` pelo pipeline (intencional: L0 `done` → L1 `active`) |
| `expire_stale_task` | `:582` | cancela os pendentes | escreve terminal |
| `abandon_task` | `:618` | cancela os pendentes | recusa terminal |
| `create_branch` | `:789` | insere o portão, escreve `awaiting_gate` literal | **não** |
| `request_branch_approval` | `:831` | idem | **não** |
| `resolve_branch_decision` | `:868` | sim (`still_pending`) | **não** — caso 6 |
| `reclassify` | `:931` | **não** | fixa pelo pipeline |
| `open_gate` | `:964` | insere o portão, escreve `awaiting_gate` literal | **não** — caso 7 |
| `transition` | `:1009` | recusa com pendente (conserto de `main`) | **não** — `abandoned` → `active` |
| `resolve_gate` (fase-portão) | `:1058` | **não** — `active` literal | **não** |
| `_resolve_escalation` | `:1093` | sim (`still_pending`) | **não** |
| `touch_files` | `:1250` | não (`verified` → `active`) | retorna antes, em terminal |
| `record_evidence` | `:1363` | não (`verified` → `active`) | sim |
| `register_stop_continuation` | `:1404` | insere `escalation`, escreve `awaiting_gate` | exige `active` antes |
| `complete` | `:1426` | **não** — `done` com portão pendente | n/a (escreve terminal) |

Duas famílias de erro saem da mesma ausência:

- **quem não lê `gates`** deixa `active` com portão pendente (casos 1–4) ou
  fecha a task por cima dele (casos 5, 6, 8);
- **quem lê `gates` mas não o ciclo de vida** ressuscita task terminal (casos
  6, 7, `transition` em `abandoned`) e, com outra task viva no escopo, estoura
  o índice `one_active_task_per_scope`.

`resolve_branch_decision` e `_resolve_escalation` foram listados na origem como
"já corretos" porque calculam `still_pending`. Estão certos na metade dos
portões e errados na metade do ciclo de vida: o caso 6 é o
`resolve_branch_decision` estourando.

## O que o estado incoerente quebra

| Consumidor | Lê | Efeito de `active` com portão pendente |
|---|---|---|
| Stop (`hooks/harness-transactional.py:1080`) | `status != "active"` → não bloqueia | bloqueia pedindo evidência enquanto a decisão humana está aberta; duas vezes depois, abre `escalation` por cima do portão que já esperava o usuário |
| `continuation_policy.continua` (`scripts/continuation_policy.py:70`) | `status in ACTIVE_STATUSES` e `pending_gate` | correto por acaso: olha `pending_gate` direto |
| `branch_state._sync_task`, `state_cli._sync`, `harness-reclassify.sh` | copiam `task["status"]` do banco | propagam a incoerência para `state.json` |
| `confirm_classification.py` (`:199`) | **não** copia `status` do banco: o `state.json` recebe o `status` de `apply_confirmation`, que decide só pelo nível | depois de consertado o banco, a projeção continuaria dizendo `active` com o banco em `awaiting_gate` — medido: hoje os dois coincidem só porque os dois estão errados |

## Chamadores (auditados antes de mudar a semântica)

| Chamador | Métodos | Observação |
|---|---|---|
| `scripts/confirm_classification.py:191` | `confirm_classification` | projeção de `status` vem de `apply_confirmation`, não do banco |
| `scripts/state_cli.py:135,164,166` | `confirm_classification`, `resolve_gate`, `complete` | `_sync` copia `status` do banco; `StateTransitionError` vira `erro:` + exit 2 |
| `hooks/harness-reclassify.sh:235` | `reclassify` | só promove L0 → L1 (`reclassification_policy.should_promote` exige `status != active`); copia `status` do banco |
| `scripts/branch_state.py:517,593,594,604,688` | `create_branch`, `request_branch_approval`, `approve_branch`, `resolve_branch_decision` | a task vem de `state.json` (`_projected_task`) ou do dono do ramo (varredura) — pode ser L0 `done` ou task já concluída |
| `hooks/harness-transactional.py` | `register_stop_continuation`, `record_evidence`, `touch_files` | nenhum chama os escritores com defeito diretamente |
| testes | `open_gate`, `complete`, `create_branch` e os demais | conferidos na fase TDD, um a um, contra a semântica nova |

## Contexto estrutural (graph-context)

`graphify update .` em 2026-09-30 sobre `18ec682` (o `graph.json` anterior era
de 2026-09-29, mais velho que o HEAD): 6257 nós, 9783 arestas. A consulta
"quem escreve `tasks.status` e quem chama os escritores" devolveu o mesmo
conjunto da auditoria manual acima, mais dois chamadores indiretos que só
escrevem estado terminal e já cancelam portões — `record_signal.main`
(`abandon_task`, `scripts/record_signal.py:230`) e
`expire_stale_pipeline.expire` (`expire_stale_task`,
`scripts/expire_stale_pipeline.py:171`). Todos os escritores moram numa
comunidade só (`HarnessDatabase`, `scripts/transactional_state.py:129`): o
helper não cruza fronteira de módulo. Testes que exercitam portão pendente e
que a fase TDD confere contra a semântica nova:
`tests/test_ciclo_de_vida_da_task.py` (evidência mantém `awaiting_gate`),
`tests/test_lifecycle_context.py:35` (`open_gate` em task viva),
`tests/test_confirm_classification.py:233`, `tests/test_transactional_branches.py`,
`tests/test_transition_portao_pendente.py`, `tests/test_portao_escalation.py`.

## Decisões (grill-me, 2026-09-30)

O `wf-grill` rodou sobre este documento em contexto limpo: 5 lentes vivas,
nenhuma morta, 44 perguntas (8 bloqueantes). As que o código responde foram
respondidas por ele; as quatro de desenho foram ao usuário, que escolheu a
opção recomendada nas quatro.

**Helper.** `HarnessDatabase._status_derivado(connection, task_id, ciclo)`,
chamado por todo escritor de `status`, dentro da mesma transação
`BEGIN IMMEDIATE` do escritor (`_write`), **depois** de ele mexer em `gates`.
`ciclo` é o ciclo de vida que o escritor pretende: terminal volta intacto;
qualquer valor vivo (`active`, `awaiting_gate`, o legado `verified`) vira
`awaiting_gate` se há portão pendente e `active` se não. Quem decide o ciclo
de vida é explícito: `confirm_classification` e `reclassify` passam
`active`/`done` pelo pipeline (é o que torna legítima a promoção L0 `done` →
L1); os demais passam o `status` atual da linha, e por isso nunca ressuscitam
terminal. `verified` é valor legado de `status` (migrado para `active` a cada
abertura, `:322`), não só a coluna. O índice `one_active_task_per_scope` cobre
os quatro status vivos. Nenhum escritor grava `suggested`.

- **D1 — reclassificação, por tipo.** Portão **de fase** (`approve-spec`,
  `approve-plan`, `answer-clarifications` — todo tipo fora de
  `PORTOES_SEM_FASE = {escalation, branch-open}`) é cancelado com
  `decision='reclassified'`: a reclassificação zera `phase_index`, e preservá-lo
  é o deadlock do caso 4; o pipeline novo reabre os portões dele ao entrar nas
  fases. `escalation` e `branch-open` são preservados e a task fica
  `awaiting_gate`. Tipos misturados: decide-se por linha.
- **D2 — fechar com portão pendente, recusar.** `complete` recusa com qualquer
  portão pendente, pela mesma `_recusa_por_portao` de `transition` (que imprime
  a linha que resolve e é executada pelo teste). Reclassificar para L0
  (pipeline vazio) com `escalation`/`branch-open` pendente também recusa, antes
  de qualquer escrita. `confirm_classification.py` já trata a exceção com
  `erro:` e exit 2 sem gravar `state.json`.
- **D3 — portão sobre task terminal: grava, status fica.** `open_gate`,
  `create_branch`, `request_branch_approval`, `resolve_branch_decision`,
  `_resolve_escalation` e `transition` passam o status atual ao helper, então
  terminal fica terminal. Some o `IntegrityError` do caso 6 e a L0 `active`
  fantasma. Invariante resultante: **task com pipeline em status terminal não
  tem portão pendente** (garantido por D2); task L0 (`pipeline=[]`, `done`)
  pode ter `branch-open` pendente, que é o ramo oferecido numa conversa L0.
  O caso 3 fica `awaiting_gate` na fase `tdd`: aprovar `approve-spec` é decisão
  explícita do usuário e avança; o `branch-open` é independente e segura a
  próxima `transition`.
- **Migração — em `_ensure_schema`**, no molde da `identity-migration`,
  idempotente, com `SELECT` antes para não pedir trava a cada abertura, e um
  evento por task tocada: (1) portão pendente de task terminal com pipeline →
  `cancelled`, `decision='terminal-migration'`; (2) task viva com `pipeline=[]`
  → `done`; (3) task viva com `status` em desacordo com `gates` → derivado.
  Alcança cada balde quando ele for aberto de novo; bancos de outras máquinas
  migram quando o código novo chegar a elas.

**Escopo fechado pelas respostas.** Entram os 17 caminhos da tabela (os que já
escrevem terminal e cancelam portões ficam como estão) e a projeção de
`scripts/confirm_classification.py`, que passa a copiar `status` do banco como
`_sync` e `_sync_task` já fazem. Fora: recusar a *operação* inteira sobre task
terminal (`transition`/`record_artifact` em `abandoned` avançam a fase e agora
mantêm `abandoned`) e o `phase_index` zerado em reconfirmação concordante.

### Perguntas originais (registro)

A causa comum pede um **helper único** que derive `status` dos dois fatos, e
todo escritor passa por ele. O que ele faz com a parte viva é mecânico
(`awaiting_gate` sse há portão pendente). Três pontos não eram mecânicos e
foram ao usuário no `grill-me`:

- **D1 — reclassificação com portão pendente** (`confirm_classification`,
  `reclassify`): cancelar ou preservar? O caso 4 mostra que preservar um portão
  **de fase** (`approve-spec`, `approve-plan`, `answer-clarifications`) numa
  reclassificação que zera `phase_index` é deadlock. `escalation` e
  `branch-open` não são fase (`_resolve_gate_aceita`, `:1105`) e resolvem em
  qualquer uma.
- **D2 — fechar a task com portão pendente** (`complete`; `confirm_classification`
  e `reclassify` para L0): recusar imprimindo a linha que resolve, como
  `transition` já faz, ou cancelar o portão?
- **D3 — escritor de portão sobre task terminal** (`open_gate`,
  `create_branch`, `request_branch_approval`, `resolve_branch_decision`,
  `_resolve_escalation`, `transition`): o helper nunca ressuscita terminal —
  e o dado já gravado (15 portões pendentes em tasks `done`, uma L0 `active`)
  é reparado por migração ou fica?

## Fora do escopo (achados de passagem)

1. `confirm_classification` zera `phase_index` mesmo quando `agreed=True` e o
   pipeline não muda: reconfirmar no meio do pipeline volta à fase 1. É
   escolha de semântica da confirmação, não de portão.
2. `transition` e `record_artifact` não recusam task terminal — avançam fase
   de task `abandoned`. O helper impede a ressurreição de `status`; recusar a
   operação inteira é outra regra (ciclo de vida), e fica para ela.

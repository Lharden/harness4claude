# Status próprio da task L0 — diagnóstico

Ramo `status-proprio-da-task-l0` (sessão `1efa70a4-87ed-4747-b758-b73c86bc2a42`),
aberto pela sessão `4af76e8b-35ee-45c9-b894-546c5da73e4c` depois do merge do
desfecho terminal (`dab7cb3`). Medido sobre `f28d4f0`, idêntico a `main` em `5670456`
nos arquivos citados (`scripts/`, `hooks/`, `contract/`, `schemas/`,
`tests/`). Só diagnóstico
e desenho: nenhum código mudou, e o caminho do classify continua congelado
enquanto o P1 do System One estiver vivo (ver "Dependência do P1").

## A pergunta

`start_task` grava `done` quando o pipeline é vazio
(`scripts/transactional_state.py:555`), então `done` diz duas coisas: "entregue"
e "nunca teve pipeline". O conserto do desfecho terminal precisou de uma exceção
por causa disso: `_desfecho_registrado` trata `done` com `pipeline_json = []`
como não-desfecho (`transactional_state.py:2138-2140`, decisão D3 e o
refinamento em `docs/specs/desfecho-terminal-diagnostico.md`). A L0 deve ter
status próprio para a regra ficar sem exceção? O que isso custa?

## O que a L0 é hoje

Três perguntas definem o ciclo de vida de uma linha em `tasks`. A L0 responde
diferente das outras duas classes nas três:

| | Segura o escopo? (`ACTIVE_STATUSES`, índice `one_active_task_per_scope`) | Acumula atividade? (toque, evidência, lançamento) | O status ainda muda? |
|---|---|---|---|
| viva (`active`, `awaiting_gate`) | sim | sim | sim |
| desfecho (`done` com pipeline, `abandoned`, `superseded`) | não | não | não (D1/D3) |
| **L0 (`done` com pipeline vazio)** | **não** | **não** | **sim**: promoção, correção, oferta de ramo |

A L0 já é um terceiro estado de ciclo de vida. Ele não tem nome: está
codificado no par (`status = 'done'`, `pipeline_json = '[]'`), e cada lugar
que precisa separá-lo do desfecho relê o pipeline.

O par é exato nos dados. Nos 227 `harness.db` do Claude, as 941 tasks com
pipeline vazio são as 941 tasks `tier = 'L0'`; no banco do Codex, 7 de 7. Não
existe L0 com pipeline nem pipeline vazio fora de L0. A exceção não erra hoje: o
problema dela é outro, e está em "Onde a ambiguidade vaza".

## Escritores do status da L0

| Onde | O que grava | Caminho |
|---|---|---|
| `hooks/harness-classify.sh:746` | projeção: `done` se `level == "L0"` | **congelado** (classify) |
| `transactional_state.py:555` `start_task` | banco: `done` se pipeline vazio | — |
| `transactional_state.py:671` `confirm_classification` | `done` para L0, `active` para L1/L2 | — |
| `scripts/confirm_classification.py:107,112` | projeção: `done` se o final começa com `L0`; com banco, `:201-204` sobrescreve com o status do banco, então as duas linhas só valem sem banco | **congelado** (decide L0/L1/L2) |
| `transactional_state.py:1150` `reclassify` | `active` na promoção L0→L1 (`done` se reclassificar para pipeline vazio) | chamado por `hooks/harness-reclassify.sh:257` |
| `transactional_state.py:516-521` `_migrar_status_derivado` | L0 viva → `done` | — |
| `transactional_state.py`: `create_branch` `:992`, `request_branch_approval` `:1035`, `resolve_branch_decision` `:1076`, `open_gate` `:1206`, `touch_files` `:1547`, `_gravar_evidencia` `:1715` | regravam o status atual por `_status_derivado`, que devolve `done` intacto (`transactional_state.py:2233`) | — |
| harness4codex `state_db.py:260, 359, 706` | `done` se pipeline vazio (start, confirmação, reclassificação) | outro repositório |

A L0 nasce `done` por duas portas: o classify (estimado em 587 tasks, as 941
L0 menos as 354 corrigidas) e a confirmação semântica que rebaixa L1/L2 para
L0 (354).

## Leitores, por pergunta

Linhas sem nome de arquivo, nesta seção, são de `scripts/transactional_state.py`.

**"Segura o escopo / continua?"** — leem `ACTIVE_STATUSES`, onde `done` não
está. A L0 fica de fora sem precisar de exceção:
`current_task` (`:733`), `ler_task_corrente` (`:2277`), `expire_stale_task`
(`:754`), o despejo de `start_task` (`:573-578`), `_recusa_por_desfecho`
(`:2161`), o índice (`:291`), `continuation_policy.continua`
(`scripts/continuation_policy.py:106`, que além disso exige pipeline), e
`mh/estado.py:72` no master-harness.

**"Acumula atividade?"** — leem `TERMINAL_STATUSES` direto, sem a exceção; a
L0 conta como encerrada, e isso é o desejado ("task L0 não acumula"):
`abandon_task` (`:811`, no-op), `touch_files` (`:1529`), `_gravar_evidencia`
(`:1703`), `registrar_lancamento` (`:1853`). Consequência medida em
`record_signal.py:100-110`: o Edit/Write de uma L0 só aparece no contador
`.session-files-count`, que é de onde a promoção conta os 3 arquivos.

**"O status ainda muda?"** — é a única pergunta que precisa da exceção:
`_desfecho_registrado` (`:2118-2140`), chamado por `_exige_task_viva` em
`complete`, `transition`, `resolve_gate`, `reclassify`,
`confirm_classification`, `open_gate` e `create_branch`. E uma segunda cópia
do mesmo predicado: `_migrar_status_derivado` só cancela portão pendente de
task terminal **com** pipeline (`:505`, `t.pipeline_json != '[]'`).

**`_status_derivado`** (`:2233`) — `ciclo in TERMINAL_STATUSES` devolve o ciclo
intacto. É o que impede um `branch-open` pendente de levar a L0 a
`awaiting_gate` e, portanto, a segurar o escopo. Funciona hoje porque `done`
está em `TERMINAL_STATUSES`.

**Projeção** (`state.json`): `should_promote`
(`scripts/reclassification_policy.py:30`, `status != "active"`), o portão de
Stop (`hooks/harness-transactional.py:2209`, `status != "active"`),
`expire_stale_pipeline.py:78`, `health-check.sh:357`. Nenhum compara com
`done`: todos perguntam por "viva".

**Telemetria**: `record_signal.py:192` grava `desfecho = status` do banco
(desde `f28d4f0`). Uma L0 registrada por ali sai com `desfecho: "done"`, igual
a uma entrega.

**Contrato**: o enum de `status` em `contract/schemas/task-state.schema.json:11`
e o teste `tests/test_ciclo_de_vida_da_task.py:689-695`, que exige todo valor de
`ACTIVE_STATUSES ∪ TERMINAL_STATUSES` no enum. A projeção tem o próprio enum,
em `schemas/state.schema.json:37`, validado por `scripts/migrate_state.py:259`.

## Medição

Leitura `mode=ro` de todo `harness.db` sob `~/.claude/harness` (227 bancos) e
de `~/.codex/harness/harness.db`, em 2026-10-01. Script:
`scratchpad/medir_l0.py` da sessão, não versionado.

| | Claude | Codex |
|---|---|---|
| tasks | 1 343 | 21 |
| pipeline vazio (todas `tier = 'L0'`) | 941 | 7 |
| … `done` | 932 | 7 |
| … `abandoned` | 8 | 0 |
| … `active` | 1 | 0 |
| `done` com pipeline (entrega de verdade) | 158 | 2 |
| **fração de `done` que é L0** | **932 / 1 090 = 85,5%** | 7 / 9 |
| sugerido L0 que saiu de L0 (promoção ou correção) | 59 | 1 |
| sugerido L1/L2 rebaixado para L0 pela confirmação | 354 | 0 |
| ramos cuja dona é L0 | 10 de 30 | 0 |

As 8 L0 `abandoned` e a L0 `active` são resíduo da ressurreição que o status
derivado consertou: ramo decidido numa L0 a levava a `active`, e o prompt
seguinte a despejava como `abandoned` (os 7 portões `branch-open` resolvidos em
L0 `abandoned` batem com isso). A `active` restante é
`science-harness-f34c6792 / t-20260921-171419056379`; a migração `l0-viva` a
leva a `done` quando o banco for reaberto pelo código novo (zero eventos
`migracao-l0-viva` gravados até agora).

As três saídas que a exceção protege são usadas: 59 promoções/correções a
partir de L0 e 10 ramos oferecidos em conversa L0. Tirar a exceção sem pôr nada
no lugar quebraria as três (é o que M2 mediu no desfecho terminal: 3 testes).

## Onde a ambiguidade vaza

1. **Quem lê `status` sozinho é enganado em 85,5% dos `done`.** Contar entrega
   exige filtrar `pipeline_json`: o diagnóstico do status derivado mediu "15
   portões pendentes em tasks `done` com pipeline", e esta medição separa as
   duas colunas pelo mesmo motivo. Instrumento que mente na maioria das linhas
   é a armadilha da memória "medir o instrumento antes do resultado".
2. **O predicado já tem duas cópias** (`_desfecho_registrado:2140` e
   `_migrar_status_derivado:505`), e a segunda deixou um resíduo: a L0
   `RSL_Project-aa99cfb0 / t-20260909-012148101851` está `done` com
   `escalation` pendente, e a migração não a repara, porque o filtro de
   pipeline a trata como "não terminal".
3. **Telemetria:** `desfecho` em `signals.json` registraria L0 como `done`.
   Hoje são 0 linhas (o campo nasceu em `f28d4f0`; as 23 L0 já registradas não
   o têm), então é vazamento futuro, não medido.
4. **Estado unificado do master-harness:** `mh/estado.py` prepara uma tabela
   `tasks` compartilhada pelos dois hosts. O leitor dali verá `done` sem saber
   que precisa olhar o pipeline.

## Opções

### A — status próprio para a L0

Um valor novo, `conversation` (E2, em "Decisões"), para "task sem pipeline". Fica **fora** de `ACTIVE_STATUSES` (não segura o escopo, não
continua) e **fora** de `TERMINAL_STATUSES` (não é desfecho). Dois conjuntos
nomeados substituem o predicado:

- `TERMINAL_STATUSES` volta a ser só desfecho, e `_desfecho_registrado` vira
  `status in TERMINAL_STATUSES`, **sem exceção**;
- um conjunto novo, "status fixo", = `TERMINAL_STATUSES ∪ {"conversation"}`,
  lido por `_status_derivado` e pelos 4 leitores de "acumula atividade?". Sem isso o
  `branch-open` levaria a L0 a `awaiting_gate` e ela passaria a segurar o
  escopo — a L0 `active` fantasma de volta.

### B — manter `done` e a exceção como estão

Zero custo. O par (`done`, `[]`) é exato nos dados. A ambiguidade continua para
quem lê `status` sozinho, e o predicado continua com duas cópias.

### C — `done` da L0 vira desfecho de verdade

Sem exceção e sem status novo: a L0 fecha ao nascer, e as três saídas passam a
abrir task nova — a promoção abre L1 nova, a correção da confirmação abre a
task do nível certo, a oferta de ramo precisa de dona viva. Quebra a
continuidade do `task_id` que a `harness-workflow` usa do início ao fim
(`--expect-task`), muda `harness-reclassify.sh` e o classify (congelados), e a
oferta de ramo numa conversa L0 fica sem dona: ou `create_branch` passa a
aceitar dona terminal (a exceção volta em outro lugar), ou o ramo L0 deixa de
existir (10 de 30 ramos medidos).

### D — derivar o ciclo de vida na leitura

A coluna fica como está; uma função `ciclo_de_vida(row)` devolve `viva`, `l0`
ou o desfecho, e todo leitor passa por ela (`_desfecho_registrado`,
`_status_derivado`, a migração, `record_signal`). Fica em
`transactional_state.py` e `record_signal.py`: sem contrato, sem migração, sem
caminho congelado. O valor gravado em disco continua dizendo `done` para quem
lê o banco sem a função — o leitor de SQL, o harness4codex e o estado
unificado do master-harness (vazamentos 1 e 4 continuam).

## Custo medido de cada opção

| | A — status próprio | B — manter | C — L0 fecha | D — derivar na leitura |
|---|---|---|---|---|
| Regra sem exceção | sim | não | sim | só dentro da função |
| `status` sozinho diz a verdade | sim | não (85,5% dos `done`) | sim | não |
| Código no harness4claude | 7 pontos de escrita em 3 arquivos (`transactional_state.py:520, 555, 671, 1150`, `harness-classify.sh:746`, `confirm_classification.py:107, 112`), 4 leitores de "acumula", `_status_derivado`, `_desfecho_registrado`, migração `transactional_state.py:505` | nada | classify, reclassify, `create_branch`, `harness-workflow` | 1 função + os leitores que hoje releem o pipeline (`transactional_state.py:2140, 505, 2233`, `record_signal.py:192`) |
| Caminho congelado | `harness-classify.sh:746` (a projeção do classify não recebe o status do banco: `:802-809` copia revisão e escopo, não `status`); `confirm_classification.py:107,112` só no caminho sem banco | não | sim, e mais | não |
| Dados | migrar 932 linhas em 227 bancos (Claude) e 7 (Codex); idempotente, mesmo molde de `migracao-l0-viva` | nada | nada | nada |
| Testes que fixam L0 = `done` | 13 asserções em 5 arquivos (abaixo); 5 fixtures continuam válidas como entrada legada | nada | as 13 e os 3 da metade 2b mudam de sentido | nada |
| Contrato com o harness4codex | enum em `task-state.schema.json` (canônica do master-harness + vizinha, dois locks), `contract_version` 1.2.0 → 1.3.0, `state.schema.json` da projeção; o harness4codex implementa nos 3 escritores dele | nada | nada no enum; o harness4codex muda o mesmo fluxo | nada |

Asserções que fixam L0 = `done` hoje (grep por `"done"` em `tests/`, 55
linhas em 12 arquivos, lidas uma a uma): `test_harness.py:299`,
`test_confirm_classification.py:116`, `test_sim_nao_fecha_entrega.py:230`,
`test_desfecho_terminal.py:352`, e `test_status_derivado_dos_portoes.py:296,
307, 316, 329, 334, 346, 356, 491, 493`. As fixtures de projeção L0 com
`"status": "done"` (`test_harness.py:1049, 1123, 1236`,
`test_posttooluse_stdout_channel.py:129`, `test_reclassification_policy.py:17`)
são entrada e continuam válidas: `should_promote` pergunta `!= "active"`.

O contrato com o harness4codex **não quebra em runtime** em nenhuma opção: os
dois hosts têm bancos separados, e o único evento que o harness4claude manda ao
spool do master-harness (`task.start`, `harness-classify.sh:856-865`; o
`harness-session-start.sh` só drena) não carrega `status`. O custo de A é de conformidade e de sincronia das árvores, e
encontra uma dívida que já existe: o harness4codex está em `contract_version`
1.1.0, sem `superseded` no enum (`harness4codex/_contract/schemas/task-state.schema.json:11`),
e o pacote `harness4contract` também; a canônica do master-harness e a vizinha
daqui estão em 1.2.0, com `superseded`. O harness4codex também não tem a regra
de desfecho terminal: só `ACTIVE_STATUSES` em `state_db.py:19`, e nenhuma
ocorrência de `TERMINAL`, `desfecho` ou `superseded` nos `.py` do pacote.

## Recomendação

**A, status próprio, executada depois do fim do P1 e junto da sincronia do
contrato que já está devida.** Aceita pelo usuário (E1).

- É a causa: a coluna guarda dois fatos, e a L0 é um terceiro ciclo de vida
  sem nome. O status derivado já firmou que `status` é a codificação do ciclo
  de vida (`_status_derivado`, "quem decide ciclo de vida passa o dele"); A
  completa essa codificação em vez de manter um segundo canal (o pipeline).
- Tem consumidores nomeados: `_desfecho_registrado`, `_status_derivado`, os 4
  leitores de "acumula", a migração (`transactional_state.py:505`, que hoje
  relê o pipeline), `record_signal` (`desfecho`) e o estado unificado do
  master-harness.
- B é o que vale **até** o P1 terminar: o predicado é exato (941/941, 7/7) e
  nenhum consumidor de `status` sozinho está quebrado hoje. Não é conserto, é
  a espera declarada.
- C troca a exceção por outra (`create_branch` em dona terminal) e quebra o
  `--expect-task` do pipeline.
- D conserta só o lado de dentro: o valor em disco continua mentindo para o
  leitor de SQL e para o outro host, que são justamente os vazamentos medidos.

Fazer D agora e A depois seria retrabalho: a função de D vira um `status in
CONJUNTO` em A.

## Dependência do P1

**Depende.** A precisa de `hooks/harness-classify.sh:746`, no caminho
congelado: a projeção que o classify grava não copia o `status` do banco
(`:802-809`). `scripts/confirm_classification.py:107,112` também é caminho
congelado, mas com banco a projeção dele já vem do banco (`:201-204`); as duas
linhas são o fallback sem banco. Mudar só o banco e deixar o classify gravando
`done` produz banco e projeção discordando a cada prompt L0: os leitores atuais
da projeção toleram (todos perguntam `!= "active"`), mas seria conserto pela
metade. A mudança espera o fim
do P1, confirmado com a sessão "Plano e implementação System One Models" antes
de qualquer código.

## Decisões

Tomadas pelo usuário em 2026-10-01, sobre este diagnóstico:

- **E1 — opção A, depois do fim do P1.** Até lá vale B, como espera
  declarada.
- **E2 — o status novo se chama `conversation`.** É o vocabulário que o código
  já usa para o caso ("oferta de ramo numa conversa L0", D2/D3 do desfecho
  terminal). Não colide com o `idle` da projeção, que quer dizer "não há task"
  (`harness-session-start.sh:66`).
- **E3 — a L0 não fecha quando o prompt seguinte abre outra task.** Fica
  `conversation`. Levá-la a `done` no despejo de `start_task` faria `done`
  voltar a misturar "entrega" com "conversa encerrada" — a ambiguidade que A
  existe para tirar. Promoção e correção seguem possíveis pelo id, como hoje.
- **E4 — o harness4codex adota junto**, subindo de 1.1.0 direto para 1.3.0
  (`superseded` incluído) e gravando `conversation` nos 3 escritores dele
  (`state_db.py:260, 359, 706`).

Decidido nesta fase, sem mudar comportamento: **`abandon_task` numa
`conversation` continua no-op**, como é hoje com `done`. `conversation` entra
no conjunto "status fixo" (que `abandon_task` passa a ler), e abandonar uma
conversa mudaria o desfecho gravado de task L0, fora do que este ramo decide.

## Fora do escopo (achados de passagem)

1. **O `escalation` órfão da L0** (`RSL_Project-aa99cfb0 /
   t-20260909-012148101851`): `_migrar_status_derivado:505` não o cancela por
   causa do filtro de pipeline. **A não o resolve sozinho**: `conversation`
   também não é terminal, e o passo 1 da migração só olha terminal. A execução
   de A tem de decidir o destino dele (cancelar, como `terminal-migration`, ou
   deixar). Hoje nenhum caminho de produção cria outro: `open_gate` não tem
   chamador fora dos testes, e a confirmação para L0 recusa com `escalation`
   pendente. Com B, fica pendente para sempre, sem leitor que o cobre.
2. **`schemas/state.schema.json:37`** (projeção) não tem `superseded` nem
   `verified`, e `migrate_state.py:259` valida a projeção contra ele.
3. **`rolled_back`** está nos enums do contrato e nenhum código de `scripts/`
   ou `hooks/` o grava.
4. **Drift de contrato entre as quatro árvores**, já descrito acima: 1.2.0 na
   canônica e aqui, 1.1.0 no harness4codex e no `harness4contract`.

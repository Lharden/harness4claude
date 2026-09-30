# Desfecho terminal reescrito — diagnóstico

Ramo `fix/desfecho-terminal-nao-muda`, sobre `main` em `18ec682`. Pipeline de
bug. O hook deixou a sessão sem task (`idle`); o pipeline não foi registrado no
banco.

Origem: `docs/specs/sim-nao-fecha-entrega-diagnostico.md` §7, item 1, no ramo
`fix/sim-nao-fecha-entrega` (`88ad6ff`, ainda fora do `main`). Lá o defeito foi
declarado fora de escopo, e o teste B1 de `tests/test_sim_nao_fecha_entrega.py`
evita de propósito depender de `complete` em task terminal.

## Sintoma

`TERMINAL_STATUSES = {"done", "abandoned", "superseded"}` vem com o comentário
"Um desfecho ja registrado nao muda mais". Três escritores respeitam
(`abandon_task`, `record_evidence`, `touch_files`); `complete` não.

Evidência real, lida só-leitura do balde
`master-harness-5a8ec6a2/sessions/5a45e264-3b16-4ebb-af06-7a9160139973-cf26e8ce/harness.db`:

| task | nível | status hoje | pipeline / fase | verified | `updated_at` |
|---|---|---|---|---|---|
| `t-20260928-203110092956` | L1-bug | **`done`** | `systematic-debugging → tdd → verify` / `verify` | 1 | 21:33:58Z |
| `t-20260928-211731216430` | L0-question | `done` | `[]` | 0 | 21:17:31Z |

A task L1 virou `superseded` às 21:17:31Z, quando o `sim` L0 abriu
`t-20260928-211731216430` (`start_task` fecha a viva: `verified=1` →
`superseded`). Às 21:33:58Z um `state_cli complete` a levou a `done`. O
`superseded` sumiu do registro: nada no banco diz mais que ela foi substituída.

## Reprodução

Num banco novo, pelos métodos de `HarnessDatabase`, contra `18ec682`. Toda task
terminal chega ao desfecho pelo caminho de produção, e depois o prompt seguinte
abre uma task L1 viva no mesmo escopo. Resultado de
`tests/test_desfecho_terminal.py` contra esse código: **25 falham, 3 passam**.

| Escritor | O que fazia com a task terminal | Onde estoura |
|---|---|---|
| `complete` | `superseded`/`abandoned`/`done` → `done` (sem recusa) | — |
| `transition` | → `active` | `IntegrityError`, `transactional_state.py:1010` |
| `resolve_gate` (`escalation`) | `done` → `active` | `IntegrityError`, `:1093` (`_resolve_escalation`) |
| `reclassify` | → `active`, fase 0, `verified=0` | `IntegrityError`, `:931` |
| `confirm_classification` | → `active`, fase 0, `verified=0` | `IntegrityError`, `:466` |
| `open_gate` | → `awaiting_gate` | `IntegrityError`, `:964` |
| `create_branch` | → `awaiting_gate` | `IntegrityError`, `:789` |
| `request_branch_approval` | dona → `awaiting_gate` | `IntegrityError`, `:831` |
| `resolve_branch_decision` | dona → `active` | `IntegrityError`, `:872` |

Sem task viva no escopo o `IntegrityError` não acontece: a task volta a viver
em silêncio (é o incidente, onde a sucessora era L0 e nasce `done`). Com task
viva, `one_active_task_per_scope` estoura. `state_cli.py` só captura
`StateTransitionError`, `KeyError` e `ValueError`, então a saída é traceback com
exit 1 — medido: `state_cli transition` numa task `superseded` com sucessora
viva.

## Causa raiz

O invariante "desfecho registrado não muda" vive num comentário, e cada
escritor de `tasks.status` decide sozinho se o respeita. São 15 funções que
escrevem `status` (contadas por `UPDATE tasks`/`INSERT INTO tasks`, fora
`_bump`, `acquire_lease` e a migração `verified → active`, que não escrevem
status terminal):

| Protegidas antes (6) | Como |
|---|---|
| `abandon_task`, `record_evidence`, `touch_files` | leem `TERMINAL_STATUSES` |
| `start_task`, `expire_stale_task` | só selecionam `ACTIVE_STATUSES` |
| `register_stop_continuation` | exige `status = 'active'` |

As outras 9 são as da tabela da reprodução. O `IntegrityError` e o `done` por
cima de `superseded` são o mesmo defeito: nove caminhos sem a pergunta.

## Chamadores (auditados antes de mudar a semântica)

| Função | Chamador de produção | Com a recusa |
|---|---|---|
| `complete`, `transition`, `resolve_gate` | `state_cli.py` | exit 2, `erro: <mensagem>`, sem `_sync` |
| `confirm_classification` | `confirm_classification.py`, `state_cli confirm` | exit 2 antes de gravar a projeção |
| `reclassify` | `hooks/harness-reclassify.sh` (promoção L0→L1) | só vê task L0 (`should_promote` exige `L0`); a exceção abaixo a mantém |
| `create_branch` | `branch_state.add`, sobre `_projected_task` | `ValueError`, como as recusas de limite e cooldown |
| `request_branch_approval`, `resolve_branch_decision` | `branch_state.set_status` / `decide` | não recusam (D2) |
| `open_gate` | nenhum fora dos testes | — |

`_projected_task` devolve a task da projeção **sem filtrar status**. Numa
conversa L0 a projeção aponta a task L0, que nasce `done`
(`hooks/harness-classify.sh:729`), e é nela que `branch_state.add` registra a
oferta de ramo. Isso decidiu o alcance da exceção de D3 (abaixo).

## Decisões

Tomadas pelo usuário em 2026-09-30, antes do teste:

- **D1 — `complete` em task terminal recusa, `superseded` inclusive.**
  `superseded` já registra "verificada e substituída antes do `complete`"; o
  `done` por cima apagou, no incidente, o fato de ela ter sido substituída.
  `done` e `abandoned` também recusam.
- **D2 — ramo de task terminal se resolve sem tocar o status dela.** O ciclo do
  ramo sobrevive à task dona: `branch_state._transaction_context` documenta ramo
  fechado depois que a task da mãe expirou. O portão e o ramo se registram; o
  status da dona fica. Oferta **nova** (`create_branch`) recusa.
- **D3 — uma regra só para os nove, com uma exceção nomeada:** task `done` sem
  pipeline (L0) não é desfecho de trabalho.

Refinamento de D3, medido depois da decisão: a exceção foi apresentada para
`reclassify` e `confirm_classification`, mas vale para os nove, porque é a
regra que é única. Sem ela em `create_branch`, oferta de ramo em conversa L0
quebraria (achado de `_projected_task` acima). A regra continua recusando
`create_branch` em desfecho de verdade: `done` depois de `complete`,
`superseded`, `abandoned`.

## Conserto

`scripts/transactional_state.py`:

- `_desfecho_registrado(row)` — a regra: status em `TERMINAL_STATUSES`, exceto
  `done` com `pipeline_json = []`.
- `_exige_task_viva(connection, row, operacao)` — recusa com
  `StateTransitionError` **antes de qualquer escrita**. Chamado por `complete`,
  `transition`, `resolve_gate`, `reclassify`, `confirm_classification`,
  `open_gate` e `create_branch`. Em `complete`, `transition` e `resolve_gate`
  vem antes da checagem de revisão: quem chega numa task substituída carrega a
  revisão de antes da troca, e "revision mismatch" o faria reler e tentar de
  novo só para então ouvir o motivo real.
- `request_branch_approval` e `resolve_branch_decision` gravam portão e ramo e
  só escrevem `tasks.status` se `_desfecho_registrado` for falso (D2).
- `abandon_task`, `record_evidence` e `touch_files` continuam lendo
  `TERMINAL_STATUSES` direto, sem a exceção L0: eles não mudam status, param de
  acumular atividade em task encerrada — e task L0 não acumula. Trocar a leitura
  deles mudaria contagem de toque e evidência em turno L0, fora do defeito.

A recusa não imprime linha de comando: nenhum comando desfaz um desfecho, e
imprimir um que "resolve" prometeria o que não existe. Ela diz o que o desfecho
já registra e onde o trabalho continua:

```
complete recusada: a task t-…-ca352e88 ja terminou como `superseded`, e desfecho registrado nao muda.
`superseded` ja registra a entrega: ela foi verificada (verified=1) e substituida antes do `complete`. Nao ha mais nada a fechar nela.
A task viva deste escopo e t-…-b9812297 (fase systematic-debugging, revision 0): o trabalho que continua e dela.
```

Sem task viva, a última linha vira "Nao ha task viva neste escopo: trabalho novo
entra por task nova, aberta pelo proximo prompt."

`state_cli.py` não muda. Os `IntegrityError` nomeados no relato fecham na
causa; capturar `IntegrityError` no CLI esconderia a próxima ressurreição.

## Falsificação

`tests/test_desfecho_terminal.py`, 28 testes:

- **Metade 1, o defeito:** matriz de 17 casos (escritor × desfecho), cada um
  conferindo que a recusa não escreve nada — foto de `tasks`, `gates`,
  `transitions`, `branches`, `classifications` e `artifacts` da task morta
  **e** da viva, igual antes e depois; a forma exata do incidente pelo
  `state_cli complete` (exit 2, banco e projeção intactos); `state_cli
  transition` com sucessora viva (exit 2, sem traceback); `resolve_gate` do
  `escalation` que sobreviveu ao `complete`; a recusa por desfecho vem antes da
  de revisão.
- **Metade 2a, o que não pode mudar — ramo:** abrir ramo (pedir portão,
  aprovar, abrir) com a dona `superseded`, `done` ou `abandoned`; parkear ramo
  de task concluída. O ramo abre, a decisão fica gravada, a dona não muda, a
  viva não muda.
- **Metade 2b — task L0:** segue promovível por `reclassify`, corrigível por
  `confirm_classification` e aceitando oferta de ramo. Passam antes e depois.

### Medido

Cópias de `scripts/` e `contract/` com uma mutação cada (âncora de ocorrência
única), `HARNESS_PLUGIN_ROOT` apontando para a cópia. M0 é
`transactional_state.py` lido de `18ec682` por SHA.

| Código | Resultado | Testes que caem |
|---|---|---|
| consertado | 28 passed | — |
| M0 `18ec682` (antes do conserto) | 25 failed | toda a metade 1 e 2a |
| M1 regra removida | 25 failed | os mesmos 25 |
| M2 sem a exceção L0 | 3 failed | os 3 da metade 2b |
| M3a `request_branch_approval` reescreve a dona | 2 failed | abrir ramo, dona `superseded` e `abandoned` (na `done` o portão segue pendente e não há pedido) |
| M3b `resolve_branch_decision` reescreve a dona | 4 failed | abrir ramo ×3, parkear |
| M4 sem guarda em `complete` | 5 failed | matriz `complete` ×3, incidente pelo CLI, ordem revisão/desfecho |
| M4 sem guarda em `transition` | 3 failed | matriz `transition` ×2, CLI com sucessora viva |
| M4 sem guarda em `resolve_gate` | 1 failed | `escalation` em task concluída |
| M4 sem guarda em `reclassify` / `open_gate` / `confirm_classification` / `create_branch` | 3 failed cada | a linha da matriz de cada um |
| M5 `complete` confere revisão antes do desfecho | 1 failed | ordem revisão/desfecho |
| M6 recusa sem a task viva | 17 failed | a matriz inteira (os testes de CLI e o do `escalation` não conferem a viva) |

Erro de instrumento achado e corrigido na primeira rodada: com o nome da
mutação no caminho da cópia, `…/scripts/transactional_state.py` passava de
MAX_PATH (260) no Windows e o `state_cli` da cópia saía com
`ModuleNotFoundError`. Os 2 testes de CLI caíam em M2, M3a, M3b e M5 pelo
caminho, não pela mutação. A segunda rodada usa pastas curtas e sonda
`state_cli.py --help` em cada cópia antes de rodar; a tabela acima é dela.

## Fora do escopo (achados de passagem)

1. **`complete` não olha portão pendente.** Task na fase final com `escalation`
   ou `branch-open` pendente vai a `done` com o portão aberto (o teste do
   `escalation` depende disso para montar o caso). Com este conserto o
   `escalation` dessa task fica pendente para sempre, porque `resolve_gate`
   recusa desfecho. Ação: `complete` recusar com portão pendente, como
   `transition` já faz desde `1f140d8`; decidir antes se `branch-open` bloqueia
   o fecho (o ramo sobrevive à task). Dono: próxima passada do ciclo de vida.
2. **Task L0 nasce `done`.** É a origem da exceção de D3: `done` significa ao
   mesmo tempo "entregue" e "sem pipeline". Um status próprio para L0 tiraria a
   exceção da regra; é desenho (toca classify, continuação e projeção).
3. **`record_artifact` em task terminal** sobe `revision` e `updated_at` (via
   `_bump`). Não muda status; é contabilidade de task encerrada, da mesma
   família que `touch_files` parou de fazer em 2026-09-03.
4. **`branch_state._sync_task` projeta a task dona do ramo**, terminal ou não,
   por cima do `state.json` — a mesma família da projeção misturada que
   `fix/sim-nao-fecha-entrega` conserta com `projecao.projetar`. Conferir no
   merge daquele ramo se `branch_state` passa pelo `projetar`.
5. **`state_cli` ainda deixa vazar `IntegrityError` de outros caminhos** (ex.:
   `init --task` com id que já existe). Não é ressurreição de desfecho; fica
   como está pela razão do Conserto.
6. **Resíduo no balde real:** `t-20260928-203110092956` segue `done` no banco
   do incidente. Nada foi escrito ali; corrigir é escrita irreversível no dado
   real e fica com o usuário.
7. **Trabalho paralelo na mesma família:** a sessão `claude/keen-wu-692338`
   (registro em `.remember/now.md`, 11:02) mapeia "estado denorm" em
   `HarnessDatabase` com 16 escritores — as mesmas funções deste conserto. O
   merge dos dois ramos precisa reconciliar `transactional_state.py`.

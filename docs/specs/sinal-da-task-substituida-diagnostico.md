# Diagnóstico — o sinal da task substituída fica fora de `signals.json`

Task `t-20260930-140618791214` (L2-bug). Ramo `fix/sinal-da-task-substituida`,
aberto de `fix/sim-nao-fecha-entrega` (`88ad6ff`), que ainda não está em `main`.

## 1. Sintoma

Depois do conserto de `fix/sim-nao-fecha-entrega`, `projecao.projetar` deixa o
`state.json` com a task **viva** do escopo quando o `state_cli` opera numa task
já substituída. O `record_signal.py --expect-task <substituída>` do DONE lê
`task_id` da projeção, encontra a viva e sai 2 sem gravar. O desfecho da
substituída está no `harness.db`; a linha dela em `signals.json` não existe.
Declarado em `sim-nao-fecha-entrega-diagnostico.md` §7 e travado no teste B2
(`test_sim_nao_fecha_entrega.py::TestProjecaoDeUmaTaskSo::test_task_substituida_nao_rouba_a_projecao_da_viva`),
que hoje espera exit 2 e nada gravado.

## 2. Causa raiz

`record_signal.main` tem **uma** fonte para "de que task é este registro": a
projeção. `--expect-task` foi escrito (incidente 2026-06-12) como guarda contra
projeção sobrescrita — compara e aborta. Ele nunca foi usado como **chave**. Com
a projeção passando a descrever sempre a task viva, a guarda recusa por
construção toda task que não é a viva, mesmo quando o banco tem tudo o que o
registro precisa:

| Campo do registro | Hoje vem de | O banco tem |
|---|---|---|
| `task_id` | `state.json` | `tasks.task_id` |
| `classification` | `state.json` | `tasks.legacy_level` (acompanha `confirm_classification` desde `c6647aa`) |
| `classification_meta` | `state.json` | `classifications` (PK `task_id`) — a projeção nova já é montada daqui |
| `steps_executed` (sem `--steps`) | `state.json.pipeline` | `tasks.pipeline_json` |
| `files_modified` | contador ∪ `files` do banco | `files` |

A segunda metade é a mesma forma: `--abandoned` chama `abandon_task` no
`task_id` **da projeção**. O exemplo do `SKILL.md` (seção DONE) não passa
`--expect-task`, e o abandono é disparado por troca de assunto — exatamente o
momento em que o classify já abriu a task nova e apontou a projeção para ela.
Seguido ao pé da letra, o protocolo encerra a task que o usuário acabou de
pedir. `abandon_task` é escrita terminal na autoridade: status terminal não
volta (`TERMINAL_STATUSES`).

## 3. Medições (só leitura, bancos reais em `~/.claude/harness/projects`)

Script: `medir_origens.py` e `medir_diferenca.py` no scratchpad da sessão.

- **Edit/Write já chegam ao banco.** `harness-reclassify.sh` chama
  `touch_file(state_task_id, alvo, origem='edit')` desde `dcf7aed`
  (2026-08-28), e `touch_files` insere em `files`. Nos 93 bancos com a tabela
  `touches`: 2 101 toques `edit`, **0** sem linha correspondente em `files`.
  O docstring de `files_modified` ("as fontes são disjuntas"; "a tabela `files`
  só recebe o que o hook transacional vê") está desatualizado desde antes de
  ser escrito (`c6647aa`, 2026-09-02).
- **O que só o contador vê.** 13 baldes têm contador com arquivos; em 10 todos
  os arquivos estão no banco sob a mesma task. Nos 3 restantes (e em 9 baldes
  de schema antigo), a diferença é de dois tipos: task L0 nascida `done`
  (`started_at == updated_at`), em que `touch_files` recusa por status
  terminal; e escrita depois de a task encerrar. É isso — e só isso — que fica
  de fora quando o contador não pode ser usado.

Consequência para o pedido: "marcar quando o contador não pôde ser usado (os
arquivos Edit/Write ficam de fora)" vale para a marca, não para o parêntese.
Os Edit/Write do período em que a task era a da projeção já estão no banco; o
que o descarte perde é o Edit/Write que o banco recusou. A marca diz que o
contador não entrou; o valor de `files_modified` é limite inferior só nesse
sentido.

## 4. Mapa de consumidores (graph-context)

Grafo `graphify-out/graph.json` gerado de `main` às 11:06; `record_signal.py`
e `expire_stale_pipeline.py` são iguais em `main` e na base do ramo. Grep
conferido contra o grafo.

| Consumidor | O que usa | Efeito do conserto |
|---|---|---|
| `skills/harness-workflow/SKILL.md` §Protocolo 7 e §DONE | CLI `--completed`/`--abandoned` | exemplo `--abandoned` ganha `--expect-task`; texto do exit 2 muda |
| `scripts/state_cli.py:_sync` (aviso) | promete "record_signal --expect-task X vai recusar" | promessa fica falsa; o aviso passa a dizer que o registro sai do banco |
| `scripts/expire_stale_pipeline.py:expire` | `build_task(state, counter, ...)` sem `harness_dir`, `record` | nenhum: assinatura de `build_task` preservada |
| `scripts/harvest_classify_labels.py` | `actual_level` | nenhum |
| `tests/test_record_signal.py` | `main`, `build_task`, `files_modified` | sem banco: comportamento atual, testes ficam |
| `tests/test_ciclo_de_vida_da_task.py::test_f1_record_signal_abandoned_fecha_a_task_no_banco` | `--abandoned --expect-task` | já passa `--expect-task`; fica |
| `tests/test_harness_dir_resolution.py` | `--completed` sem `--expect-task`, sem banco | nenhum |
| `tests/test_sim_nao_fecha_entrega.py` B1, B2 | `--completed --expect-task` | B1 continua; B2 inverte (pedido) |

Nenhum hook chama `record_signal` como processo; nenhum outro chamador de
`--abandoned` existe fora do `SKILL.md` e do próprio docstring.

## 5. Conserto

**R1 — Com `--expect-task` e `harness.db` no balde, o banco é a fonte.**
`HarnessDatabase.task(id)`, `classification(id)`, `files(id)`. A projeção não é
lida. `classification = legacy_level`; `steps_executed` sem `--steps` vem de
`pipeline` do banco.

**R2 — O contador entra só se for da task.** `counter.task_id == expect`: une
com `files` como hoje, e o registro leva `contador_usado: true`. Senão
(outra task, sem `task_id`, ausente, ilegível) fica fora e o registro leva
`contador_usado: false`. Campo sempre presente no caminho do banco; ausente só
no caminho da projeção (R3/R4). Revisado no grill (§7).

**R3 — Sem banco, nada muda.** Projeção de outra task: exit 2, nada gravado.

**R4 — Banco presente sem a task.** Cai na regra de R3 (projeção da task grava;
de outra, exit 2). É o que acontece hoje nesse caso; o banco não sabe nada que a
projeção não diga.

**R5 — Banco presente e ilegível.** Exit 1, nada gravado, erro no stderr.
"Não consegui perguntar" não vira "grava o que a projeção disser" — mesma regra
de `projecao.projetar`.

**R6 — `--abandoned` exige `--expect-task`.** Sem ele (ou vazio): exit 2, nada
gravado, nada abandonado, e a mensagem imprime a linha que corrige com um
marcador no lugar do id — nunca o id da projeção, que numa troca de assunto é o
da task nova — e lista as tasks recentes do balde, do banco, para quem perdeu o
id. Com ele, o abandono vai para a task esperada, nunca para a da projeção; task
fora do banco (R4) não chama `abandon_task`. Custo: só o exemplo do
`SKILL.md` chama sem a flag (§4); o único teste de `--abandoned` já a passa.
`--completed` segue aceitando a chamada sem `--expect-task` (`test_harness_dir_resolution`
depende disso e ninguém é encerrado por ela).

**R7 — Textos que prometem o comportamento antigo.** Aviso do
`state_cli._sync`; parágrafo "Exit 2 não se conserta..." e exemplos do
`SKILL.md`; docstrings de `record_signal` (módulo e `files_modified`);
`sim-nao-fecha-entrega-diagnostico.md` §7 aponta para este ramo.

## 6. Plano de teste (vermelho antes)

| # | Caso | Vermelho contra o código atual |
|---|---|---|
| S1 | B2 invertido: substituída registrada com o meta, a classificação e o pipeline dela; projeção e status da viva intactos | exit 2 |
| S2 | Contador da viva no balde: arquivos dele fora do registro da substituída, `contador_usado: false`; `state.json` e contador byte a byte iguais | exit 2 |
| S3 | Contador da própria task: entra na união, `contador_usado: true`; contador ausente ou sem `task_id`: `false` | campo ausente |
| S4 | `--abandoned` sem `--expect-task` (e com `""`), projeção da viva: exit 2, viva segue viva, nada gravado, stderr traz a linha com `--expect-task` e marcador, sem o id da viva na linha | exit 0 e a viva `abandoned` |
| S5 | `--abandoned --expect-task` da task viva com projeção de outra task: abandona a esperada, a da projeção intacta | exit 2 |
| S6 | Banco sem a task, projeção dela: grava pela projeção; projeção de outra: exit 2 | controle (passa antes e depois) |
| S7 | Banco ilegível: exit 1, nada gravado | exit 0 |
| S8 | `--abandoned --expect-task` de task fora do banco, projeção dela: sinal gravado, exit 0, banco intocado | exit 1 (`abandon_task` levanta depois de gravar) |

S6 é controle: tem de passar antes e depois. Os outros são a falsificação — cada
um reprova contra `88ad6ff`.

## 7. Grill — rodada 1

`wf-grill` sobre este arquivo, sem a conversa que o produziu: 5/5 lentes vivas,
47 perguntas, 6 bloqueantes (run `wf_b8ccfd8e-fdd`). Agrupadas; os números
citam o retorno.

### 7.1 Resolvidas por evidência

| Grupo | Resposta | Evidência |
|---|---|---|
| A linha copiável de R6 leva o id da projeção? (1, 4, 5) | Não: leva um marcador (`<task_id anotado no inicio do pipeline>`). O id da projeção é justamente o da viva. Para quem perdeu o id (compactação, sessão retomada), a mensagem lista as tasks recentes do balde, do banco, só leitura, com status — a substituída aparece como `superseded`. | R6 revisada; S4 confere que a linha não traz o id da viva |
| `record_signal` escreve no `state.json` ou no contador? (2, 3, 21, 28) | Não, e passa a ser NEVER travado: as únicas escritas são `signals.json` e, com `--abandoned`, `abandon_task` da task esperada. S1, S2 e S5 conferem `state.json` e `.session-files-count` byte a byte. | `record_signal.main`: nenhum `open(..., 'w')` fora de `record` |
| Banco de "schema antigo" cai em R5? (6, 9, 12, 16, 17) | Não. `HarnessDatabase.__init__` roda `_ensure_schema`, que cria o que falta; os 9 baldes do §3 não tinham `touches` na leitura `mode=ro`, que não migra. `harness.db` de 0 bytes vira banco vazio: task ausente, R4. R5 é só erro do SQLite que sobra depois do `busy_timeout` de 5 s (arquivo corrompido, trava que não solta). | `transactional_state.py:126-130`, `_connect` |
| Campo vazio no banco (8, 18, 35) | `legacy_level` e `pipeline_json` são `NOT NULL`. Sem linha em `classifications` (task anterior à tabela): `classification_meta: {}`, o mesmo default do caminho da projeção (`state.get("classification_meta", {})`); `recompute_aggregates` só conta `agreed` não nulo. | `transactional_state.py:165-169`; `build_task` |
| Desfecho de task terminal (7, 11, 14, 20, 27, 45) | O registro leva o desfecho declarado na CLI, como hoje; o status do ciclo de vida fica no banco. `--abandoned` de task terminal: `abandon_task` não muda status terminal e devolve a task; exit 0. Task fora do banco (R4): o abandono no banco não é chamado — hoje ele levantaria "task not found" depois de gravar o sinal. | `abandon_task` (`TERMINAL_STATUSES`); S5 e caso novo S8 |
| Registro duplicado (39, 44) | `record` substitui por `task_id`; segunda chamada reescreve a mesma linha. | `record_signal.record` |
| R1 vale quando a projeção é da task (10, 15) | Vale. A tabela do §2 é a lista inteira do que `build_task` lê da projeção (`task_id`, `classification`, `classification_meta`, `pipeline`). A projeção nova é montada com `banco.classification(...)`, a mesma função — B1 continua igual. | `build_task`; `projecao.py:124` |
| Contador sem `task_id`, ausente ou malformado (19, 26, 36, 42) | O campo passa a se chamar `contador_usado` (booleano, sempre presente no caminho do banco): `true` só quando o `task_id` do contador é o esperado. Ausente, sem `task_id`, de outra task ou ilegível: `false`. "Descartado" deixava o ausente sem resposta. O contador zera quando o `task_id` muda (`harness-reclassify.sh`, `harness-classify.sh`), então o carimbo cobre a lista inteira. | R2 revisada |
| O consumidor aceita campo novo? (32, 38, 43) | `signals.schema.json` tem `additionalProperties: true` em todos os níveis; `harvest_classify_labels` lê só `actual_level`. Ausência do campo = registro do caminho da projeção. | `schemas/signals.schema.json` |
| `task/classification/files` existem nessa forma? (22, 29) | Existem na base e em `main`; `task` devolve `pipeline` decodificado; o meta usa `classification`, a mesma função que `projecao.projetar`. | `transactional_state.py:414, 492, 1136` |
| `--expect-task ""` (40) | Vazio ou só espaço conta como ausente — para `--abandoned`, exit 2. | R6 revisada |
| Leitura do contador em corrida com o classify (41) | Lido uma vez; a decisão e a união usam a mesma leitura. | implementação |

### 7.2 Mudanças no conserto por causa do grill

- R2: `contador_descartado` → `contador_usado`, com os casos de borda acima.
- R6: linha copiável com marcador, nunca com o id da projeção; lista das tasks recentes do balde para recuperar o id.
- NEVER novo: `record_signal` não escreve `state.json` nem `.session-files-count`.
- S8 novo: `--abandoned --expect-task` de task fora do banco, com projeção dela, grava o sinal e sai 0 sem tocar o banco.

### 7.3 Resíduos declarados

- **Backfill** (13): as tasks substituídas desde `fix/sim-nao-fecha-entrega`
  ficam sem linha. Rodar o caminho novo nos baldes reais é escrita em dado
  real; fica com o usuário.
- **`steps_executed` sem `--steps`** (25): vem do pipeline planejado, como vinha
  da projeção. O protocolo passa `--steps` explícito.
- **Ordem de merge e deploy** (23, 33, 46): este ramo contém a base; mesclar
  este traz os dois. Script e `SKILL.md` vão juntos no mesmo `deploy_to_cache`;
  deploy é decisão do usuário depois do merge.
- **Balde de outra sessão** (24): task nascida em outra sessão mora em outro
  balde; o `--harness-dir` errado cai em R4 e sai 2 com a mensagem que já nomeia
  essa causa.
- **R6 é decisão do agente** (30): o pedido deixou a decisão aberta ("decidir se
  o script deve exigi-lo"). Motivo no §5 R6; reverter é tirar a checagem.
- **Os 2 vermelhos pré-existentes** (31, 34): nomeados pelo nodeid no relatório
  de verificação, medidos na suíte-base deste ramo antes de qualquer edição.
- **`sim-nao-fecha-entrega-diagnostico.md` §7** (34): só ganha a nota que aponta
  para cá; o texto de lá fica.

### 7.4 Boundaries

- **ALWAYS** — com `--expect-task` e banco com a task, o registro sai do banco;
  a linha que corrige traz marcador, não id adivinhado.
- **NEVER** — `record_signal` escreve `state.json` ou `.session-files-count`;
  abandona task que não seja a de `--expect-task`; conta contador de outra task.
- **ASK** — backfill em baldes reais; deploy para o cache.

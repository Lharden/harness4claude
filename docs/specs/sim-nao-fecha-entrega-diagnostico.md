# Um "sim" não pode fechar a entrega

**Status:** conserto implementado; grill rodada 1 resolvida (§8) · aberto em 2026-09-28
**Task:** `t-20260928-213959694055` (L2-bug) · ramo `fix/sim-nao-fecha-entrega`
**Origem:** incidente medido na sessão `5a45e264-3b16-4ebb-af06-7a9160139973`,
balde `master-harness-5a8ec6a2/sessions/5a45e264-…-cf26e8ce`.

## 1. Sintoma

1. A task `t-20260928-203110092956` (L1-bug, fase `verify`, status `active`)
   esperava uma aprovação do usuário pedida em texto, não por `AskUserQuestion`.
2. O usuário respondeu `yes`. O UserPromptSubmit abriu a task
   `t-20260928-211731216430` (L0-question, `done`, pipeline vazio), e a L1 virou
   `superseded`.
3. Depois de `state_cli.py complete --task t-20260928-203110092956`, o
   `state.json` ficou misturado: `task_id`, `status` e `pipeline` da L1;
   `classification_meta`, `started_at`, `prompt_len` e `prompt_excerpt` do
   prompt `yes`. O `record_signal.py` teria gravado o meta errado na telemetria
   de acurácia; os campos foram restaurados à mão.
4. Contraste: minutos antes, na mesma task, uma mensagem de outra sessão chegou
   como prompt e recebeu `HARNESS v3 CONTINUING` corretamente.

## 2. Linha do tempo medida (UTC)

Fontes: `harness.db` do balde (somente leitura) e `~/.claude/harness/emissions.jsonl`.

| Hora | Fato | Fonte |
|---|---|---|
| 20:31:10 | L1 criada (`suggested` L2-docs, `final` L1-bug, semântico 0,8) | `tasks`, `classifications` |
| 21:00:10 | transição `tdd → verify` — fase final (índice 2 de 3) | `transitions` id 3 |
| 21:01:48, 21:12:06 | `continuing` emitido para a sessão | `emissions.jsonl:4544,4554` |
| 21:12:51 | evidência id 1: exit 0, 10/10 → `verified = 1` | `evidence` |
| 21:17:31 | `yes` → task L0 `done`; L1 → `superseded` | `tasks` |
| 21:33:07, 21:33:12 | evidências 2 e 3 gravadas na L1 já terminal (status congelado) | `evidence` |
| 21:33:58 | `complete` → L1 `superseded → done` | `tasks.updated_at` |

Nenhuma emissão às 21:17: o caminho L0 do classify sai por `print` cru, sem
o emissor — coerente com o que se viu. O contador `.session-files-count` do
balde ainda aponta para a task do `yes` (`count: 0`): os arquivos Edit/Write da
L1 também se perderam para o `record_signal`.

A diferença entre 21:12:06 (CONTINUING) e 21:17:31 (task nova) é **uma coluna**:
`verified` virou 1 às 21:12:51, com a task parada na última fase e sem gate.

## 3. Causa raiz

### Metade 1 — a regra F2 fura a decisão D2

`scripts/continuation_policy.py::continua` tem uma exceção para a entrega sem
`complete`: fase final + `verified` + nenhum gate pendente ⇒ a task **não**
continua, e o prompt seguinte a fecha como `superseded`. A exceção olha só o
estado da task, nunca o prompt: um `yes` e um pedido de trabalho novo recebem a
mesma resposta (`NENHUMA`), o classify abre task nova, e `start_task` fecha a
entrega.

Isso contradiz uma decisão registrada do usuário. Relatório
`ciclo-de-vida-da-task-causa-raiz.md` (vault), §6 e §8.4, 2026-09-23:

> **D2 = sim** — task L0 não encerra pipeline vivo; só troca explícita ou L1/L2
> novo encerra.

A F2 entrou depois, no `/code-review` do mesmo ramo (§9.4), para que "trabalho
entregue sem `complete`" não virasse CONTINUING por 24 h. A preocupação da F2 é
trabalho **novo** sendo engolido pela task velha — e trabalho novo é prompt que
abre pipeline (L1/L2). Ela foi escrita sem olhar o nível do prompt e, com isso,
também fecha a entrega em prompt L0, que é exatamente o que a D2 proíbe.

A premissa escondida: "fase final + evidência fresca" = "o modelo acabou de
falar com o usuário". O incidente é o contraexemplo: o modelo estava na fase
final **esperando a resposta** a uma pergunta feita em texto. Os gates formais
(`HUMAN_GATES`) não cobrem "posso fazer o merge?", então `pending_gate` fica
vazio e nada no banco distingue "esqueceu o `complete`" de "está esperando o
usuário".

Fechar é a ação que não se desfaz (`superseded` é terminal). O princípio já
escrito no próprio `continuation_policy` — `DESCONHECIDA` nunca abre task,
porque abrir fecharia a que talvez exista — vale aqui: na dúvida entre "o
usuário respondeu" e "o usuário pediu outra coisa", o prompt que não pede
pipeline não pode fechar a entrega.

### Metade 2 — `state_cli._sync` projeta uma task por cima de outra

`scripts/state_cli.py::_sync` lê o `state.json` e faz `projection.update(...)`
com os campos da task operada, sem conferir se a projeção é **dessa** task.
Quando é de outra, sobrevive tudo o que só a projeção guarda —
`classification_meta`, `started_at`, `prompt_len`, `prompt_excerpt`,
`schema_version` — e o resultado descreve duas tasks ao mesmo tempo. Todo
leitor da projeção herda a mentira; o `record_signal` é o que grava telemetria
com ela (`build_task` lê `classification_meta` de `state.json`).

O mesmo `update` cego tem um segundo efeito: ele **rouba** a projeção. Se a
outra task é a viva do escopo, apontar a projeção para a operada desvia os
toques e a evidência do PostToolUse (que acha a task pelo `task_id` da
projeção) até o próximo prompt reparar. Depois do conserto da metade 1, este é
o caminho que sobra para chegar aqui: `complete`/`evidence` numa task
substituída por trabalho novo, que está vivo.

## 4. Mapa de consumidores (graph-context)

Grafo `graphify-out/graph.json` gerado às 18:40 locais, depois do HEAD
`c175fd9` (17:12) e antes de qualquer edição — conferido. O grafo é AST e não
enxerga o Python dentro dos `.sh`; esses vieram de grep.

### 4.1 Quem pergunta "há task viva?"

| Chamador | Uso | Muda? |
|---|---|---|
| `hooks/harness-classify.sh:467-547` | `VIVA` e não troca ⇒ CONTINUING; senão classifica e abre | **sim** — passa o nível do prompt |
| `hooks/harness-session-start.sh:470-478` | `VIVA` ⇒ RESUMING | não — sem prompt, F2 intacta |
| `hooks/skill_router.py:297-299` | `NENHUMA` ⇒ o router fala | **sim** (§8.2 abaixo) — roda no mesmo UserPromptSubmit e tem o prompt |

### 4.2 Quem escreve a projeção `state.json`

| Escritor | Task projetada | Troca de task | Veredito |
|---|---|---|---|
| classify, task nova (`:747-793`) | a que acabou de abrir | projeção nova | ok |
| classify, `_reparar_projecao` (`:487-542`) | a viva do banco | reconstrói + meta do banco | ok |
| PostToolUse, `_sync_projection` (`harness-transactional.py:878`) | a da projeção | relê e recusa outra task | ok |
| reclassify (`harness-reclassify.sh`) | a da projeção | — | ok |
| `confirm_classification.py` | a da projeção | — | ok |
| TTL, `expire_stale_pipeline._sync_projection` | a corrente do banco | projeção nova | ok |
| **`state_cli._sync`** | a do `--task` | `update` cego | **mistura e rouba** |
| **`branch_state._sync_task`** | a DONA do ramo (`branches.task_id`), não a da projeção (`_transaction_context:130-135`) | `update` cego | **mistura e rouba** |

Os dois escritores defeituosos são também os dois que ficaram fora do helper
atômico (`gravar_json_atomico`) na passada `ciclo-de-vida-da-task`: tmp de nome
fixo (`.json.tmp`, `.json.branch.tmp`) e `replace` sem retentativa.

### 4.3 Quem lê a projeção e sofre com a mistura

`record_signal.build_task` (meta → `signals.json`), `confirm_classification`
(atualiza o meta que achar), `reclassification_policy` e o reclassify (decidem
promoção pelo meta), `harness-transactional._database_for_payload` (acha a task
pelo `task_id`).

## 5. Conserto

1. **`continuation_policy`** — `entregue(task)` vira função própria;
   `continua(task, *, nivel_do_prompt=None)` e `task_viva(balde, *,
   nivel_do_prompt=None)`. Task entregue continua **só** para prompt L0 (D2).
   Domínio do nível: `None`, `"L0"`, `"L1"`, `"L2"` — o que `classify_prompt`
   devolve; fora dele levanta, e pela `task_viva` vira `DESCONHECIDA`, que
   nunca abre nem fecha task. Sem nível (session-start), a F2 fica como está.
2. **classify** — calcula `classify_prompt(msg)` antes da pergunta e passa o
   nível. Troca explícita continua encerrando (`esquece isso, me diz as horas`
   é L0 e sai pela porta da troca — relatório do ciclo de vida, §8.2). O CONTINUING de uma
   entrega diz que a fase final já está verificada e sem `complete`, e só
   informa: não diz que houve aprovação (o hook não sabe se há pergunta
   pendente nem quem escreveu o prompt).
3. **router** — `passes_guards` pergunta com o nível do prompt, como o classify
   no mesmo evento; sem isso ele oferecia skill no turno em que o classify
   anunciava CONTINUING.
4. **`projecao.projetar`** — um lugar para a regra "uma projeção descreve uma
   task só": mesma task ⇒ atualiza no lugar; outra task viva no escopo ⇒ não
   toca e devolve o id dela; senão ⇒ projeção nova montada do banco, com
   `classification_meta` do banco. "Viva" é o status do banco
   (`current_task`), não a política sem prompt — a entrega que espera o `sim`
   conta como viva. Relê o `task_id` do arquivo antes de gravar (regra F7) e
   grava por `gravar_json_atomico`. `state_cli._sync` e
   `branch_state._sync_task` passam a usá-la; o `state_cli` avisa em stderr
   quando a projeção ficou com outra task.
5. **`skills/harness-workflow/SKILL.md`** — o remédio documentado para o exit 2
   do `record_signal` era "restaure o state da sua task". Com o item 4 isso
   passaria a tirar a projeção da task viva à mão; o texto agora diz para não
   editar.

`start_task` não muda (relatório do ciclo de vida, decisão §8.4). `complete` aceitando status terminal é
outro defeito (§7).

## 6. Plano de teste

`tests/test_sim_nao_fecha_entrega.py`. Os itens ✗ têm de falhar antes do
conserto; os ✓ são a metade de falsificação — verdes antes e depois, provam que
o conserto não calou o que já funcionava. "✗ API" é vermelho por assinatura
ausente, não pelo defeito: a prova comportamental da metade 1 é o A1, pelo
hook. A medição fica no relatório de verificação.

| # | Cenário | Esperado | Antes |
|---|---|---|---|
| A1 | entrega L1/L2 + `yes` / `sim` / `pode` pelo hook | CONTINUING com a nota; nenhuma task nova; projeção, classificações e contador intactos | ✗ |
| A2 | entrega + pedido L2 novo | task nova, entrega `superseded` (F2) | ✓ |
| A3 | entrega + `esquece isso, me diz as horas` | task nova, entrega `superseded` (troca explícita) | ✓ |
| A4 | fase final sem evidência + `yes` | CONTINUING sem a nota | ✓ |
| A5 | política: entrega + nível L0 / L1 / L2 / nenhum; `entregue`; meio do pipeline; nível fora do domínio | viva / nenhuma / nenhuma / nenhuma; …; `ValueError` e `DESCONHECIDA` | ✗ API (nenhum ✓) |
| R1 | router, entrega + resposta L0 longa | cala | ✗ |
| R2 | router, entrega + pedido L2 novo | fala | ✓ |
| B1 | réplica de 21:33:07: `evidence` na L1 com a projeção do `yes`, e o `record_signal` | projeção só da L1; `signals.json` com o meta da L1 | ✗ |
| B1b | `complete` da task viva com a projeção de uma task morta | projeção nova, só da viva | ✗ |
| B2 | `evidence` na substituída com trabalho novo vivo (entregue ou não) | projeção intacta; aviso nomeia a dona; `record_signal` sai 2 e não grava | ✗ |
| B3 | `transition` na própria task | atualiza no lugar, preserva meta, prompt e `started_at` | ✓ |
| B4 | `branch_state.attach_files` com a dona do ramo substituída por task viva | projeção continua da viva | ✗ |
| B5 | idem, sem task viva no escopo | projeção só da dona, meta do banco | ✗ |
| B6 | outra task posta na projeção entre a decisão e a escrita | nada gravado, id da intrusa devolvido | ✗ API |
| G | `state_cli.py` e `branch_state.py` passam por `projetar`, sem `projection.update(` | guarda de regressão | ✗ |

## 7. Fora de escopo, declarado

- **`complete`, `transition` e `resolve_gate` reescrevem desfecho terminal.**
  `TERMINAL_STATUSES` diz que desfecho registrado não muda, e `abandon_task`,
  `record_evidence` e `touch_files` respeitam; os três não. No incidente,
  `complete` levou `superseded → done`. Com a metade 1 consertada o caminho do
  incidente fecha, mas o defeito continua para task substituída por L1/L2.
  Ação: task separada, com teste vermelho antes; dono: próxima passada do ciclo
  de vida.
- **`record_signal` lê `classification_meta` da projeção**, não do banco — a
  mesma família de "ferramenta que lê a projeção enquanto o banco manda"
  (`ok-nao-pode-virar-l1-spec-light.md`, D-B). Depois do conserto ele não
  consegue mais gravar meta errado pelo protocolo (com `--expect-task`: ou a
  projeção é da task, sem mistura, ou ele sai 2 sem gravar — B1 e B2). O que
  sobra é **falta**, não mentira: a task substituída por trabalho vivo fica sem
  linha em `signals.json`. Ação: `record_signal --expect-task` ler a task e o
  meta do banco pelo id; na mesma passada, o exemplo `--abandoned` do
  `SKILL.md`, que não passa `--expect-task` e encerraria a task da projeção.
  Dono: task separada. **Fechado em `fix/sinal-da-task-substituida`**
  (`sinal-da-task-substituida-diagnostico.md`): o registro sai do banco pelo
  id, e `--abandoned` exige `--expect-task`; o B2 passou a esperar a linha da
  substituída.
- **Os dois vermelhos pré-existentes da suíte** (medidos em `c175fd9`, antes de
  qualquer edição deste ramo): `test_verify_por_tabela.py::test_CONTROLE_codigo_antigo_adjudicava_medium_low`
  usa `git show main:...` como "código antigo", e desde o merge `c175fd9` o
  `main` já é o código novo — reprova por construção; ação: fixar o controle em
  `d017a6b^`, task separada. `test_deploy_drift.py::test_o_cache_reflete_o_publicado`:
  o cache instalado não recebeu o deploy desse merge (os 5 arquivos listados são
  os do verify-por-tabela); ação: `python scripts/deploy_to_cache.py --apply` de
  um checkout de `main`, decisão do usuário.
- **Resíduo do incidente no balde real** (`master-harness-5a8ec6a2/…-cf26e8ce`):
  a task L0 do `yes`, a L1 levada a `done` por um `complete` em status
  terminal e o `.session-files-count` ainda apontando para o `yes`. Nada foi
  escrito no dado real; limpar é escrita irreversível e fica com o usuário.
  Projeções já misturadas por código antigo em outros baldes também não são
  migradas: o próximo prompt que abre task as regrava inteiras.

## 8. Grill — rodada 1

`wf-grill` sobre este arquivo, sem a conversa que o produziu: 5/5 lentes vivas,
77 perguntas, 6 bloqueantes (run `wf_8ec3454a-257`). Agrupadas; os números
citam o retorno.

### 8.1 Resolvidas por evidência

| Grupo | Resposta | Evidência |
|---|---|---|
| Que predicado de "viva" o `projetar` usa? E com banco ilegível? (1, 6, 41, 56, 63) | Status do banco via `current_task` (`ACTIVE_STATUSES`): a entrega à espera do `sim` é viva e fica protegida. Banco ilegível levanta **antes** de gravar. | `projecao.projetar`; teste B2 parametrizado com a viva entregue |
| O `record_signal` grava meta da viva como se fosse da substituída? (2, 4, 31, 40) | Não: com `--expect-task` ele sai 2 e não grava nada. Sem mistura na projeção, não há caminho de meta emprestado. A lacuna que sobra é falta de registro (§7). | teste B2 (`returncode == 2`, `signals.json` ausente) |
| Duas tasks vivas no escopo? (36) | Impossível: índice único `one_active_task_per_scope`. | `transactional_state.py:180-181` |
| Quais campos a projeção nova tem, de onde vêm? (11, 25, 34, 67) | Os de `_reparar_projecao` do classify, do banco (`tasks` + `artifacts`); meta da linha única de `classifications` (PK `task_id`); `schema_version` 3; `prompt_len`/`prompt_excerpt` ausentes — nenhum leitor os usa (grep). | `projecao._projecao_nova`; `transactional_state.py:182-189`; B1/B5 conferem campo a campo |
| O CONTINUING grava algo do `yes`? (26, 30, 42) | Não. A1 confere projeção inteira, linhas de `classifications` e contador (que nasce com 2 arquivos, não 0). | A1 |
| O teste mede o código do ramo e fica fora do estado real? (21, 33) | `HARNESS_PLUGIN_ROOT` vazio no ambiente ⇒ o `conftest` aponta para o worktree; `HARNESS_DIR`, `MASTER_HARNESS_HOME` e `AI_BRAIN_PATH` isolados por classe. Sem bash, a classe A é pulada com motivo visível (`BASH_REQUIRED_CLASSES`). | `conftest.py`: `pytest_configure`, fixtures `harness_dir` e `ai_brain_dir` |
| O código que rodou às 21:17 é o do HEAD? (32) | Sim para os arquivos envolvidos: o teste de drift lista os 5 arquivos em que o cache difere de `main`, e nenhum é deste conserto. | `test_deploy_drift`, suíte-base |
| `classify_prompt` tem efeito colateral ou serviço externo? (70) | Não: regex pura, sem I/O; já rodava em todo prompt que não continuava. | `scripts/classify_prompt.py` |
| B1 depende do defeito do §7? (12, 36) | Dependia; o `complete` saiu da réplica (o `_sync` que misturou é o mesmo, e o do `evidence` rodou primeiro) e ganhou caso próprio sobre task viva (B1b). | B1, B1b |
| `entregue` usa a coluna ou a frescura? (27, 48, 61) | A coluna `verified`, como a F2. Edição depois do `yes` zera a coluna e a task volta a continuar para todo nível; o portão de Stop cobra evidência no fim do turno e a devolve a "entregue" antes do prompt seguinte. | `touch_files` zera `verified`; `_handle_stop` |

### 8.2 Mudanças no conserto por causa do grill

- Router pergunta com o nível (49, 59, 65, 74) — §5.3, testes R1/R2.
- Domínio de `nivel_do_prompt` fechado; fora dele levanta e falha fechado (8, 55, 58, 65).
- Nota do CONTINUING só informa, não insinua aprovação (19, 46, 17, 23).
- `projetar` relê a projeção antes de gravar (62) — teste B6.
- Aviso do `state_cli` e `SKILL.md` deixam de mandar restaurar o `state.json` à mão (31, 60).
- Guarda de regressão para os dois escritores (53) — teste G.

### 8.3 Resíduos declarados

- **O juiz é o nível do regex** (3, 5, 7, 14, 15, 22, 28, 37). Medido sobre os
  1 037 prompts humanos de `~/.claude/harness/calib/classify-labels.json`: 270
  começam com token de aprovação; 247 (91%) saem L0 e passam a continuar a
  entrega. Os 23 L1/L2 são quase todos longos e com instrução nova ("certo,
  tudo resolvido. agora me ajude a formular o email"); curtos com palavra-chave
  são 2 (`faca a integracao`, `pode seguir com o plano aplicado proximo passo!`).
  Resposta de aprovação com palavra de L1/L2 (`yes, fix the typo and merge`,
  `pode seguir com o plano`, `/code-review`) ainda fecha a entrega. O conserto é
  monótono: o conjunto de prompts que fecha a entrega só encolhe (de "todos"
  para "L1/L2 ou troca"), e nenhum prompt que continuava passa a fechar. O
  erro que ele pode introduzir é o inverso — pedido novo lido como L0 fica na
  entrega —, e prompt L0 não tem pipeline em nenhum dos dois desenhos.
  Esperar a confirmação semântica antes de fechar exigiria um caminho para o
  modelo abrir a task nova depois; é desenho, não conserto.
- **Vocabulário de troca em resposta negativa** (16, 24, 64): `não, cancela o
  merge` casa `\bcancela\b` e fecha a entrega. Vale igual no meio do pipeline
  desde antes deste ramo; é a mesma precisão de regex do item acima.
- **Origem do prompt** (10, 17, 23, 29, 71): o payload não diz quem escreveu.
  Mensagem de outra sessão segue a regra do nível, por decisão D3 (manter fora
  das assinaturas de automação).
- **Duração da entrega viva para L0** (9, 18, 75): até `complete`, troca, L1/L2
  ou TTL (24 h). A nota do CONTINUING faz o modelo fechar no primeiro prompt L0
  seguinte; a F2 perdia o sinal da task esquecida (`superseded` sem registro).
- **D2 contra F2** (38): D2 é decisão explícita do usuário (§6 e §8.4 do
  relatório do ciclo de vida, 2026-09-23); F2 é achado de revisão sobre
  trabalho **novo**. Onde colidem — prompt L0 —, vale a D2. Se o usuário quiser
  a F2 inteira de volta, é reverter `continua` para `return False`.
- **Anúncio do fechamento** (20, 46, 77): quando a F2 fecha a entrega, o
  CLASSIFIED da task nova não diz que a anterior virou `superseded`. O caminho
  que este ramo abre (CONTINUING da entrega) passa pelo emissor e aparece em
  `emissions.jsonl`; o `print` cru do caminho L0 fica como está.

### 8.4 Boundaries

- **ALWAYS** — uma projeção descreve uma task só; quem projeta a task escolhida
  pelo chamador passa por `projecao.projetar`.
- **NEVER** — prompt L0 fecha entrega; `projetar` tira a projeção de outra task
  viva; nível desconhecido decide fechar; teste escreve no `~/.claude/harness`
  real ou no plugin em cache.
- **ASK** — qualquer escrita no balde real do incidente; deploy para o cache
  (consequência de merge, decisão do usuário).

# Portão de Stop cobra continuação com trabalho em voo — diagnóstico

Ramo `claude/magical-shamir-b49924`, pipeline L2-bug (regex sugeriu L2-docs;
confirmação semântica corrigiu), task `t-20260930-053203156690`. Base: `main`
em `c175fd9`; avançado para `6bd2962` antes da verificação.

Leitura prévia obrigatória, e o que ela já decidiu:

- `portao-stop-sem-codigo-diagnostico.md` — código é cobrado em toda fase
  (`cobra_evidencia_nesta_fase`); docs só na fase final (D1). Não reaberto.
- `portao-escalation-inaprovavel-diagnostico.md` (ramo
  `claude/exciting-bhabha-3659cc`, em `main` desde `69bbea5`) — conserta a
  RESOLUÇÃO do `escalation`. Seu achado de passagem nº 2 é este defeito:
  "É a política de quando o Stop cobra, não a resolução do portão." Este ramo
  foi avançado para `main` em `6bd2962` antes da suíte inteira; o conserto de
  lá e o daqui convivem no mesmo `transactional_state.py` sem conflito.
- `portao-mede-atividade-verification.md` §3 — o toque mede ATIVIDADE onde
  devia medir MUDANÇA; o conserto desenhado (digesto da árvore) segue aberto
  (§7.3 de lá).

## O incidente

2026-09-29, task `t-20260929-130710180778`, balde
`master-harness-5a8ec6a2/sessions/6b215b3f-...-06d59ca1`, pipeline L1-bug
`[systematic-debugging, tdd, verify]`. Trabalho: medir tempo de suíte sob carga
real, em rodadas longas em segundo plano. Nenhum arquivo do projeto mudou.

Transcript da sessão (`great-dirac-dbb262/6b215b3f-....jsonl`) cruzado com o
`harness.db` (leitura `mode=ro`):

| Hora (UTC) | Evento | Job em voo da própria sessão |
|---|---|---|
| 13:07:31 | Bash em background, `backgroundTaskId=bzrg3z3z3` | — |
| 13:09:40 | fim de turno: "rodada 1 em segundo plano; retomo quando ela terminar" | `bzrg3z3z3` |
| 13:09:42 | **Stop cobra #1** (verification gate) | `bzrg3z3z3` |
| 13:13:11 | `<task-notification>` `bzrg3z3z3` `completed` | — |
| 13:19:09 | Bash em background, `b36bf65ya` | — |
| 13:19:42 | Monitor `blkfa974t` | — |
| 13:20:03 | fim de turno: "aguardando as rodadas terminarem" | `b36bf65ya`, `blkfa974t` |
| 13:20:05 | **Stop cobra #2** | `b36bf65ya`, `blkfa974t` |
| 13:20:39 | evento do Monitor acorda o modelo (rodada 1 de 6) | `b36bf65ya`, `blkfa974t` |
| 13:20:48 | fim de turno: "aguardando as outras 5" | `b36bf65ya`, `blkfa974t` |
| 13:20:51 | **Stop abre `escalation`** | `b36bf65ya`, `blkfa974t` |
| 13:28:40 | `<task-notification>` `b36bf65ya` `completed` | — |

As três cobranças aconteceram com job da própria sessão em voo — e em todas o
host tinha garantido reinvocar o modelo quando o job terminasse. Doze minutos
entre a primeira e a terceira. Na hora da escalada: `code_revision` 19, 19
toques, todos `shell-placeholder`; `evidence` vazia.

### O dano não parou na escalada

`escalation` põe a task em `awaiting_gate`, e `_handle_stop`
(`hooks/harness-transactional.py:1080`) só age com `status == "active"`. A
task seguiu trabalhando por mais **16 horas** — `code_revision` de 19 a 167,
três suítes `bun test` gravadas — com o portão de Stop **mudo**, porque o
`escalation` era inaprovável (o defeito do outro ramo). A escalada falsa não
foi só ruído: ela desligou o portão para o resto da task. Consertar a resolução
do `escalation` fecha a porta de saída; este conserto fecha a porta de entrada.

### O segundo caso: harness4claude, mesmo dia

A suíte do harness4claude leva ~21 min e passa do limite de 10 min da
ferramenta Bash; o host a joga para segundo plano sozinho
(`toolUseResult.timedOutAfterMs` + `backgroundTaskId`). O `PostToolUse` desse
lançamento passa por `is_trusted_verification` e grava uma linha de evidência
sem saída nenhuma:

    balde 037312e7, task t-20260929-144950366362, code_revision 21
    command="python -m pytest -q -p no:cacheprovider" exit_code=0 tests_collected=NULL

E encerrar o turno enquanto ela roda cobraria continuação, como no incidente.

## Causa raiz

**O Stop trata todo fim de turno como tentativa de resposta final.** Não é. No
Claude Code um turno também termina quando o modelo espera trabalho que ele
mesmo lançou em segundo plano — Bash/PowerShell com `run_in_background` ou
jogado para o fundo por timeout, Monitor, Workflow, Agent assíncrono. Nesses
casos o host reinvoca o modelo quando o job termina (`<task-notification>`);
cada evento de Monitor também acorda o modelo, e cada despertar termina em
outro Stop. `register_stop_continuation`
(`scripts/transactional_state.py:1233`) conta cada um como "o modelo tentou
terminar sem verificar e foi empurrado de volta".

O contador existe para detectar modelo **travado**: empurrado duas vezes e
ainda sem verificar. Um modelo esperando o próprio job não está travado — ele
não teve como verificar, porque o resultado ainda não existe.

A cobrança nasce no mesmo desenho que produz o segundo caso: o hook trata
**lançamento** como **resultado**. No `PostToolUse`, lançar a suíte em segundo
plano vira evidência (vazia); no Stop, esperar o job vira tentativa de encerrar.

### Por que nenhum teste pegou

`test_stop_blocks_twice_then_opens_escalation_gate` e vizinhos
(`tests/test_transactional_hook.py`) montam payloads de Stop sem
`transcript_path` e sem job nenhum. `test_aviso_de_background_quando_nao_ha_casos`
(`:421`) trava o AVISO do lançamento em segundo plano, e com isso travou também
a gravação da evidência vazia que o aviso descreve.

## O que o hook consegue observar

Medido em 60 transcripts recentes da máquina (`~/.claude/projects/*/*.jsonl`):

| Lançamento | Onde aparece | Id |
|---|---|---|
| Bash / PowerShell em background (pedido ou por timeout) | `toolUseResult.backgroundTaskId` | 9 chars |
| Monitor | `toolUseResult.taskId` (+ `timeoutMs`, `persistent`) | 9 chars |
| Workflow | `toolUseResult.taskId`, `status: async_launched` | 9 chars |
| Agent assíncrono | `toolUseResult.agentId`, `status: async_launched` | 17 chars |

Término: `<task-notification>` com `<task-id>ID</task-id>` e
`<status>` em `completed` (521), `failed` (35), `killed` (13), `stopped` (11).
Eventos intermediários do Monitor vêm sem `<status>`. De 153 Agents
assíncronos, 145 têm notificação terminal com `task-id == agentId`.

A notificação não é o único término, e ler só ela deixaria job morto "em voo".
Medido em 80 transcripts (script `lost.py` no scratchpad da sessão):

| Término | Forma no transcript | Sem ele |
|---|---|---|
| `TaskStop` bem-sucedido | `toolUseResult.message` = `Successfully stopped task: ID`, `toolUseResult.task_id` | **39 de 55** jobs parados não recebem notificação nenhuma |
| Monitor que expira | `<event>[Monitor expired after 30m ...` sem `<status>` | 6 Monitors de `fa9aea75` pareciam vivos |
| Sessão encerrada com job vivo | na retomada, UMA notificação com vários `<task-id>` e `<status>stopped</status>` ("didn't finish before the previous session ended") | ler só o primeiro `<task-id>` do bloco perde os outros |
| Notificação enfileirada | entrada `queue-operation` (`operation: enqueue`) com `content` | a notificação de `bbcnq2zqw` (`be6222f1`) só existe ali |

Depois de ler as quatro formas, sobra job sem término nenhum: `bh9quzdpy`
(`86459dbf`, lançado em 2026-09-10, sessão seguiu até 09-24). Existe, é raro,
e não tem cota — Bash em background não tem prazo. Por isso a forma (a1)
abaixo está fora: um job perdido calaria o portão por duas semanas. O Monitor
tem prazo declarado pelo próprio host (`timeoutMs` no lançamento) e ele entra
como término: passado `lançamento + timeoutMs`, o Monitor não está em voo.

Canais descartados, com o motivo medido:

- **Evento de hook dedicado** — não existe. `TaskCreated`/`TaskCompleted` são
  do `TaskCreate` (lista de tarefas), não de job em segundo plano
  (https://code.claude.com/docs/en/hooks).
- **`UserPromptSubmit` recebendo a notificação** — acontece
  (`harness-classify.sh:363`, `test_37`), mas **42 de 582** notificações
  terminais (7,2%) não tiveram `UserPromptSubmit` nos 15 min seguintes: as
  que o host injeta no meio de um turno. Um job cujo fim se perde fica "em voo"
  para sempre.
- **`PostToolUse` do lançamento** — o hook transacional só está registrado
  para `Bash|PowerShell` (`hooks/hooks.json`); Monitor, Workflow e Agent nunca
  chegam nele.

O que resta e cobre tudo: o **transcript**, cujo caminho vem no payload do Stop
(`transcript_path`, documentado). Lançamento e término estão lá, na mesma
fonte, na ordem em que o host os registrou.

## As regras candidatas, julgadas

| Candidata | Veredito | Por quê |
|---|---|---|
| (a) job da própria sessão em voo → não cobra | **adotar** | É a causa: o fim de turno não é tentativa de encerrar, e o host garante o despertar. Cobre as 3 cobranças do incidente. |
| (b) `code_revision` igual desde o último bloqueio → não cobra | rejeitar | Contradiz o propósito do contador. "Empurrado, continuou, parou de novo sem mudar nada" é exatamente o modelo travado que a escalada existe para trazer ao humano. No incidente, entre a cobrança 2 e a 3 a revisão não mudou — mas porque havia job em voo, que (a) já cobre. |
| (c) só toques `shell-placeholder` → não cobra | rejeitar | Placeholder é "não sei se escreveu", não "não escreveu": `python - <<PY ... write_text`, `npm run format`, `git apply` são escrita real sem caminho atribuível. Isentar enfraquece o portão para mudança real de código. O defeito verdadeiro está uma camada abaixo (toque mede atividade, não mudança) e tem conserto desenhado: `portao-mede-atividade-verification.md` §3. |

### Bloquear ou só não cobrar, com job em voo

A regra (a) tem duas formas:

- **(a1) não bloqueia e não cobra.** Zero ruído enquanto espera. Mas um job
  que não termina nunca — servidor de desenvolvimento em background, Monitor
  `persistent`, ou uma notificação terminal que o transcript não registrou —
  cala o portão até o fim da sessão. "Editei, subi o `npm run dev`, pronto"
  passaria sem teste. **Enfraquece o portão para mudança real.**
- **(a2) bloqueia, mas não cobra.** O bloqueio de hoje fica intacto em todo
  fim de turno não verificado; só a contagem para a escalada é suspensa
  enquanto há job em voo. O custo é uma continuação curta por despertar, e a
  mensagem diz que aquele Stop não foi cobrado e qual job está em voo. Job que
  nunca termina adia a escalada, nunca o bloqueio.

Recomendação: **(a2)**. A escalada falsa é o dano; o bloqueio é a função.

## Correção proposta

1. **`jobs_em_voo(transcript_path, agora)`** — função pura sobre o transcript:
   lançamentos lidos de `toolUseResult` (`backgroundTaskId`; `taskId` de
   Monitor não-persistente e de Workflow; `agentId` com `async_launched`)
   menos os terminados pelas quatro formas da tabela acima: bloco
   `<task-notification>` com qualquer `<status>` encerra TODOS os `<task-id>`
   do bloco, em qualquer tipo de entrada; evento `Monitor expired`; `TaskStop`
   bem-sucedido; Monitor com `lançamento + timeoutMs` no passado. Status
   desconhecido conta como término: errar para esse lado cobra, que é o
   comportamento de hoje. Pré-filtro por substring antes do `json.loads`,
   entradas `isSidechain` fora. Ilegível ou ausente → lista vazia, ou seja, o
   comportamento de hoje (fail-closed).
   Só entram lançamentos com carimbo de tempo posterior a `tasks.started_at`
   (D2).
2. **`register_stop_continuation(..., em_voo=[...])`** — com `em_voo` não
   vazio, não incrementa `stop_continuations`, não abre `escalation`, e grava
   o evento `stop_nao_cobrado` com os ids (D3). A decisão de contar continua no
   banco; o hook só entrega a lista.
3. **Mensagem do bloqueio** — com job em voo, diz que este Stop não foi
   cobrado, nomeia os ids, e que a evidência continua exigida antes da
   resposta final. Toda mensagem do portão, escalada incluída, mostra quantos
   Stops desta task não foram cobrados (D3).
4. **Lançamento não é resultado** — no `PostToolUse`, resposta com
   `backgroundTaskId` não grava evidência; o aviso passa a dizer que a suíte
   foi para segundo plano e como registrar a evidência quando ela terminar.
   O toque (`shell-placeholder`) continua: o job pode escrever.

Fora do escopo:

- captura automática da evidência quando a suíte em segundo plano termina
  (exige gravar na `code_revision` do lançamento, não na corrente) — ramo
  próprio (D4);
- job em segundo plano que escreve na árvore depois de a evidência ser
  gravada, sem toque nenhum (grill #52): lacuna anterior a este conserto, do
  mesmo tipo que `shell-placeholder` — escrita que o hook não vê;
- o digesto de árvore para `shell-placeholder` (`portao-mede-atividade` §3);
- o caminho relativo resolvido contra o `cwd` do payload e não contra o `cd`
  do comando (`cd $SP && cat > resumir.py` virou toque "dentro da raiz" no
  incidente).

## Falsificação

- **Metade 1, o defeito:** o incidente reconstruído — transcript com Bash em
  background sem notificação terminal, Stop três vezes. Hoje abre
  `escalation`; depois, `stop_continuations` parado e nenhum portão.
- **Metade 2, o que não pode mudar:**
  - sem job em voo, o mesmo roteiro continua abrindo `escalation` na terceira;
  - job **terminado** (notificação `completed`, `failed`, `killed`,
    `stopped`) não isenta;
  - Monitor `persistent` não isenta;
  - com job em voo, o Stop **continua bloqueando** (só não cobra);
  - transcript ausente ou corrompido → cobra como hoje.
- **Lançamento:** `PostToolUse` com `backgroundTaskId` não grava linha em
  `evidence`; `pytest` em primeiro plano continua gravando.
- **Medida com a função de produção:** `jobs_em_voo` sobre o transcript real
  do incidente, cortado em cada um dos três Stops, devolve job em voo nos três.

## Decisões (grill-me, rodada 1, usuário em 2026-09-30)

- **D1 — bloqueia, não cobra.** Com job em voo o Stop bloqueia como hoje; só a
  contagem para a escalada é suspensa. Job que nunca termina adia a escalada,
  nunca o bloqueio.
- **D2 — só jobs da task atual.** Isenta apenas job lançado depois de
  `tasks.started_at`. O contador é por task; um servidor subido numa task
  anterior não isenta a seguinte.
- **D3 — sem teto numérico, contagem visível.** Cada Stop não cobrado vira uma
  linha em `events` (`stop_nao_cobrado`, com os ids). O consumidor é a própria
  mensagem do portão: ela mostra quantos Stops desta task não foram cobrados e
  por quais jobs, também na mensagem de escalada.
- **D4 — captura automática da evidência em segundo plano vai para ramo
  próprio.** Aqui o lançamento só deixa de gravar a linha vazia e o aviso passa
  a imprimir a receita manual. Consumidor nomeado do ramo novo: a suíte de
  ~21 min do harness4claude.

## Grill-me, rodada 1 — o que fechou por medição ou por código

`wf-grill` sobre este documento, contexto limpo: 57 perguntas, 5 de 5 lentes
vivas, 7 bloqueantes. As que se respondem sem decisão humana:

1. **Laço de bloqueio com job em voo (bloqueantes 1 e 5).** Não existe. O
   Stop que encerra a continuação forçada chega com `stop_hook_active`, e
   `handle_payload` devolve vazio antes de abrir o banco
   (`hooks/harness-transactional.py:1263`). Um bloqueio por despertar, no
   máximo — é o comportamento de hoje, que a regra não muda.
2. **"O item 4 tira o caminho de evidência" (bloqueantes 2, 4, 6, 7).** Não
   tira: a linha vazia do lançamento nunca verificou. A régua exige
   `tests_collected > 0` (`REGRA_EVIDENCIA_VALIDA`,
   `scripts/transactional_state.py:56`); `NULL` reprova, `verified` vai a 0 e
   o contador não zera. O caminho para suíte em segundo plano é o mesmo antes e
   depois: o modelo lê a saída e registra com a receita `state_cli evidence`
   que o próprio portão imprime (`comando_de_evidencia`). O conserto só deixa
   de gravar uma linha que não prova nada e passa a dizer isso.
3. **O payload do `PostToolUse` é o `toolUseResult` do transcript?** É. A
   linha do caso nº 2 (balde `037312e7`) tem `output_hash`
   `d67f82e8...f4455`; o `_response_text` de produção aplicado ao
   `toolUseResult` do mesmo lançamento no transcript dá o mesmo sha256. Mesmo
   objeto, mesma ordem de chaves, no caso empurrado por timeout
   (`timedOutAfterMs: 600000` + `backgroundTaskId: b8jmxvjka`). O `exit_code=0`
   daquela linha é o padrão de `_exit_code` para `PostToolUse` sem código
   explícito (`:784`).
4. **Custo de ler o transcript a cada Stop.** Maior transcript da máquina:
   129 MB, 67 131 linhas, 632 candidatas depois do pré-filtro por substring;
   leitura + `json.loads` das candidatas em 1,3 s. Timeout do hook de Stop:
   15 s (`hooks/hooks.json`). Estouro faria o host soltar o Stop — por isso o
   pré-filtro não é otimização, é requisito.
5. **`SubagentStop` chega em `_handle_stop`?** Não: `SubagentStop` vai só para
   `harness-lifecycle.py`, e `handle_payload` só despacha `name == "Stop"`.
6. **Contador acumulado antes do voo.** Fica congelado, não zera. Zerar no fim
   do job abriria o laço "lança, espera, para" que nunca chega a 2 — o modelo
   travado que a escalada existe para trazer ao humano.
7. **Notificação citada** (ler esta spec, `grep` num `.jsonl`) pode marcar um
   id como terminado. O erro vai para o lado que cobra — o de hoje. Lançamento
   só vem de chave de primeiro nível de `toolUseResult`, nunca de texto.
8. **Linha quebrada** (host ainda gravando) é descartada sozinha, como em
   `branch_sensor._assistant_text`. Se era a notificação terminal, o job
   parece em voo naquele Stop: não cobra uma vez, e bloqueia igual.
9. **Estado já danificado.** Nenhuma linha vazia verificou task (item 2). As
   tasks presas em `awaiting_gate` por escalada falsa saem pela aprovação do
   `escalation`, que é o conserto do outro ramo. Histórico não é reescrito.
10. **Números.** Os 580 da contagem por status (521+35+13+11) e os 582 da
    medição do `UserPromptSubmit` vêm de duas varreduras com pareamento
    diferente de `<task-id>`/`<status>`; a segunda pareava cada id de um bloco
    de órfãos. A conclusão (7,2% sem `UserPromptSubmit`) não muda.
11. **Implantação.** Os hooks rodam do plugin instalado, não do worktree. O
    merge não implanta; atualizar o plugin é passo separado, depois do merge.

## Contexto estrutural (fase graph-context)

`graphify-out/graph.json` gerado em 2026-09-30 02:32 sobre este worktree, depois
do `HEAD` (`c175fd9`, 2026-09-28) — conferido. `graphify query` + `Grep`:

- `register_stop_continuation`: um único chamador de produção,
  `_handle_stop` (`hooks/harness-transactional.py:1087`). Testes:
  `test_transactional_hook.py`, `test_ciclo_de_vida_da_task.py`,
  `test_portao_de_docs.py`.
- `stop_continuations`: escrito só em `register_stop_continuation` e zerado
  só em `record_evidence` (evidência válida); projetado por `_sync_projection`.
  O ramo `exciting-bhabha-3659cc` acrescenta o zero na aprovação do
  `escalation` (`resolve_gate`) — função diferente, sem sobreposição de linha.
- `contract/` não menciona continuação nem `escalation` do Stop: a mudança não
  toca a superfície compartilhada com o harness4codex.
- Precedente de leitura do transcript num hook de Stop:
  `branch_sensor._assistant_text` (`scripts/branch_sensor.py:508`) — lê o
  arquivo, tolera linha quebrada, devolve vazio se não der.
- `_handle_post_tool` recebe só `Bash|PowerShell` (`hooks/hooks.json`); o
  lançamento de Monitor/Workflow/Agent não passa por ele, o que confirma o
  transcript como fonte única.

# Spec: evidência automática de suíte em segundo plano

**Status**: Grilled (rodada 1) — aguardando `approve-spec`
**Created**: 2026-09-30
**Updated**: 2026-09-30
**Branch**: `claude/vibrant-montalcini-9868a6` (base `main` em `d3fc851`)
**Task**: `t-20260930-135548871810` (L2-feature)
**Author**: AI-generated, reviewed by Leonardo
**Constrangida por**: `docs/CONTEXT.md`, bloco "evidência automática de suíte em segundo plano" (L-01..L-09)
**Depende de**: ramo `claude/magical-shamir-b49924` — **em `main` desde `d3fc851`** (L-08 cumprido; fast-forward feito em 2026-09-30)

---

## Executive Summary

O portão do harness só aceita como evidência de teste uma suíte rodada em
primeiro plano: o hook lê a saída na resposta da ferramenta. Uma suíte que vai
para segundo plano — pedida, ou empurrada pelo host depois de 10 min de Bash —
termina sem evidência, e o modelo precisa ler a saída e registrá-la à mão. A
suíte completa do harness4claude leva ~21 min e por isso nunca se autoverifica.
Esta feature registra o lançamento no `harness.db`, reconhece o término no
transcript da sessão e grava a evidência **na revisão de código em que a suíte
foi lançada**: se nada mudou desde então, a task fica verificada sozinha; se
mudou, o resultado fica como histórico e não verifica nada.

## Context

### O que já decide o desenho

- **D4** de `docs/specs/portao-stop-em-voo-diagnostico.md`: o `PostToolUse` de
  uma verificação confiável cuja resposta tem `backgroundTaskId` não grava
  evidência e imprime `AVISO_SEGUNDO_PLANO` com a receita manual. Esta spec
  parte daí.
- **L-09** do `CONTEXT.md`: a captura roda onde `verified` é decidido — Stop e
  `complete`.

### Files/Modules Impactados

Linhas em `main` `d3fc851`.

- `hooks/harness-transactional.py`
  - `_handle_post_tool` (`:1310`): o ramo `_job_em_segundo_plano` (`:1370`)
    hoje só imprime o aviso; passa a registrar o lançamento.
  - `_handle_stop` (`:1403`): passa a capturar antes de decidir o bloqueio.
  - `_motivo_do_gate` (`:1520`): passa a listar os lançamentos.
  - `_test_counts` (`:875`) e `_soma_categorias` (`:866`): a lógica de
    contagem passa a ser usada também sobre o texto do arquivo de saída.
  - `AVISO_SEGUNDO_PLANO` (`:219`): anuncia a captura automática.
- `scripts/transactional_state.py`
  - `_ensure_schema` (`:156`): tabela nova de lançamentos.
  - `record_evidence` (`:1290`): hoje grava sempre em `row["code_revision"]` e
    sempre mexe em `verified`; passa a aceitar a revisão do lançamento.
  - `complete` (`:1465`): captura antes de recusar por falta de evidência.
- `scripts/trabalho_em_voo.py`: leitura do transcript com pré-filtro por
  substring; primitivas `_BLOCO`, `_TASK_ID`, `_textos`, `_instante`.
- `scripts/state_cli.py` (`:151`, `:166`): chamadores de produção de
  `record_evidence` e `complete`. **Não ganha parâmetro de revisão.**

Chamadores de produção medidos (`graphify update .` sobre `18ec682`, 6254 nós;
+ `Grep` excluindo `tests/`): `record_evidence` ← hook e `state_cli.py:151`;
`complete` ← `state_cli.py:166`; `_test_counts` ← hook. Nenhum outro.

### Dependências

- Host Claude Code: `tool_response.backgroundTaskId` no lançamento (pedido e
  empurrado por timeout); bloco `<task-notification>` no transcript; arquivo
  `<output-file>` em `%TEMP%\claude\<projeto>\<sessão>\tasks\<id>.output`.
  Formatos internos do host, não documentados — a doc oficial é silenciosa
  sobre `backgroundTaskId` e sobre evento de término (conferido 2026-09-30).
- `transcript_path` e `tool_use_id` no payload do `PostToolUse`/
  `PostToolUseFailure`; `transcript_path` no do Stop — documentados em
  https://code.claude.com/docs/en/hooks.

### Medição que sustenta a spec (2026-09-30)

Instrumento: `scratchpad/medir_bg.py` desta sessão, sobre **3570 transcripts**
principais de `~/.claude/projects`, **680 lançamentos** Bash/PowerShell com
`toolUseResult.backgroundTaskId`. O instrumento errou uma vez e foi corrigido
antes da leitura: a primeira versão só procurava `origin` no nível de cima da
entrada e contou 281 notificações com marcador do host; o marcador também vem
aninhado em `attachment.origin`, e o número correto é 301.

| Pergunta | Resultado |
|---|---|
| Lançamento com notificação terminal (`<status>` presente) | 619 de 680 (91%); 61 sem notificação nenhuma |
| Status terminal | `completed` 541 · `failed` 63 · `stopped` 11 · `killed` 2 · `completed\|stopped` 2 |
| `<tool-use-id>` da notificação = `tool_use_id` do lançamento | 617 confere · 1 diverge · 1 ausente |
| Notificação terminal com `origin.kind == "task-notification"` | 301 de 619 (48,6%) |
| Formas da entrada que carrega a notificação | `queue-operation/enqueue` 615 · `queue-operation/remove` 321 · `attachment/queued_command` 337 (20 com marcador) · `user` 282 (281 com marcador) · `assistant` 1 |
| Arquivo de saída existe hoje | 231 existe · 388 sumiu |
| Retenção por idade do lançamento | < 6 h: 15 existe, 0 sumiu · < 24 h: 46 / 1 · < 48 h: 112 / 3 · sumido mais novo: 23,6 h |
| Código de saída: resumo × trailer `[exited with code N]` do arquivo | 226 concorda · **0 diverge** · 4 sem trailer · 1 só no trailer |
| Resumo sem código de saída | "No completion record was found…" 10 · "didn't finish before the previous session ended" 3 · "was stopped" 2 |
| Resumo `completed` com código ≠ 0 | existe: "completed (exit code 1: No matches found)" |

Caso real do consumidor: `b8jmxvjka` (balde `037312e7`, 2026-09-29), comando
`python -m pytest -q -p no:cacheprovider` (passa em `is_trusted_verification`),
empurrado pelo timeout (`timedOutAfterMs: 600000`, `stdout: ""`); notificação
21 min depois como `attachment/queued_command` com marcador do host,
`<summary>Background command "Run the full test suite" failed with exit code 1</summary>`;
arquivo de 4706 bytes terminando em `2 failed, 1610 passed, 1 skipped, 6
subtests passed in 1248.39s` e `[exited with code 1]`. **Insumo guardado** em
2026-09-30 (~21 h de idade, antes do corte de ~24 h): arquivo inteiro (sha256
`a8f1ad1f…f8f3`) e as 7 linhas do transcript que citam o job, em
`scratchpad/insumo-b8jmxvjka/` desta sessão; o design decide a forma de fixture.

---

## Glossário (fecha as ambiguidades do grill)

- **Lançamento**: linha da tabela nova. **Estado** ∈ {`pendente`, `capturado`,
  `historico`, `superado`, `rejeitado`}; **motivo** é coluna separada, texto
  curto (`sem-codigo`, `sem-arquivo`, `arquivo-estranho`, `codigo-diverge`,
  `notificacao-diverge`, `status-inconsistente`). Nos AC, `rejeitado:X` quer
  dizer estado `rejeitado`, motivo `X`.
- **Revisão**: `code_revision` da task. Contador monotônico — só
  `touch_files` a muda, sempre `+1` (`transactional_state.py:1239`). Qualquer
  toque conta (`.md`, `shell-placeholder`), não só código.
- **Revisão do lançamento (N')**: a revisão lida logo depois do toque do
  próprio comando de lançamento, no mesmo `PostToolUse`.
- **Término**: carimbo de tempo (`timestamp`) da entrada do transcript que traz
  a primeira notificação terminal aceita de J.
- **Notificação aceita**: bloco `<task-notification>` com `<status>`, cujo
  `<task-id>` = J e `<tool-use-id>` = o do lançamento, contido numa entrada de
  forma do host (ASSUMPTION-007).
- **"Hoje"**: `main` em `d3fc851`.

---

## User Stories

### US-1: Suíte verde em segundo plano verifica sozinha (Priority: P1) — MVP

**Como** modelo trabalhando numa task do harness
**Quero** que a suíte que lancei e que foi para segundo plano vire evidência quando termina
**Para que** a suíte de ~21 min do harness4claude verifique a task sem registro à mão

**Why this priority**: é o consumidor nomeado (L-01) e a metade 1 da falsificação (L-07).

**Independence**: testável sozinha com transcript e arquivo de saída sintéticos.

**Acceptance Criteria**:

- **AC-1.1**: Given uma task ativa e um `PostToolUse` de `python -m pytest -q`
  cuja resposta tem `backgroundTaskId=J`, `tool_use_id=U` e `transcript_path=T`,
  When o hook processa esse `PostToolUse`, Then existe um lançamento `pendente`
  de J com comando, U, T e N' = a revisão logo depois do toque do próprio
  lançamento, e nenhuma linha nova em `evidence`.
- **AC-1.2**: Given o lançamento pendente de J em N', a revisão corrente ainda
  N', e T com notificação aceita `completed` de J, resumo `(exit code 0)`,
  apontando `…\<sessão>\tasks\J.output` que termina em `1610 passed, 1
  skipped`, When o Stop roda, Then existe evidência `test` em N' com
  `exit_code=0`, `tests_collected=1611`, `tests_passed=1610`,
  `tests_skipped=1`, `verified=1`, `stop_continuations=0`, o lançamento fica
  `capturado` apontando a evidência, e o Stop não bloqueia.
- **AC-1.3**: Given o mesmo cenário, When o modelo roda `state_cli complete`
  antes de qualquer Stop, Then a captura acontece dentro do `complete` e a task
  fecha `done` (na fase final).
- **AC-1.4**: Given a captura já feita, When o Stop ou o `complete` rodam de
  novo, Then nenhuma linha nova em `evidence` nem em `events`.
- **AC-1.5** (P2, condicionado à medição de PowerShell no design): Given
  lançamento feito por PowerShell com `backgroundTaskId`, When capturado, Then
  o comportamento é o de AC-1.2.
- **AC-1.6**: Given o mesmo `PostToolUse` de J processado duas vezes, When o
  hook roda na segunda, Then continua existindo um lançamento só de J.

**Edge Cases**:
- Lançamento pedido (`run_in_background`) e empurrado por timeout
  (`timedOutAfterMs`) seguem o mesmo caminho.
- Dois lançamentos pendentes na mesma task: processados em ordem de término
  (REQ-F10), cada um na própria revisão.

---

### US-2: Revisão mudou desde o lançamento — histórico, não verificação (Priority: P1) — MVP

**Como** portão do harness
**Quero** que o resultado de uma suíte lançada antes de uma mudança não verifique o código mudado
**Para que** evidência velha nunca passe por fresca

**Why this priority**: metade 2 da falsificação (L-07); sem ela a feature enfraquece o portão.

**Acceptance Criteria**:

- **AC-2.1**: Given lançamento pendente em N' e um toque posterior que subiu a
  revisão para N'+1, e T com término `completed (exit code 0)` e saída verde,
  When o Stop roda, Then existe evidência em **N'** (não em N'+1), `verified`
  continua 0, `stop_continuations` não zera, o lançamento fica `historico`, e
  o Stop bloqueia como hoje.
- **AC-2.2**: Given `verified=1` por suíte em primeiro plano em N'+1 e um
  lançamento pendente em N' que termina vermelho, When capturado, Then a linha
  vermelha vai para N' e `verified` continua 1.
- **AC-2.3**: Given `record_evidence` chamado sem revisão explícita (hook em
  primeiro plano, `state_cli evidence`), When grava, Then o comportamento é o
  de hoje.
- **AC-2.4**: Given lançamento `historico` em N' e a revisão corrente em N'+1,
  When o Stop roda em qualquer momento depois, Then a linha de N' nunca decide
  `verified` — a revisão não volta a N' (contador monotônico).

---

### US-3: Sem prova, não verifica (Priority: P1) — MVP

**Como** portão do harness
**Quero** que toda dúvida sobre o término ou a saída resulte em "não capturado"
**Para que** a captura automática nunca seja mais fraca que a suíte em primeiro plano

**Why this priority**: direção de erro obrigatória (Constraints do `CONTEXT.md`).

**Acceptance Criteria** — Given lançamento pendente de J em N' = revisão
corrente, When o Stop roda, Then nenhuma evidência com `verified=1` nasce, o
lançamento fica no estado indicado, e o Stop decide como hoje:

- **AC-3.1**: sem notificação aceita de J → `pendente`.
- **AC-3.2**: notificação aceita com `<status>` fora de {`completed`,
  `failed`}, ou resumo sem código de saída ("No completion record…", "didn't
  finish before the previous session ended", "was stopped") →
  `rejeitado:sem-codigo`.
- **AC-3.3**: `failed with exit code 1` e saída `2 failed, 1610 passed` →
  evidência em N' com `exit_code=1`, `verified=0`, `capturado` (o mesmo que o
  primeiro plano faria; `stop_continuations` não zera).
- **AC-3.4**: arquivo de saída inexistente → `rejeitado:sem-arquivo`, nenhuma
  evidência.
- **AC-3.5**: trailer `[exited with code M]` do arquivo com M ≠ código do
  resumo → `rejeitado:codigo-diverge`, nenhuma evidência.
- **AC-3.6**: `<tool-use-id>` da notificação diferente do lançamento, ou
  lançamento com `tool_use_id` nulo → notificação não aceita; `pendente`.
- **AC-3.7**: notificação de J só dentro de entrada `assistant`, de
  `toolUseResult` (ex.: `cat` de um `.jsonl`), de subagente (`isSidechain`),
  de `queue-operation`, de `attachment` que não seja `queued_command` com
  `commandMode == "task-notification"`, ou de `user` sem `origin.kind ==
  "task-notification"` → não aceita; `pendente`.
- **AC-3.8**: `<output-file>` cujo nome não é `J.output`, ou cujo diretório não
  é `…\<sessão de T>\tasks` → `rejeitado:arquivo-estranho`.
- **AC-3.9**: saída sem contagem reconhecível (ex.: `no tests ran`) →
  evidência em N' com `tests_collected` 0 ou `NULL`, `verified=0`, `capturado`.
- **AC-3.10**: transcript ausente ou ilegível, arquivo existente mas sem
  permissão de leitura, ou exceção qualquer → hook não cai, Stop e `complete`
  decidem como hoje, lançamento continua `pendente` (tenta de novo no próximo).
- **AC-3.11**: duas notificações aceitas de J que discordam em status ou código
  → `rejeitado:notificacao-diverge`. Cópias idênticas do mesmo bloco (enqueue,
  remove, attachment, user) concordam e contam como uma.
- **AC-3.12**: status `failed` com código 0 no resumo →
  `rejeitado:status-inconsistente`. (`completed` com código ≠ 0 é aceito: é
  forma medida, ver tabela.)

---

### US-4: A execução que terminou por último decide (Priority: P1) — MVP

**Como** portão do harness
**Quero** que uma captura atrasada nunca passe por cima de uma execução que terminou depois dela na mesma revisão
**Para que** a feature nunca deixe o portão mais fraco que o primeiro plano (grill #4)

**Why this priority**: sem ela, verde velho cobre vermelho novo — o único caso
em que a feature enfraqueceria o portão.

**Acceptance Criteria**:
- **AC-4.1**: Given suíte verde lançada em N' com término em t1, e evidência
  vermelha em primeiro plano gravada em N' com `created_at` t2 > t1, When a
  verde é capturada, Then nenhuma linha nova em `evidence`, o lançamento fica
  `superado`, e `verified` continua 0.
- **AC-4.2**: Given evidência verde em N' gravada em t0 < t1 e captura
  vermelha com término t1, When capturada, Then a vermelha entra em N' e
  `verified` vai a 0.
- **AC-4.3**: Given J1 (vermelho, término t2) e J2 (verde, término t1 < t2)
  pendentes em N', When o mesmo Stop captura os dois, Then J2 é gravado antes
  de J1 e `verified` termina 0.

---

### US-5: O portão diz o que aconteceu com cada lançamento (Priority: P2)

**Como** modelo (e humano) lendo a mensagem do portão
**Quero** ver os lançamentos desta task e o destino de cada um
**Para que** eu saiba se espero, registro à mão ou relanço — e para que mudança de formato do host apareça como pendente acumulado, não como silêncio

**Acceptance Criteria**:
- **AC-5.1**: Given lançamentos desta task, When o Stop bloqueia ou o
  `complete` recusa, Then a mensagem lista cada um com id, N', estado e motivo.
- **AC-5.2**: Given um lançamento resolvido, When a resolução acontece, Then há
  uma linha em `events` (`lancamento_resolvido`) com id, estado e motivo, na
  mesma transação da evidência.
- **AC-5.3**: Given o lançamento, When o `PostToolUse` o registra, Then o aviso
  diz que a evidência será capturada no término, na revisão N', que qualquer
  toque antes disso a torna histórico, e que a receita manual só vale se nada
  mudou desde o lançamento.

---

## Current System

### Entry Points
- `PostToolUse`/`PostToolUseFailure` de `Bash|PowerShell` → `_handle_post_tool`.
- `Stop` → `_handle_stop`.
- `state_cli.py evidence|complete` → `HarnessDatabase.record_evidence|complete`.

### Data Flow (hoje, `d3fc851`)

```
Bash pytest (fg) --PostToolUse--> touch (rev N') --> _test_counts(resposta) --> record_evidence(rev corrente)
Bash pytest (bg) --PostToolUse--> touch (rev N') --> AVISO_SEGUNDO_PLANO (receita manual) --> [nada gravado]
... 21 min ...  host grava <task-notification> no transcript e reinvoca o modelo
modelo lê saída --> state_cli evidence (à mão, na revisão CORRENTE)
Stop --> jobs_em_voo(transcript) --> register_stop_continuation(em_voo) --> bloqueia se verified=0
```

### Constraints Existentes
- `REGRA_EVIDENCIA_VALIDA` (`transactional_state.py:60`): exit 0, collected > 0,
  passed > 0, passed + skipped == collected.
- `_has_fresh_evidence`: a última linha (maior `id`) do tipo exigido na revisão
  corrente decide. US-4 garante que a captura nunca vira a maior `id` por atraso.
- Timeout do hook de Stop: 15 s; maior transcript medido: 129 MB (1,3 s com
  pré-filtro), e o Stop já faz essa leitura para `jobs_em_voo`.
- `continuation_policy.task_viva` é somente-leitura por contrato.
- Balde por sessão: sessão nova, banco novo.

---

## Requirements

### Functional

- [ ] **REQ-F1**: O `harness.db` guarda cada lançamento em segundo plano de
  verificação confiável: task, id do job, `tool_use_id`, comando,
  `transcript_path`, N', instante do lançamento, estado, motivo, instante do
  término, instante da resolução, id da evidência. Único por (task, job).
  Migração no padrão de `_ensure_schema`, sem tocar linhas de outras tabelas.
  consumidor: REQ-F3 (captura) e REQ-F8 (mensagem do portão). [traces: US-1, US-2, US-5]
- [ ] **REQ-F2**: O `PostToolUse`/`PostToolUseFailure` de comando que passa em
  `is_trusted_verification` e cuja resposta tem job em segundo plano
  (`_job_em_segundo_plano`) grava o lançamento DEPOIS do toque do próprio
  comando, com a revisão resultante, na task do contexto do hook. Não grava
  `evidence`. Repetido, não duplica.
  consumidor: hook em produção (`hooks/hooks.json`, matcher `Bash|PowerShell`). [traces: US-1]
- [ ] **REQ-F3**: Uma função de captura resolve os lançamentos `pendente` DE
  UMA task (a do Stop, ou a do `complete`), usando só o banco e o transcript
  gravado em cada linha: acha as notificações aceitas de J, aplica REQ-F4,
  REQ-F9, REQ-F10 e grava pela REQ-F6. Uma transação por lançamento: evidência,
  estado e evento juntos; a transição sai de `pendente` só uma vez (UPDATE
  condicionado ao estado).
  consumidor: REQ-F7 (Stop e `complete`). [traces: US-1..US-4]
- [ ] **REQ-F4**: Código de saída vem do resumo — `(exit code N` e `with exit
  code N`. Status tem de ser `completed` ou `failed`; `failed` com 0 rejeita.
  Trailer do arquivo presente e diferente, rejeita. Sem código, rejeita.
  [traces: US-1, US-3]
- [ ] **REQ-F5**: Contagens saem do texto do arquivo de saída pela MESMA função
  que conta a resposta em primeiro plano: a lógica de `_test_counts` vira uma
  função sobre texto, e `_test_counts` passa a chamá-la. Nenhuma cópia.
  consumidor: `_test_counts` (hook) e REQ-F3. [traces: US-1, US-3]
- [ ] **REQ-F6**: `record_evidence` aceita a revisão em que a evidência vale.
  Omitida ou igual à corrente: comportamento de hoje. Diferente da corrente:
  grava a linha nessa revisão e NÃO altera `verified`, o `status` da task nem
  `stop_continuations`. O parâmetro não é exposto no `state_cli`.
  consumidor: REQ-F3. [traces: US-2]
- [ ] **REQ-F7**: O Stop roda a captura antes de `jobs_em_voo` e do bloqueio; o
  `complete` roda a captura antes de conferir `verified`. Sem lançamento
  pendente na task, a captura é uma consulta e nada mais. Falha na captura
  nunca muda a decisão do Stop nem do `complete` em relação a hoje.
  [traces: US-1]
- [ ] **REQ-F8**: A mensagem do Stop que bloqueia e a recusa do `complete`
  listam os lançamentos da task com id, N', estado e motivo. Cada resolução
  grava `events.lancamento_resolvido`. `AVISO_SEGUNDO_PLANO` anuncia a captura
  automática, a revisão N' e a condição da receita manual. [traces: US-5]
- [ ] **REQ-F9**: `<output-file>` tem nome `J.output` e fica num diretório
  `tasks` cujo pai tem o nome da sessão de T (o nome do arquivo de transcript
  sem `.jsonl`); fora disso, rejeita. Lê no máximo a cauda (teto no design) —
  o sumário do pytest e o trailer estão no fim. [traces: US-3]
- [ ] **REQ-F10**: Ordem de término. Vários pendentes com notificação aceita são
  processados em ordem crescente de término. Uma captura cujo término é
  anterior ao `created_at` de alguma evidência do mesmo tipo já gravada na
  mesma revisão N' não grava evidência: estado `superado`. [traces: US-4]

### Non-Functional

- [ ] **REQ-NF1 (Performance)**: sem pendente, 1 consulta SQL. Com pendente,
  uma leitura por `transcript_path` distinto, com o mesmo pré-filtro por
  substring de `trabalho_em_voo`, e uma leitura de cauda por arquivo; dentro dos
  15 s do Stop no maior transcript medido, somada à leitura que `jobs_em_voo`
  já faz.
- [ ] **REQ-NF2 (Direção do erro)**: erro de leitura ou exceção → `pendente`
  (tenta de novo); prova ausente ou inconsistente → `rejeitado`; nunca
  `capturado` com prova incompleta. Hook e `complete` nunca caem por causa da
  captura.
- [ ] **REQ-NF3 (Observabilidade)**: todo estado final tem motivo no banco e em
  `events`; pendente acumulado aparece na mensagem do portão (é o alarme para
  mudança de formato do host).

---

## Boundaries

### ALWAYS
- Gravar a evidência capturada na revisão do LANÇAMENTO (L-04).
- Contar com a função de produção compartilhada com o primeiro plano (L-05).
- Casar notificação por `<task-id>` E `<tool-use-id>`, em entrada de forma do host.
- Capturar só lançamentos da task sobre a qual o chamador decide.
- Processar em ordem de término; captura atrasada não passa por cima (REQ-F10).
- Degradar para "não capturado" em qualquer dúvida (REQ-NF2).
- Rodar `state_cli` em comando atômico nos testes e receitas.

### NEVER
- Ler lançamento de texto livre: só de `tool_response` do `PostToolUse` de
  verificação confiável.
- Aceitar notificação fora das duas formas de ASSUMPTION-007.
- Deixar evidência histórica mexer em `verified`, no status da task ou em
  `stop_continuations`.
- Expor a revisão explícita no `state_cli` ou em qualquer entrada que o modelo acione.
- Alterar ou reclassificar linhas já gravadas em `evidence`.
- Mudar `REGRA_EVIDENCIA_VALIDA` ou `is_trusted_verification`.
- Editar `contract/schemas/` (R4 do `CONTEXT.md`) ou o caminho do classificador
  (`harness-classify.sh`, `classify_prompt.py`, `continuation_policy.py`).
- Escrever em `continuation_policy.task_viva`.
- Deploy sem OK do usuário.

### ASK
- Se a medição de PowerShell (design) mostrar formato diferente do Bash.
- Se a medição de retenção mudar de ordem de grandeza (arquivo sumindo em
  minutos, não em dias).
- Se o teto de cauda (REQ-F9) cortar o sumário em algum caso real medido.

---

## Nível de garantia

**Esta feature entrega:** evidência de teste gravada automaticamente, na revisão
de código em que a suíte foi lançada, para verificação confiável que o Bash ou o
PowerShell desta task mandou para segundo plano e cujo término o host gravou no
transcript da própria sessão.

**E não cobre:**
- **Escrita concorrente dentro da janela do lançamento** — chamada paralela na
  mesma mensagem, outra sessão no mesmo worktree, processo fora dos hooks: se
  ela toca antes do `PostToolUse` do lançamento, entra em N' sem o pytest tê-la
  coletado; se é de outra sessão, não toca este banco. **Idêntico ao primeiro
  plano hoje** — a captura não fica mais fraca. Decidido 2026-09-30 (grill #3).
- **Sessão retomada, bifurcada ou limpa**: balde novo, banco novo; o lançamento
  fica `pendente` no balde antigo. Vale a receita manual.
- Job sem notificação terminal aceita no transcript (61 de 680, 9%): `pendente`.
- **Receita manual** (`state_cli evidence`): continua gravando na revisão
  corrente e confiando no modelo, como hoje. Decidido 2026-09-30 (grill #2).
- Arquivo de saída alterado por outro processo entre o término e a leitura: a
  defesa é nome e diretório do arquivo e a concordância do código de saída, não
  um hash do host.
- Escrita que o job faz na árvore sem gerar toque (Deferred no `CONTEXT.md`).
- Lançamento de subagente cuja notificação só existe em entrada `isSidechain`:
  `pendente`.
- Monitor, Workflow, Agent assíncrono; evidência de `docs`.

---

## [NEEDS CLARIFICATION]

- [x] ~~**CLARIF-1**: de qual entrada do transcript a notificação terminal é
  aceita?~~ → ASSUMPTION-007.
- [x] ~~**GRILL-2**: fechar a receita manual quando há lançamento?~~ → ASSUMPTION-008.
- [x] ~~**GRILL-3**: escrita concorrente na janela do lançamento?~~ → ASSUMPTION-009.
- [x] ~~**GRILL-4**: qual execução decide na mesma revisão?~~ → ASSUMPTION-010.

---

## Suposições

- **ASSUMPTION-001**: um arquivo de saída de job terminado há minutos existe
  quando o hook o lê — medido: 0 sumidos entre 15 lançamentos com < 6 h, sumido
  mais novo com 23,6 h · decidido 2026-09-30 por inferência (medição) ·
  justifica: REQ-F3, AC-1.2, AC-3.4
- **ASSUMPTION-002**: o código de saída do resumo e o trailer do arquivo não
  divergem em uso normal — medido 226/226 · decidido 2026-09-30 por inferência
  (medição) · justifica: REQ-F4, AC-3.5
- **ASSUMPTION-003**: o payload do `PostToolUse` traz `tool_use_id` igual ao
  `<tool-use-id>` da notificação. Conferido na doc oficial que o campo existe
  ("Unique identifier for this tool call"); a igualdade foi medida no
  transcript (617/619), não num payload de hook — o design confere num payload
  real · decidido 2026-09-30 por inferência · justifica: REQ-F1, REQ-F3, AC-3.6
- ~~**ASSUMPTION-004**~~ FALSA em 2026-09-30 (grill #4): "a última linha por
  `id` decidir é certo também para evidência capturada". Uma captura atrasada
  ganharia a maior `id` e cobriria uma execução mais nova. Substituída por
  ASSUMPTION-010.
- **ASSUMPTION-005**: suíte vermelha capturada na revisão corrente zera
  `verified`, como a mesma suíte em primeiro plano zeraria · decidido
  2026-09-30 por inferência · justifica: AC-3.3, AC-4.2
- **ASSUMPTION-006**: todo turno termina num Stop antes do próximo
  `UserPromptSubmit`, então `task_viva` já lê o resultado da captura · decidido
  2026-09-30 por usuário (L-09) · justifica: REQ-F7
- **ASSUMPTION-007**: a notificação é aceita só de entrada de forma do host —
  `attachment` do tipo `queued_command` com `commandMode ==
  "task-notification"`, ou `user` com `origin.kind == "task-notification"` — e
  nunca de `queue-operation`, `assistant`, `toolUseResult`, subagente, ou
  `queued_command` com `commandMode == "prompt"` (humano ou outra sessão). O
  modelo não escreve essas formas; `additionalContext` de hook e arquivo `@`
  chegam como `attachment` de outros tipos. Medido 2026-09-30: `commandMode`
  separa 715/715 notificações dos prompts enfileirados; `attachment` + `user`
  cobrem os 619 términos sem `queue-operation` · decidido 2026-09-30 por
  usuário (CLARIF-1: "entrada do host"; a lista de formas é o refinamento do
  grill #19, #25, #36, apertado pela medição da fase de TDD) · justifica:
  REQ-F3, AC-3.7
- **ASSUMPTION-008**: a receita manual continua como hoje; ela já confia no
  modelo, e travar só este caso não sobe a segurança · decidido 2026-09-30 por
  usuário (grill #2) · justifica: AC-2.3, AC-5.3, NEVER "expor a revisão"
- **ASSUMPTION-009**: escrita concorrente na janela do lançamento é limite
  declarado, idêntico ao do primeiro plano · decidido 2026-09-30 por usuário
  (grill #3) · justifica: AC-1.1, Nível de garantia
- **ASSUMPTION-010**: na mesma revisão, decide a execução que TERMINOU por
  último; o término da captura é o carimbo da notificação, o da evidência
  gravada é seu `created_at` · decidido 2026-09-30 por usuário (grill #4) ·
  justifica: REQ-F10, AC-4.1..4.3
- **ASSUMPTION-011**: `code_revision` nunca repete valor dentro de uma task —
  única escrita é `+1` em `touch_files` (`transactional_state.py:1239`) ·
  decidido 2026-09-30 por inferência (código) · justifica: AC-2.4, REQ-F6
- **ASSUMPTION-012**: pendente de task morta nunca é lido de novo (a captura só
  olha a task do chamador), então não precisa expirar; pendente da task viva é
  relido junto da leitura que `jobs_em_voo` já faz · decidido 2026-09-30 por
  inferência (código) · justifica: REQ-NF1

---

## Grill-me — rodada 1 (2026-09-30)

`wf-grill`, contexto limpo: **57 perguntas, 5 de 5 lentes vivas, 5
bloqueantes**. Destino de cada grupo:

| Grill | Destino |
|---|---|
| #2 receita manual | usuário → ASSUMPTION-008; NEVER expor revisão |
| #3, #18, #57 escrita concorrente na janela | usuário → ASSUMPTION-009; Nível de garantia |
| #4, #10, #22 ordem entre execuções | usuário → US-4, REQ-F10, ASSUMPTION-010; ASSUMPTION-004 derrubada |
| #5 insumo real some em ~24 h | guardado em 2026-09-30 (sha256 `a8f1ad1f…`); forma de fixture no design |
| #1, #21, #28, #46 sessão retomada | balde por sessão → Nível de garantia |
| #6, #20, #22 várias notificações | AC-3.11 |
| #7, #50 status × resumo | REQ-F4, AC-3.2, AC-3.12 |
| #8 precedência entre regras | design (ordem: aceitação → REQ-F4/F9 → REQ-F10 → revisão) |
| #9, #32 estado × motivo, status | Glossário |
| #11 o que é "escrita" | Glossário (qualquer toque) |
| #12 "hoje" | Glossário (`d3fc851`); captura antes de `jobs_em_voo` (REQ-F7) |
| #13, #52 metade-controle | design fixa SHA `d3fc851` + mutante "grava na revisão corrente" |
| #14 revisão exposta no CLI | NEVER |
| #15, #43 revisão repetida | ASSUMPTION-011, AC-2.4 |
| #16, #49 task morta / outra task | REQ-F3 (só a task do chamador), ASSUMPTION-012 |
| #17, #34, #50, #55 caminho e tamanho do arquivo | REQ-F9, AC-3.8 |
| #19, #25, #36 texto colado em `attachment`/`user` | ASSUMPTION-007 refinada, AC-3.7 |
| #23, #41 várias linhas de sumário no texto inteiro | mesma função e mesmo risco do primeiro plano (`max` por categoria); cauda limitada (REQ-F9) |
| #24 arquivo preso, ilegível ou parcial | AC-3.10 (erro de leitura → pendente), AC-3.9 |
| #26 deploy | NEVER deploy sem OK; hooks sobem do mesmo plugin por processo |
| #27 `transcript_path` no `PostToolUse` | doc oficial: presente |
| #29 comando real do consumidor | `python -m pytest -q -p no:cacheprovider` passa (linha vazia gravada no balde `037312e7`) |
| #30, #37 pendente eterno | ASSUMPTION-012 |
| #31 várias leituras | REQ-NF1 |
| #33, #44, #48 atomicidade e duplicidade | REQ-F1 (único), REQ-F3 (transação, UPDATE condicionado), AC-1.6 |
| #35 task de docs / sem task | `record_evidence` já trata tipo diferente como histórico; sem task, o hook não roda |
| #38 lançamento de subagente | Nível de garantia |
| #39 exceção no `complete` | REQ-F7, REQ-NF2 |
| #40 evidência antiga, régua | NEVER |
| #42 retenção por evento | ASSUMPTION-001 + AC-3.4 (fail-closed) |
| #45, #54 PowerShell | AC-1.5 condicionado; medição no design; ASK |
| #47 `tool_use_id` nulo | AC-3.6 |
| #51 contrato | eventos e tabelas não estão em `contract/` (conferido) |
| #53 âncoras da `magical-shamir` | em `main` `d3fc851`; linhas conferidas |
| #56 formato do host muda | REQ-NF3 (pendente acumulado visível) |

---

## Success Criteria

- [ ] Todos os AC P1 passando em testes automatizados, cada um visto vermelho antes
- [ ] Falsificação nas duas metades (L-07): AC-1.2 e AC-2.1 contra o código de
  `d3fc851` (reprova) e com a feature (passa); AC-2.1 e AC-4.1 também contra um
  mutante que grava na revisão corrente (reprova)
- [ ] Medida com a função de produção: captura sobre o insumo real de `b8jmxvjka`
  grava `exit_code=1, tests_collected=1613, tests_passed=1610, tests_skipped=1`
  (os 2 `failed` entram em `collected`)
- [ ] Testes afetados verdes; suíte completa só no merge (regra de coordenação)
- [ ] Zero findings críticos em `wf-verify-multimodel`
- [ ] Toda CLARIF/decisão do grill convertida em `ASSUMPTION-nnn`

---

## Spec Metadata (machine-readable)

```json
{
  "spec_id": "evidencia-em-segundo-plano",
  "version": 2,
  "harness_version": "v3",
  "generated_by": "write-spec skill",
  "generated_at": "2026-09-30T15:10:00-03:00",
  "priorities": ["P1", "P2"],
  "requirement_count": 13,
  "user_story_count": 5,
  "needs_clarification_count": 0,
  "assumption_count": 12,
  "assumptions_by_inference": 7,
  "grilled": true,
  "grill_rounds": 1
}
```

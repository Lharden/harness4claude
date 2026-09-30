---
applies_to:
  - scripts/evidencia_em_segundo_plano.py
  - scripts/transactional_state.py
  - hooks/harness-transactional.py
  - tests/test_evidencia_em_segundo_plano.py
  - tests/fixtures/evidencia_em_segundo_plano/**
---

# Design: evidência automática de suíte em segundo plano

**Spec Link**: [evidencia-em-segundo-plano-spec.md](evidencia-em-segundo-plano-spec.md) (v2, aprovada 2026-09-30)
**Status**: Draft
**Created**: 2026-09-30
**Author**: AI-generated, reviewed by Leonardo
**Base**: `main` em `d3fc851`

---

## Technical Context

**Language**: Python 3 (o interpretador do `PATH`; hooks rodam do plugin instalado)
**Framework**: nenhum — hooks de linha de comando do Claude Code
**Key Dependencies**:
- `sqlite3` (stdlib) — `harness.db` por balde de sessão, WAL
- `scripts/trabalho_em_voo.py` (`main`) — leitura do transcript com pré-filtro

**Storage**: `harness.db` no balde da sessão (`HarnessDatabase`)
**Testing**: pytest (suíte do repo); testes herméticos com `tmp_path`
**Type Check / Lint**: nenhum obrigatório no repo; seguir o idioma dos arquivos

**Constraints**:
- Latência: Stop tem 15 s de timeout (`hooks/hooks.json`); a leitura de
  transcript já feita por `jobs_em_voo` custa 1,3 s no pior caso medido.
- Coordenação (L-08): só testes afetados, um processo por vez; suíte completa
  só no merge; nenhum deploy sem OK; não tocar o caminho do classificador.

### Medições que este design usa (2026-09-30, além das da spec)

`scratchpad/medir_bg2.py`:

| Pergunta | Resultado |
|---|---|
| Lançamentos por ferramenta | Bash 595 · PowerShell 107 (pares `tool_use_id`→job, lista não deduplicada; a contagem deduplicada da spec é 680) |
| `.output` de PowerShell existentes | 74: 66 com trailer `[exited with code N]`, 8 sem; 74/74 decodificam UTF-8 |
| Tamanho dos `.output` de shell | n=602 · mediana 270 B · p95 46 KB · **máx 323 MB** |
| Distância do início do último sumário do pytest até o fim do arquivo | n=140 · mediana 79 B · p95 164 B · **máx 511 B** |

Consequência: ler a cauda com teto de **64 KiB** cobre 128× o pior caso medido e
nunca carrega os 323 MB. AC-1.5 (PowerShell) deixa de ser condicional: mesmo
formato.

---

## Architecture

A feature tem três tempos, e cada um fica com o dono natural:

1. **Lançamento** — o hook sabe que houve lançamento (é ele que vê o
   `tool_response`). Grava uma linha em `lancamentos` e nada em `evidence`.
2. **Julgamento** — função pura: dado um lançamento, as notificações do
   transcript e a cauda do arquivo, devolve um veredito. Não toca banco, não
   levanta. Mora num módulo novo em `scripts/`, porque o `complete` (em
   `transactional_state`) também precisa dela e `scripts/` não importa `hooks/`.
3. **Resolução** — o banco aplica o veredito numa transação: evidência (ou
   não), estado do lançamento, evento. Mora em `HarnessDatabase`, que já é dono
   de `evidence` e do lock de escrita.

A captura (julgar + resolver os pendentes de uma task) é chamada nos dois
leitores que decidem `verified` (L-09): `_handle_stop` e `HarnessDatabase.complete`.

A contagem de testes sai de `_test_counts` para o módulo novo, como função
sobre texto; `_test_counts` passa a ser `contar_testes(_response_text(payload))`.
Uma implementação, dois consumidores (REQ-F5).

### High-Level Diagram (ASCII)

```
PostToolUse (Bash|PowerShell, verificação confiável, backgroundTaskId=J)
   |-- touch_files ---------------------------> tasks.code_revision = N'
   |-- db.registrar_lancamento(task, J, U, T, cmd)  (lê N' no mesmo lock)
   '-- AVISO_SEGUNDO_PLANO (captura automática em N')

... host grava <task-notification> em T e reinvoca o modelo ...

Stop ------------------+                 state_cli complete --+
                       v                                      v
        capturar_lancamentos(db, task_id)  <-- mesma função --'
           | db.lancamentos_pendentes(task_id)   (1 SELECT; vazio -> fim)
           | notificacoes_de(T, {J: U})          (1 leitura por T, pré-filtro)
           | ordenar por término
           | para cada J:  julgar(lanc, notifs, ler_cauda) -> Veredito
           '-              db.resolver_lancamento(task_id, J, veredito)  (1 transação)
                              |-- superado?  (evidence em N' com created_at > término)
                              |-- _gravar_evidencia(conn, row, ..., revisao=N')
                              |-- UPDATE lancamentos ... WHERE estado='pendente'
                              '-- INSERT events(lancamento_resolvido)
        depois: jobs_em_voo, register_stop_continuation, bloqueio  (como hoje)
```

### Key Components

1. **`scripts/evidencia_em_segundo_plano.py`** (novo) [traces: REQ-F3, REQ-F4, REQ-F5, REQ-F9, REQ-F10, US-1, US-3, US-4]
   - Responsabilidade: tudo que é leitura e julgamento; nada de escrita.
   - Interface pública:
     - `contar_testes(texto: str) -> tuple[int|None, int|None, int|None, str|None]`
       — o corpo atual de `_test_counts`, com `VEREDITO_PASSA`, `VEREDITO_FALHA`,
       `SEM_VEREDITO` e `_soma_categorias` movidos para cá.
     - `notificacoes_de(transcript_path, esperados: dict[str, str|None]) -> dict[str, list[Notificacao]]`
       — lê o transcript uma vez, com pré-filtro `"<task-notification>"`, e
       devolve só notificações aceitas (ASSUMPTION-007) cujo `<task-id>` está em
       `esperados` e cujo `<tool-use-id>` bate com o valor esperado. Reusa
       `_BLOCO`, `_TASK_ID`, `_textos`, `_instante` de `trabalho_em_voo`.
     - `ler_cauda(caminho: str, limite: int = 65536) -> str` — lê os últimos
       `limite` bytes e decodifica UTF-8 com `errors="replace"`; levanta
       `OSError` (o chamador distingue `FileNotFoundError` dos demais). O
       insumo real **não é UTF-8 válido** (byte `0x97` na posição 3661, cp1252
       de uma mensagem de teste) e usa CRLF — achado no primeiro vermelho do TDD.
     - `julgar(lancamento: dict, notificacoes: list[Notificacao], transcript_path: str, ler=ler_cauda) -> Veredito`
       — pura (a leitura é injetada, para teste).
     - `capturar_lancamentos(database, task_id: str) -> int` — orquestra;
       devolve quantas linhas de `evidence` gravou (o `complete` precisa disso,
       ver API Contracts). Nunca levanta.
   - Dependências: `trabalho_em_voo`; `HarnessDatabase` só pelo parâmetro.

2. **`HarnessDatabase`** (`scripts/transactional_state.py`) [traces: REQ-F1, REQ-F3, REQ-F6, REQ-F7, REQ-F10, US-1, US-2, US-4]
   - `_ensure_schema`: tabela `lancamentos` + índice (Data Model).
   - `registrar_lancamento(task_id, job_id, tool_use_id, command, transcript_path) -> dict | None`
     — `INSERT OR IGNORE` com `revisao = tasks.code_revision` lida dentro do
     mesmo `_write()`; ignora task terminal.
   - `lancamentos(task_id, *, estado=None) -> list[dict]`.
   - `resolver_lancamento(task_id, job_id, veredito) -> bool` — uma transação;
     `False` se o lançamento já não estava `pendente`.
   - `record_evidence(..., code_revision: int | None = None)` — o corpo vira
     `_gravar_evidencia(connection, row, ..., revisao)`, compartilhado com
     `resolver_lancamento`. Regra de REQ-F6: `revisao` omitida ou igual à
     corrente → exatamente o código de hoje; diferente → só o `INSERT`.
   - `complete(task_id, *, expected_revision)` — ver API Contracts.

3. **`hooks/harness-transactional.py`** [traces: REQ-F2, REQ-F5, REQ-F7, REQ-F8, US-1, US-5]
   - `_test_counts(payload)` → `contar_testes(_response_text(payload))`.
     Os nomes `VEREDITO_*`, `SEM_VEREDITO` e `_soma_categorias` continuam
     importáveis do hook (reexportados), porque testes existentes os usam.
   - `_handle_post_tool`: no ramo `job = _job_em_segundo_plano(...)`, depois do
     toque, `database.registrar_lancamento(...)` com `tool_use_id` e
     `transcript_path` do payload; aviso novo.
   - `_handle_stop`: `capturar_lancamentos(database, task_id)` e `database.task()`
     de novo **antes** do teste `task["verified"]` (senão uma captura verde nunca
     solta o Stop que a disparou); o resto como hoje.
   - `_motivo_do_gate`: bloco "Lançamentos desta task: J (rev N', estado,
     motivo) ; …" quando houver algum.

---

## Data Model

### Entities

#### `lancamentos` (tabela nova) [traces: REQ-F1, REQ-F3, REQ-F10, US-1, US-4, US-5]

```sql
CREATE TABLE IF NOT EXISTS lancamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    job_id TEXT NOT NULL,
    tool_use_id TEXT,                -- NULL => nunca casa (AC-3.6)
    command TEXT NOT NULL,
    transcript_path TEXT,            -- NULL => nunca captura
    code_revision INTEGER NOT NULL,  -- N'
    estado TEXT NOT NULL DEFAULT 'pendente',
    motivo TEXT,
    lancado_em TEXT NOT NULL,
    terminou_em TEXT,                -- carimbo da notificação aceita
    resolvido_em TEXT,
    evidence_id INTEGER REFERENCES evidence(id),
    UNIQUE(task_id, job_id)
);
CREATE INDEX IF NOT EXISTS lancamentos_pendentes ON lancamentos(task_id, estado);
```

**Estados** (`estado`): `pendente` → um de {`capturado`, `historico`,
`superado`, `rejeitado`}. Transição única, por
`UPDATE ... WHERE id = ? AND estado = 'pendente'`; `rowcount == 0` desfaz a
transação inteira (outra captura chegou antes — AC-1.4, grill #44/#48).

**Motivos** (`motivo`, só em `rejeitado` e `superado`): `sem-codigo`,
`sem-arquivo`, `arquivo-estranho`, `codigo-diverge`, `notificacao-diverge`,
`status-inconsistente`, `sem-trailer`, e — já no registro, sem esperar
notificação — `sem-tool-use-id`, `sem-transcript`; `superado` grava o id da evidência mais nova que o
superou (`por-evidence-<id>`).

**Invariantes**:
- `capturado` e `historico` ⇒ `evidence_id` não nulo, e
  `evidence.code_revision = lancamentos.code_revision`.
- `historico` ⇔ `code_revision` do lançamento ≠ revisão corrente no momento
  da resolução.
- `superado` e `rejeitado` ⇒ `evidence_id` nulo.

**Relations**: `tasks` 1:N `lancamentos`; `lancamentos` N:0..1 `evidence`.

**Migração**: só `CREATE ... IF NOT EXISTS`; nenhuma coluna em tabela existente
muda, nenhuma linha antiga é tocada (NEVER da spec). Processo antigo que abra o
mesmo banco ignora a tabela.

### Value object: `Veredito`

```python
@dataclass(frozen=True)
class Veredito:
    estado: str                 # 'pendente' | 'aceito' | 'rejeitado'
    motivo: str | None
    exit_code: int | None = None
    tests_collected: int | None = None
    tests_passed: int | None = None
    tests_skipped: int | None = None
    output_hash: str | None = None
    terminou_em: str | None = None
```

`julgar` devolve `pendente` (nada a fazer agora), `rejeitado` (terminal, sem
evidência) ou `aceito` (tem evidência). `aceito` vira `capturado`, `historico`
ou `superado` só dentro de `resolver_lancamento`, que é quem lê a revisão
corrente e as evidências sob o lock.

### Precedência em `julgar` (fecha grill #8)

Ordem fixa; a primeira regra que decide encerra:

1. Nenhuma notificação aceita → `pendente`.
2. Notificações aceitas discordam em (`status`, código do resumo) →
   `rejeitado:notificacao-diverge`.
3. `status` ∉ {`completed`, `failed`} ou resumo sem código →
   `rejeitado:sem-codigo`.
4. `failed` com código 0 → `rejeitado:status-inconsistente`.
5. `<output-file>` com nome ≠ `J.output` ou fora de um diretório `tasks` →
   `rejeitado:arquivo-estranho`. (A versão anterior exigia também pasta de
   sessão e projeto iguais às do transcript; o verify #19 mediu 65/654
   rejeições falsas por sessão retomada — retirado.)
6. Arquivo inexistente (`FileNotFoundError`) → `rejeitado:sem-arquivo`.
   Outro `OSError` (permissão, arquivo preso) → `pendente`.
7. Sem trailer → `rejeitado:sem-trailer`; trailer ≠ código →
   `rejeitado:codigo-diverge` (trailer obrigatório desde o verify #15).
8. Senão → `aceito` com `contar_testes(cauda)` e `sha256(cauda)`.

Antes da regra 1, em `notificacoes_de` (`_textos_da_notificacao` +
`_bloco_do_host`): só o campo da notificação é lido (`attachment.prompt`,
`message.content`), nunca metadados como `cwd` (rodada 3 #7); o bloco vai do
primeiro `<task-notification>` ao último `</task-notification>`, os campos do
host saem de antes do primeiro `<summary>` e o resumo vai até o último
`</summary>` — bloco forjado pela descrição (verify #11) e tag literal na
descrição (rodada 3 #9) ficam dentro do resumo do job verdadeiro. A versão da
iteração 1 ("um bloco por texto", 2471/2471 legítimos) descartava a notificação
legítima no segundo caso. O código do resumo é casado no FIM do summary (verify
#10, #12), porque o começo é a `description` do modelo. Na regra 2, o conjunto
de divergência inclui o `<output-file>` (verify #13).

Em `resolver_lancamento`, sob o lock:

9. Existe `evidence` do mesmo tipo na revisão N' cuja execução terminou depois
   de `terminou_em` → `superado` (sem evidência). O fim de cada execução é
   `COALESCE(lancamentos.terminou_em, evidence.created_at)`: para evidência
   capturada, `created_at` é a hora da captura, não a do término.
   **Corrigido no TDD** (teste do AC-4.3): a primeira versão usava só
   `created_at`, e um job capturado no mesmo Stop que outro fazia o que
   terminou por último sair `superado` pelo que terminou antes.
10. N' = revisão corrente → `capturado` (evidência pelo caminho de hoje).
11. N' ≠ revisão corrente → `historico` (evidência só inserida).

`<sessão de T>` é o nome do arquivo de transcript sem `.jsonl`; `<projeto de T>`
é o nome do diretório que contém o transcript. Comparação sem diferenciar
maiúsculas (Windows). Medido no caso real: `projects\C--…-exciting-bhabha-3659cc\037312e7-….jsonl`
↔ `%TEMP%\claude\C--…-exciting-bhabha-3659cc\037312e7-…\tasks\b8jmxvjka.output`.

### Formas aceitas da entrada que carrega a notificação (ASSUMPTION-007)

| `type` | condição extra |
|---|---|
| `attachment` | `attachment.type == "queued_command"` e `attachment.commandMode == "task-notification"` |
| `user` | `origin.kind == "task-notification"` e sem `toolUseResult` |
| qualquer outra (inclusive `queue-operation`), ou `isSidechain: true` | recusada |

`origin` lido no topo da entrada ou em `attachment.origin` (erro de instrumento
medido e corrigido na spec).

Medido em 2026-09-30 (`scratchpad/medir_bg3.py`): nas `queued_command` que
carregam notificação terminal, `commandMode` é `task-notification` em **715 de
715**; prompts de humano e de outra sessão enfileirados vêm com `commandMode:
"prompt"` (153 `human`, 288 `peer`, 4 sem `origin`). `attachment` (337) + `user`
(282) somam os 619 términos da spec, então `queue-operation` — a forma em que
texto colado entraria sem marcador nenhum — é redundante e fica de fora. Custo:
a 1 notificação `user` sem marcador medida.

---

## API Contracts

### `HarnessDatabase.record_evidence(..., code_revision=None)` [traces: REQ-F6, US-2]

- `code_revision is None` ou `== tasks.code_revision`: comportamento de hoje,
  byte a byte (AC-2.3).
- Diferente: `INSERT` com essa revisão; `tasks.verified`, `tasks.status`,
  `tasks.stop_continuations` intocados; `revision += 1` (houve escrita no
  banco da task, e quem usa CAS precisa enxergar).
- `state_cli evidence` não repassa o parâmetro (NEVER).

### `HarnessDatabase.complete(task_id, *, expected_revision)` [traces: REQ-F7, US-1, AC-1.3]

A captura sobe `revision` (cada evidência gravada é `+1`), e o `expected_revision`
que o modelo passou foi lido antes dela. Sem cuidado, o `complete` que a captura
acabou de habilitar recusaria por `revision mismatch`. Contrato:

1. Lê a task; `revision != expected_revision` → recusa como hoje (a captura
   nem roda).
2. `gravadas = capturar_lancamentos(self, task_id)` (nunca levanta).
3. Confere `revision == expected_revision + gravadas`; diferente → recusa como
   hoje (houve outro escritor no meio). Só evidência gravada conta: resolver
   um lançamento sem evidência (`rejeitado`, `superado`) não mexe em
   `tasks.revision`.
4. Resto igual a hoje (`verified`, `_has_fresh_evidence`, fase final).

A recusa por falta de evidência passa a listar os lançamentos da task.

### `capturar_lancamentos(database, task_id) -> int` [traces: REQ-F3, REQ-F7, REQ-NF1, REQ-NF2]

- 0 pendentes → devolve 0 depois de 1 `SELECT`.
- Agrupa pendentes por `transcript_path`; uma leitura por caminho.
- Ordena por `terminou_em` crescente (REQ-F10); sem notificação vai por último
  e sai `pendente`.
- Qualquer exceção → devolve o que já gravou; nada propaga.

### `registrar_lancamento(...)` no `PostToolUse` [traces: REQ-F2, AC-1.1, AC-1.6]

Payload lido: `tool_response.backgroundTaskId` (via `_job_em_segundo_plano`),
`tool_use_id` (doc oficial), `transcript_path` (doc oficial), comando de
`_command(payload)`. Faltando `tool_use_id` ou `transcript_path`, grava com
NULL — o lançamento aparece na mensagem do portão e nunca captura.

### Evento `lancamento_resolvido` [traces: REQ-F8, AC-5.2]

`payload_json = {"job": J, "estado": ..., "motivo": ..., "code_revision": N', "evidence_id": ...}`.
Não está em `contract/` (conferido); não se edita `contract/`.

### Textos [traces: REQ-F8, AC-5.1, AC-5.3]

- `AVISO_SEGUNDO_PLANO`: "a suíte foi para segundo plano (job J). A evidência
  será capturada sozinha quando ela terminar, na code_revision N'. Qualquer
  escrita antes disso a torna histórico. Se a captura não acontecer, e nada
  tiver mudado desde o lançamento, registre à mão: {comando}".
- `_motivo_do_gate` e recusa do `complete`: "Lançamentos desta task: J rev=N'
  estado[:motivo] ; …".

---

## Implementation Phases

Por prioridade das user stories. Cada fase termina com os testes dela verdes e
vistos vermelhos antes.

### Phase 1: Foundation (pré-requisitos, sem mudar comportamento)
- [ ] Mover a contagem para `contar_testes` e fazer `_test_counts` chamá-la;
      testes existentes de contagem verdes sem alteração [traces: REQ-F5]
- [ ] `record_evidence` → `_gravar_evidencia(connection, row, ..., revisao)` e
      parâmetro `code_revision` (AC-2.2, AC-2.3 em teste unitário do banco);
      `resolver_lancamento` depende disto [traces: REQ-F6, US-2]
- [ ] Fixture do insumo real (Test Strategy) [traces: L-01, Success Criteria]

### Phase 2: Core (US-1, US-3 — P1)
- [ ] Tabela `lancamentos`, `registrar_lancamento`, `lancamentos()` [traces: REQ-F1, REQ-F2]
- [ ] `notificacoes_de`, `ler_cauda`, `julgar` — um teste por regra 1–8,
      inclusive `OSError` → `pendente` [traces: REQ-F3, REQ-F4, REQ-F9, REQ-NF2]
- [ ] `resolver_lancamento` (regras 10–11), `capturar_lancamentos` [traces: REQ-F3]
- [ ] Captura no Stop (antes do teste de `verified`) e no `complete`
      (contrato de `expected_revision`) [traces: REQ-F7, AC-1.3]

### Phase 3: US-2 (P1)
- [ ] Integração AC-2.1 e AC-2.4 pelo hook (lançamento → `Edit` → Stop) [traces: REQ-F6, US-2]

### Phase 4: US-4 (P1)
- [ ] Ordem de término e `superado` (regra 9) [traces: REQ-F10]

### Phase 5: US-5 (P2)
- [ ] Mensagens e evento [traces: REQ-F8]

### Phase 6: Prova (L-07, REQ-NF1) e entrega
- [ ] Insumo real `b8jmxvjka` pela função de produção → 1613/1610/1, `exit_code=1`
- [ ] Controle em `d3fc851` (worktree fixado por SHA): AC-1.2, AC-2.1, AC-4.1 reprovam
- [ ] Mutantes M1–M6: cada um reprova pelo menos um teste; registrar qual
- [ ] Teste de performance (130 MB, < 5 s)
- [ ] Testes afetados, um processo por vez: o arquivo novo +
      `test_transactional_hook.py`, `test_portao_em_voo.py`, testes de contagem
- [ ] `graphify update .`; atualizar de `main` antes de pedir merge (coordenação)

---

## Test Strategy

Arquivo novo `tests/test_evidencia_em_segundo_plano.py`, hermético: `harness.db`
em `tmp_path`, transcript e `.output` escritos pelo teste em
`tmp_path/projects/<proj>/<sessão>.jsonl` e `tmp_path/temp/claude/<proj>/<sessão>/tasks/J.output`.
Hook exercido por `handle_payload(payload, harness_root=tmp_path)` — a função
de produção, não uma reimplementação.

### Unit
- `contar_testes`: os casos de `_test_counts` que já existem continuam verdes
  (Phase 1); mais a saída real (`1610 passed, 1 skipped, 2 failed` → 1613/1610/1).
- `julgar`: um teste por regra da precedência (1–8), incluindo cada forma
  recusada da tabela de procedência (AC-3.7) e `OSError` ≠ `FileNotFoundError`
  (AC-3.10).
- `resolver_lancamento`: 9–11 (`superado`, `capturado`, `historico`); corrida
  (dois `resolver_lancamento` no mesmo J → uma evidência) [AC-1.4].
- `record_evidence`: revisão omitida = hoje (compara com o comportamento
  antigo nas mesmas entradas); revisão antiga não mexe em `verified` para
  nenhum lado [AC-2.2, AC-2.3].

### Integration (hook + banco, pela função de produção)
- AC-1.1 → AC-1.2: `PostToolUse` com `backgroundTaskId`, depois transcript com
  notificação, depois `Stop` → evidência em N', `verified=1`, Stop sem bloqueio.
- AC-1.3: `complete` sem Stop no meio, com `expected_revision` lido antes.
- AC-2.1: `PostToolUse` de lançamento, `PostToolUse` de `Edit` (toque), Stop →
  evidência em N', `historico`, Stop bloqueia.
- AC-4.1..4.3: ordem de término.
- AC-5.1: texto da mensagem.

### Fixture do insumo real (grill #5)
`tests/fixtures/evidencia_em_segundo_plano/b8jmxvjka/`:
- `b8jmxvjka.output` — o arquivo inteiro (4706 B, sha256 `a8f1ad1f…f8f3`),
  guardado em 2026-09-30 antes do corte de ~24 h. Conteúdo: nomes de teste e o
  sumário do pytest do próprio harness4claude.
- `transcript.jsonl` — **só as entradas do host** que citam o job: o
  `tool_result` do lançamento (linha 612 do original) e as três entradas da
  notificação (662 `queue-operation/enqueue`, 666 `attachment/queued_command`,
  669 `queue-operation/remove`). As linhas 634, 655 e 661 são texto do
  modelo e ficam de fora.
- O teste copia os dois para `tmp_path` com o layout real e reescreve apenas o
  caminho de `<output-file>` para o `tmp_path`. É a única alteração do insumo,
  e o teste diz isso.

### Falsificação (L-07) e metade-controle por SHA
- **Controle**: os testes de AC-1.2, AC-2.1 e AC-4.1 rodam contra `d3fc851`
  (worktree temporário fixado por SHA, nunca `main:` — memória "controle de
  código antigo por SHA") e reprovam; com a feature, passam.
- **Mutantes**, cada um tem de reprovar pelo menos um teste:
  - M1: captura grava na revisão corrente (em vez de N');
  - M2: `julgar` ignora `<tool-use-id>`;
  - M3: aceita notificação de entrada `assistant`/`toolUseResult`;
  - M4: sem a regra `superado`;
  - M5: evidência histórica mexe em `verified`;
  - M6: `_handle_stop` captura depois do teste `task["verified"]`.

### Performance
- Transcript sintético de 130 MB com 1 lançamento pendente: captura + `jobs_em_voo`
  < 5 s na máquina (orçamento do Stop: 15 s).

### Coverage Target
- Toda regra de `julgar` e toda transição de estado com pelo menos um teste.

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|-----------|
| Host muda formato da notificação ou do resumo | High (captura cai a zero) | Med | Pendente acumulado aparece na mensagem do portão (REQ-NF3); receita manual continua |
| `tool_use_id` do payload do hook diferente do `<tool-use-id>` do transcript | High (nada casa) | Low | Doc oficial documenta o campo; o `tool_response` já foi medido idêntico ao `toolUseResult` (sha256). Conferir no primeiro uso real depois do deploy: lançamento `capturado` ou `pendente` na mensagem |
| `complete` recusando por revisão depois da captura | Med | Med sem o contrato | Contrato `expected_revision + gravadas` (API Contracts) e teste AC-1.3 |
| Stop acima de 15 s | High (host solta o Stop) | Low | Pré-filtro; leitura só com pendente; cauda de 64 KiB; teste de performance |
| Sete frentes no mesmo `harness-transactional.py` | Med (conflito no merge) | High | 7º na ordem de merge; atualizar de `main` antes do pedido de merge; mudanças do hook concentradas em 4 pontos |
| Fixture com dado de sessão real | Low | Low | Só entradas do host; sem texto do modelo nem do usuário |

---

## Open Questions

- Nenhuma que bloqueie o plano. A conferência de `tool_use_id` num payload
  real acontece depois do deploy (Risks), porque o hook instalado é o único que
  vê payload real, e deploy é decisão do usuário.

---

## Validation Report

- **Status**: PASS (after revision 1)
- **Cobertura**: REQ-F1..F10 e REQ-NF1..NF3 com fase; L-01..L-09 com fase ou
  já cumpridos (L-06 medido na spec, L-08 em `d3fc851`); nenhum Deferred no plano.
- **Gaps found**:
  1. L-07 (falsificação) sem fase — só descrita na Test Strategy.
  2. REQ-NF1 (performance) sem fase.
  3. Dependência invertida: `resolver_lancamento` (fase 2) usava
     `_gravar_evidencia(revisao)`, prevista só para a fase 3.
  4. REQ-NF2 sem teste nomeado para `OSError` → `pendente`.
- **Revisions applied**: `_gravar_evidencia` e `code_revision` subiram para a
  fase 1; fase 3 ficou só com a integração de US-2; fase 6 nova (prova, controle
  por SHA, mutantes, performance, testes afetados, sincronizar com `main`);
  `OSError` nomeado na fase 2.
- **Validated at**: 2026-09-30T15:05-03:00

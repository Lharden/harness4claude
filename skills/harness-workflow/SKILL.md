---
name: harness-workflow
description: "Orquestrador de pipeline v2. Lê classificação do hook (L0/L1/L2), roteia para pipeline correto (brainstorming, write-a-prd, grill-me, prd-to-plan, tdd, etc.), mantém state.json, registra métricas. Usar quando <harness-classification> aparece com L1+ no contexto."
category: workflow
risk: low
source: custom
date_added: "2026-03-24"
metadata:
  version: 3
  triggers: harness-classification, L1, L2, pipeline, feature, bug, refactor, architecture
---

# Harness Workflow v3 — Roteador de Pipeline

> **Precedência:** CLAUDE.md SEMPRE tem prioridade sobre esta skill.

## Harness4Contract v1

`contract/` é a superfície canônica compartilhada com Harness4Codex. O
`state.json` é uma projeção legível; `harness.db`, no bucket da sessão, é a
autoridade para revisão CAS, gates, artefatos e evidência fresca. Resolva o bucket
com `python "$PR/scripts/harness_paths.py" --cwd "$PWD" --session-id "<session_id>"`.

Toda transição usa `scripts/state_cli.py`: `artifact`, `transition`, `touch`, `evidence` e
`complete`. Teste com exit 0 e zero casos coletados não verifica. Mudança posterior
de código invalida evidência anterior. Gates `approve-spec`, `approve-plan`,
`answer-clarifications`, `branch-open` e `escalation` exigem decisão humana explícita.

Antes de propagar qualquer artefato, aplique uma vez `DROP / CONSTRAIN / RETAIN`:
DROP retira direção editorial rejeitada do trabalho e da memória temática; auditoria
indispensável guarda apenas id, localização e destino DROP. CONSTRAIN mantém
predicados persistentes de segurança, conformidade, privacidade e formato na
fronteira. RETAIN mantém limite científico local à alegação e à evidência.

## Quando ativar

Ative quando o contexto contiver `<harness-classification>` com `level: L1` ou `level: L2`.
Para L0, NÃO ative — execute direto sem pipeline.

## Protocolo

1. **Ler classificação** — extraia level, type, pipeline do bloco injetado pelo hook. O `state.json` traz `classification_meta.suggested` (vindo do **regex**, rápido/offline) com `final: null` para L1+.
2. **Confirmar classificação (camada semântica)** — ANTES de anunciar, avalie a intenção REAL do prompt do usuário e compare com o `suggested`. Execute **sempre**, concordando ou não:

   ```bash
   # UMA CHAMADA POR LINHA, CAMINHO LITERAL. Sem `;`, sem `&&`, sem `$( )`, sem
   # continuacao com barra invertida — nova linha tambem e composicao.
   #
   # `2>&1` PODE, desde 2026-09-21. Ele nao era composicao e o codigo achava que
   # era: `_OPERADORES` olhava o `&` sem olhar o `>` colado antes. Foi a maior
   # classe de dano do harness — 1 062 recusas em 649 comandos distintos, 46,3%
   # de todas as recusas da maquina, e as quatro isencoes do hook caindo juntas
   # porque as quatro passam pela mesma varredura. Ver `_duplicacao_de_fd`.
   # Redirecionar para ARQUIVO (`> saida.txt`, `&> saida.txt`) continua sendo
   # escrita e continua contando.
   #
   # A isencao do contador de escrita (hooks/harness-transactional.py:152-173)
   # so vale para comando SEM composicao de shell. Ate 2026-09-17 a receita
   # daqui era composta e derrotava a propria isencao que existe para ela: as
   # tres sessoes que copiaram o bloco travaram no laco em que registrar
   # evidencia invalidava a evidencia registrada. Medido no mapa
   # `revisao-que-invalida` §6.4 (receita literal -> nao isenta; forma atomica
   # -> isenta) e §5.1 (sete chamadas atomicas seguidas, `code_revision` parado
   # em 24 nas sete, `complete` devolveu `done`).
   #
   # Passo 1 — leia `~/.claude/harness/plugin-root` com a ferramenta Read, nao
   #           com shell. Le-lo por shell nao custa nada; abrir um shell so para
   #           isso custa uma linha de comando a mais para errar.
   # Passo 2 — resolva o balde UMA vez por sessao e anote o valor impresso.
   #           Esta linha SOBE o contador: e `python`, e o hook nao pode saber
   #           se um interpretador escreve. Custo conhecido, pago uma vez.
   #           Excecao: o pin da sessao repina quando fica parado alem de
   #           HARNESS_PIN_TTL_H e o projeto e outro, e o caminho anotado passa
   #           a ser de um balde que os hooks largaram. `state_cli.py` e
   #           `record_signal.py` recusam esse balde com exit 2 e imprimem o
   #           atual: re-resolva e siga com ele, nunca recrie a task a mao.
   #           Re-resolver por task custaria uma escrita no contador em TODA
   #           task (medido 2026-10-02: 735 tasks, 1 repin em 195 pins).
   # --session-id NAO e opcional: sem ele o CLI devolve o bucket do PROJETO,
   # e o hook escreve no bucket da SESSAO. Mesma forma do preambulo
   # "Harness4Contract v1", acima.
   python "<PLUGIN_ROOT>/scripts/harness_paths.py" --cwd "<cwd>" --session-id "<session_id>"
   # Passo 3 — substitua <PLUGIN_ROOT> e <STATE_DIR> pelos valores literais.
   #           Esta linha e isenta porque nao tem composicao nenhuma.
   python "<PLUGIN_ROOT>/scripts/confirm_classification.py" --final "<L1-feature|L2-bug|...>" --confidence "<0.0-1.0>" --expect-task "<task_id>" --harness-dir "<STATE_DIR>"
   ```

   - **`--confidence`** é a sua confiança na classificação final, de 0 a 1 — não a do regex. Sem ele o banco grava `NULL` (não declarada). Até 2026-09-23 a falta virava `1.0`, e 113 de 141 linhas afirmavam uma certeza que ninguém tinha declarado.

   - **Concorda** → passe `--final` igual ao `suggested`; o script grava `agreed = true`.
   - **Discorda** (ex.: regex marcou L2 por conter "feature", mas é uma adição L1 pequena; ou o oposto) → passe o `--final` correto: o script grava `agreed = false`, corrige `classification` e **troca `pipeline`** sozinho, lendo a arvore de contrato que estiver valendo (`contract/pipelines.json`, ou a canonica do master-harness quando ela estiver alcancavel).
   - Se o usuário corrigir explicitamente depois → rode de novo com `--source human_override`.
   - **Não edite `classification_meta` à mão.** Esse era o protocolo anterior e ele não era cumprido: a auditoria de 2026-07-28 encontrou `agreed = null` em 100% das tasks e `avg_classify_accuracy = null` desde sempre, porque `recompute_aggregates` só conta tasks com `agreed is not None`. Sem este passo a métrica de accuracy é matematicamente incapaz de sair de zero.

     Só que não era só desobediência. Entre a mudança para bucket por sessão e
     2026-09-09, os três blocos deste arquivo resolviam `harness_paths.py` **sem
     `--session-id`** — e o CLI, sem ele, devolve o bucket do PROJETO enquanto os
     hooks escrevem no da SESSÃO. Quem seguia o protocolo ao pé da letra apontava
     `confirm_classification.py` e `record_signal.py` para um `state.json` órfão,
     de `task_id: null`, e recebia exit 2 com uma mensagem que acusava outra
     sessão de ter sobrescrito o estado. A obediência falhava igual à omissão, e
     por isso a métrica não distinguia as duas.
3. **Anunciar** — exiba: "Harness v3: {level}-{type} → {pipeline}" (sinalize se houve correção semântica).
4. **Atualizar estado** — registre artefato e avance com `state_cli.py`, sempre passando a revisão esperada; o helper sincroniza `state.json`.
5. **Invocar skills** — na sequência do pipeline, usando Skill tool.
6. **Obrigações** — uma fase existente pode reutilizar artefato válido, mas a transição e sua evidência continuam registradas.
7. **DONE** — grave evidência fresca, execute `state_cli.py ... complete`, então registre a task:
   ```bash
   # Mesma regra do passo 2: uma chamada por linha, caminho literal.
   # <PLUGIN_ROOT> e <STATE_DIR> ja foram resolvidos no inicio do pipeline.
   python "<PLUGIN_ROOT>/scripts/record_signal.py" --completed --steps "step1,step2,..." --expect-task "<task_id>" --harness-dir "<STATE_DIR>" --signals-dir "<HARNESS_ROOT>"
   ```
   (grava em `signals.json` com `classification_meta` e recalcula `avg_classify_accuracy`; idempotente por `task_id`). Para troca de tarefa antes do fim: `--abandoned --reason "<motivo>" --expect-task "<task_id>"` — o `--expect-task` é obrigatório com `--abandoned`.
   **Sempre passe `--expect-task` com o task_id anotado no INÍCIO do pipeline.** Com ele, e com `harness.db` no balde, o registro sai do banco pelo id — `task_id`, classificação, `classification_meta` e arquivos da SUA task —, mesmo quando a projeção já é de outra (task substituída por trabalho novo). O contador `.session-files-count` só entra se for da sua task, e o registro diz se entrou (`contador_usado`). Sem banco, ou sem a task nele, vale a projeção: se ela for de outra task (incidente 2026-06-12), o script sai 2 sem gravar.
   **Exit 2 não se conserta editando o `state.json`.** Desde 2026-09-28 o `state_cli` não aponta a projeção para uma task substituída enquanto outra está viva no escopo — avisa em stderr e grava só no banco. Restaurar a projeção à mão tiraria a projeção da viva: o PostToolUse passaria a gravar toque e evidência na task errada. Exit 2 com banco no balde quer dizer que o `--harness-dir` não é o balde da sessão da task.
   **`--abandoned` sem `--expect-task` sai 2 sem encerrar nada.** O abandono vem de troca de assunto, e nesse momento a projeção já é da task nova: sem o id, o script encerraria a task que o usuário acabou de pedir. A mensagem traz a linha para copiar e lista as tasks recentes do balde, para quem perdeu o id (`docs/specs/sinal-da-task-substituida-diagnostico.md`).

## Pipelines

Os nomes abaixo são **fases** (espelham `PIPELINES` em `harness-classify.sh`). O hook grava a fase-lista em `state.json.pipeline`; a seção **Motor de Execução** mapeia cada fase ao mecanismo real (skill direta, Workflow de fan-out, ou gate humano).

| Classificação | Fases |
|---|---|
| **L1-feature** | write-spec-light → tdd → verify-against-spec |
| **L1-bug** | systematic-debugging → tdd → verify |
| **L1-refactor** | write-spec-light → tdd → verify-against-spec |
| **L1-review** | code-review → verify |
| **L1-docs** | source-selection → documentation → verify |
| **L2-feature** | discuss → brainstorming → graph-context → write-spec → grill-me → approve-spec → design-doc → validate-plan → approve-plan → tdd → verify-multimodel |
| **L2-bug** | systematic-debugging → graph-context → grill-me → tdd → verify |
| **L2-refactor** | discuss → graph-context → write-spec → grill-me → approve-spec → design-doc → validate-plan → approve-plan → tdd → verify-multimodel |
| **L2-architecture** | discuss → brainstorming → graph-context → write-spec → grill-me → approve-spec → design-doc → validate-plan → approve-plan → tdd → verify-multimodel |
| **L2-review** | graph-context → code-review → verify-multimodel |
| **L2-docs** | source-selection → graph-context → documentation → verify-against-spec |

> **Zero skills fantasma:** removidos `triage-issue`, `request-refactor-plan`, `improve-codebase-architecture`, `prd-to-plan`, `write-a-prd`, `execucao`. Cada fase mapeia a um mecanismo real — ver "Mapa fase → mecanismo" abaixo, que agora cobre as 18.
>
> Essa afirmação foi **falsa entre 2026-08-28 e 2026-09-02**. As pipelines de `review` e `docs` entraram em `d7fa6d8` por paridade de contrato com o harness4codex, declarando `source-selection`, `documentation` e `code-review` sem que nenhum existisse — e o mapa abaixo pulava justamente esses três. Pior, eram inalcançáveis: `classify_prompt.py` só emitia `{bug, refactor, architecture, feature}`, então nenhum prompt jamais chegava lá. As duas skills foram escritas, `code-review` foi mapeado ao comando built-in, e o classificador ganhou `DOCS_PATTERNS` e `REVIEW_PATTERNS`. Fica registrado porque uma linha que declara ausência de fantasma três linhas abaixo de três fantasmas é o tipo de erro que se repete.
> **autoresearch (acelerador opcional, não obrigatório):** em bugs, `tdd`/`verify` podem usar `autoresearch:debug`/`autoresearch:fix` (loops com guard pytest); em L2 com auth/dados/API, rodar `autoresearch:security`; em L2-architecture, `autoresearch:predict` para pré-análise. Se o plugin estiver indisponível, seguir sem ele (degradação graceful).

## Motor de Execução: Workflows + Gates

> **Princípio:** fases SEM humano podem virar **Workflow** (fan-out determinístico em background); fronteiras COM humano são **gates** (`AskUserQuestion`) ENTRE as fases. O `state.json` é a máquina de estados que sobrevive entre turnos — `status: "awaiting_gate"` é retomado pelo hook no próximo prompt.

### Mapa fase → mecanismo

| Fase | Mecanismo | Como |
|---|---|---|
| (classificação) | inline | confirmar via `scripts/confirm_classification.py` (Protocolo, passo 2) |
| `discuss` | skill | `Skill(skill="discuss")` → `docs/CONTEXT.md` |
| `brainstorming` | skill | `Skill(skill="superpowers:brainstorming")` |
| `graph-context` | skill | `Skill(skill="graph-context")` — knowledge graph (graphify) primeiro; fallback `wf-context-scan` |
| `write-spec` / `write-spec-light` | skill | `Skill(skill="write-spec[-light]")` |
| `grill-me` | skill + **Workflow** | `Skill(skill="grill-me")` — o passo 0 dela chama `wf-grill` para gerar o conjunto adversarial em contexto limpo; o loop com o humano segue na skill, sem limite |
| `design-doc` | skill | `Skill(skill="design-doc")` |
| `validate-plan` | skill | `Skill(skill="validate-plan")` |
| `systematic-debugging` | skill | `Skill(skill="superpowers:systematic-debugging")` — primeira fase dos pipelines de bug |
| `tdd` | skill | `Skill(skill="superpowers:test-driven-development")` |
| `approve-spec` / `approve-plan` | gate humano | gravar gate, obter decisão explícita e resolver antes de avançar |
| `verify-multimodel` | **Workflow** | `wf-verify-multimodel` (review multi-perspectiva + adversarial) |
| `verify-against-spec` (L1) | skill | `Skill(skill="verify-against-spec")` (Workflow opcional) |
| `verify` (bug) | skill | `Skill(skill="superpowers:verification-before-completion")` |
| `source-selection` | skill | `Skill(skill="source-selection")` — decide qual fonte manda antes de escrever doc |
| `documentation` | skill | `Skill(skill="documentation")` — escreve a partir da tabela de fontes |
| `code-review` | comando | `/code-review` (built-in do Claude Code). Sem argumento revisa o diff atual; aceita PR, branch ou path |

### Invocando um Workflow

Na fase mapeada para Workflow, chame a tool **Workflow** com o script e os `args`:

Primeiro resolva o caminho do plugin — ele varia por máquina e por versão instalada:

```bash
cat "${HARNESS_DIR:-$HOME/.claude/harness}/plugin-root"
```

Depois use o valor retornado como prefixo. `scriptPath` vai para a tool, **não para um shell** — substitua `<PLUGIN_ROOT>` pelo caminho literal que o comando acima imprimiu:

```
Workflow({
  scriptPath: "<PLUGIN_ROOT>/scripts/workflows/wf-verify-multimodel.js",
  args: { task_id, changed_files: [...], spec_path: "docs/specs/<slug>-spec.md", base_ref: "HEAD",
          repo_root: "<raiz do worktree da task>" }
})
```

`repo_root` diz a revisores e adjudicador onde está o código: sem ele, eles leem
caminho relativo no diretório da sessão, que pode ser outro worktree.

Retorna JSON. Workflows disponíveis (validar com `node scripts/workflows/validate_workflows.cjs`):
- `wf-verify-multimodel.js` → `{ pass, critical_count, nao_adjudicados, findings[], nos_mortos[], summary }` (5 dimensões em paralelo + adjudicação adversarial de crítico/alto). `pass` só é `true` com zero crítico/alto confirmado, zero nó morto **e zero achado não adjudicado**: finding não julgado não é finding liberado. Cada finding traz `n` (posição) e `adjudicado`; os não adjudicados vão para a triagem da seção "verify (com gap-closure)".
- `wf-context-scan.js` → `{ files[], patterns[], constraints[], risks[], cobertura }` (exploração paralela do codebase). `cobertura.angulos_mortos` nomeia o que faltou no mapa.
- `wf-grill.js` → `{ perguntas[], bloqueantes, lentes_mortas[], cobertura, summary }` (5 lentes adversariais sobre a spec, em janelas novas). Recebe **só** `spec_path` e `context_path` — nunca a conversa que produziu a spec. `lentes_mortas` não-vazio significa cobertura incompleta, não ausência de achado.

> Workflows exigem opt-in do usuário; **esta skill instruir a chamada já é o opt-in válido**. Não bloqueiam interação — toda decisão humana ocorre em gate ENTRE Workflows.

### Regras de fan-out

Quatro regras antes de abrir qualquer Workflow. Custam uma linha cada e evitam as
falhas que não avisam.

**1. Teste da aresta falsa — decide se há grafo.** Percorra o trabalho passo a
passo e pergunte em cada um: *este passo precisa do resultado do anterior?* Se
sim, a aresta é real, mantenha a ordem. Se não, não há aresta — a espera é
desperdício e os dois jobs rodam juntos. **Se você não encontrar dois jobs sem
aresta entre eles, não abra Workflow.** É um loop, e loop está certo: a
coordenação seria puro overhead. O mesmo vale quando o usuário quer aprovar cada
passo, quando o trabalho é exploratório, ou quando a tarefa é pequena e isolada.

**2. Censo de nós — conte antes de filtrar.** `filter(Boolean)` descarta agente
morto em silêncio. Numa cadeia, um nó morto para tudo e é óbvio; num fan-out, um
nó morto entre duzentos entra num relatório com cara de completo. Compare o
número de retornos com o número esperado, nomeie quem morreu, e **não deixe
`pass: true` sair de cobertura incompleta** — "nada encontrado" e "ninguém
procurou" são resultados diferentes. Implementado como `censoNos` em
`scripts/workflows/wf-verify-multimodel.js` e `wf-context-scan.js`.

**3. Fan-in em camadas — antes que a síntese estoure.** Fan-out largo cujo merge
lê todas as saídas de uma vez estoura o contexto antes de sintetizar qualquer
coisa. Quebre em lotes, resuma cada lote, sintetize os resumos: o nó final lê 25
resumos, não mil saídas brutas.

```javascript
const lotes = chunk(resultados, 40)
const resumos = await parallel(lotes.map((l) => () => agent('resuma este lote', { input: l })))
return agent('escreva a resposta a partir dos resumos', { input: resumos })
```

**4. Descontaminar a aresta — decida o que NÃO viaja.** Um nó verificador só vale
porque a janela dele é nova: verificação adversarial funciona por **contexto
descorrelacionado**. Se a aresta que chega nele carrega o raciocínio de quem
produziu o resultado, as duas pontas voltam a correlacionar e o viés de
confirmação retorna — o refutador ancora na narrativa antes de abrir o arquivo, e
o relatório sai com carimbo de revisado.

Regra: para um nó que julga, a aresta carrega **alegação + localização +
critério**. Nunca a justificativa, nunca a conversa que gerou o artefato, nunca o
`CONTEXT.md` de quem escreveu. O raciocínio segue no *retorno*, para o humano ler
— não no *prompt*, para a máquina imitar.

Vale para toda fase de julgamento, não só código: `wf-verify-multimodel` (o
adjudicador recebe a alegação, não o `rationale`) e `wf-grill` (as cinco lentes
recebem o caminho da spec, não o brainstorming que a produziu). Travado em
`tests/test_workflow_returns.py::test_adjudicador_nao_recebe_rationale`.

> A pergunta que fecha as quatro regras: *que contexto viaja nesta aresta, e por
> quê?* Regra 1 decide se a aresta existe; a 4 decide o que ela carrega.

### Gates (decisões humanas via AskUserQuestion)

Ao atingir uma fronteira de decisão: grave `pending_gate` no state, marque `status: "awaiting_gate"`, use `AskUserQuestion`. Após resolver: limpe `pending_gate`, volte `status: "active"`, prossiga.

| Gate | Quando | Pergunta |
|---|---|---|
| `answer_clarifications` | spec tem `[NEEDS CLARIFICATION]` | uma pergunta por ambiguidade (com opções sugeridas) |
| `approve_spec` | após `grill-me` | Aprovar spec / Revisar / Cancelar |
| `approve_plan` | após `design-doc`/`validate-plan` | Aprovar plano / Revisar / Cancelar |
| `escalation` | `verify` falha após 2 gap-closures | mostrar gaps e pedir direção |

"Revisar" re-invoca a fase anterior; clarifications respondidas são gravadas na spec.

### Verify + gap-closure

O protocolo inteiro (rodadas, bloqueio, triagem, checador, ordem até o
`complete`) está na seção **"verify (com gap-closure)"**, mais abaixo. Resumo: no
máximo 3 rodadas; bloqueio (crítico/alto confirmado ou nó morto) vai ao
gap-closure; todo achado não adjudicado de toda rodada recebe destino escrito;
DONE só com `checar_triagem.py` saindo 0.

## Steps novos (v2.1)

### discuss
Alinhamento upstream com usuario. Gera `docs/CONTEXT.md` com decisoes Locked/Deferred/Discretion. Todas as etapas downstream DEVEM ler e respeitar CONTEXT.md. Invocar via `Skill(skill="discuss")`.

### validate-plan
Verificacao pre-execucao. Verifica se plano cobre todos requisitos do CONTEXT.md/PRD. Detecta gaps, auto-revisa ate 2x. Invocar via `Skill(skill="validate-plan")`.

## Steps novos (v3.0 — SDD)

### write-spec
Gera spec formal completa com user stories priorizadas (P1/P2/P3), acceptance criteria Given/When/Then, boundaries ALWAYS/NEVER/ASK, e `[NEEDS CLARIFICATION]` para ambiguidades. Substitui `write-a-prd` em L2. Invocar via `Skill(skill="write-spec")`.

Artefato: `docs/specs/{feature-slug}-spec.md`

### write-spec-light
Versão enxuta para L1 (~50 linhas: objetivo, REQs, ACs Given/When/Then, boundaries mínimas). Invocar via `Skill(skill="write-spec-light")`.

Artefato: `docs/specs/{feature-slug}-spec-light.md`

### design-doc
Gera design técnico separado (arquitetura, data model, API contracts, test strategy, risks) a partir de spec aprovada. Usado em L2 entre `grill-me` e `validate-plan`. Invocar via `Skill(skill="design-doc")`.

Artefato: `docs/specs/{feature-slug}-design.md`

### verify-against-spec
Estende `verify` com verificação item-por-item da spec: cada REQ, AC, US, boundary e success criterion é checado com evidência concreta. Gera report de cobertura e lista de gaps. Invocar via `Skill(skill="verify-against-spec")`.

Artefato: `docs/specs/{feature-slug}-verification.md`

### Backward compatibility v3
- `write-a-prd` ainda funciona (legacy). Novos pipelines L1/L2 usam `write-spec`/`write-spec-light`.
- `verify` tradicional ainda funciona. Novos pipelines usam `verify-against-spec`.
- Pipelines L1/L2-bug e L1-refactor mantêm fluxo antigo (bug/refactor não precisa de spec formal).

## Related patterns — Advisor Strategy (Anthropic 2026-04-09)

A Anthropic lançou a **Advisor Strategy** um dia antes do Harness v3 ser implementado. É uma **primitiva da Claude Platform API** (`advisor_20260301` tool type) que permite Sonnet/Haiku consultar Opus mid-generation dentro de uma única request `/v1/messages`. Opera em camada diferente do Harness v3.

### Distinção clara
| Camada | Tecnologia | Granularidade |
|--------|-----------|---------------|
| **Advisor Strategy** | API Messages, intra-turn | Modelo executor consulta Opus via tool call, mid-generation |
| **Harness v3** | Claude Code CLI, inter-turn | Pipeline de skills sequenciais com humano no loop |
| **Multi-model review (v3 verify)** | Subagents paralelos | Codex+Gemini revisam pós-execução |

### Por que são complementares, não concorrentes
- Advisor Strategy é **pre-decisão mid-generation** (Opus opina ANTES do executor gerar resposta)
- Multi-model review v3 é **pós-decisão** (revisa diff depois do Claude escrever)
- `grill-me` é **humano-no-loop adversarial** (questiona para humano decidir)
- Advisor Strategy é **modelo-no-loop colaborativo** (consulta outro modelo automaticamente)

### O que o Harness v3 já cobre do espírito advisor (sem o nome):
1. `grill-me` — adversarial pré-execução (humano decide)
2. Multi-model review — Codex+Gemini como advisors pós-execução
3. `validate-plan` — gate antes de tdd
4. `autoresearch:predict` — multi-persona pré-análise

### Quando considerar incorporar Advisor Strategy
**NÃO** refatorar pipelines atuais para incluir advisor step. **NÃO** criar skill `advisor` (confunde orchestration vs API primitive).

**SIM** quando/se o Harness v3 migrar etapas para execução não-interativa via Agent SDK (scope futuro F6 do plano harness4claude). Nesse caso, habilitar `advisor_20260301` na chamada API faz sentido para que o executor Sonnet possa escalonar para Opus dentro da etapa sem round-trips extras. Benchmarks: Sonnet +2.7pp SWE-bench com -11.9% custo.

**Referência completa:** `~/.claude/projects/C--Windows-System32/memory/reference_advisor_strategy.md`

### verify (com gap-closure)
A fase de verificação no fim de cada pipeline:

1. **L1** → `Skill(skill="verify-against-spec")`. **Bug** → `Skill(skill="superpowers:verification-before-completion")` (testes/lint/type-check). **L2** → o Workflow `wf-verify-multimodel`, pelo protocolo de rodadas abaixo.
2. Apresentar a tabela de `findings` retornada.

**Protocolo de rodadas (L2).** Até 2026-09-30 o DONE saía de `pass: true`, e o
`pass` contava só crítico/alto confirmado: três rodadas seguidas foram a DONE com
25 defeitos reais entre 49 achados abertos, dois deles caminhos de evidência
verde forjada (`docs/specs/verify-adjudica-tudo-medicao.md`). Hoje o `pass` só é
`true` sem achado aberto, e o DONE exige destino escrito para cada achado não
adjudicado, conferido por script (spec `verify-adjudica-tudo`, REQ-F8).

1. **Rodada** = uma execução do Workflow. No máximo **3 por task** (a inicial e
   mais 2), um contador só para bloqueio e triagem. Passe `repo_root` com a raiz
   do worktree da task.
2. **Copie a saída logo depois da rodada.** O `.output` do host (o
   `<output-file>` da notificação) some em horas; a cópia fica no balde da task,
   fora do repo, em `<STATE_DIR>/verify/<run id>.json` (`<run id>` = o `wf_…`
   que o tool devolveu). Nunca edite a cópia. Execução que o host abortou (saída
   sem `result`) não é rodada: não a copie — ela conta como nó morto (passo 6).
   Se já copiou, mova a cópia para fora de `<STATE_DIR>/verify/` e diga no
   closure-plan qual execução era; o checador sai 2 enquanto ela estiver lá.
   ```bash
   mkdir -p "<STATE_DIR>/verify"
   cp "<output-file da notificação>" "<STATE_DIR>/verify/<run id>.json"
   ```
   ```powershell
   New-Item -ItemType Directory -Force "<STATE_DIR>/verify"
   Copy-Item "<output-file da notificação>" "<STATE_DIR>/verify/<run id>.json"
   ```
3. **Bloqueio** (crítico/alto confirmado, ou `nos_mortos` não vazio) → gap-closure:
   delta-tasks no `docs/closure-plan.md`, conserto com teste.
4. **Triagem.** Todo achado com `adjudicado: false`, de toda rodada, com ou sem
   bloqueio, ganha uma linha na seção `## Destino dos achados não adjudicados` do
   `closure-plan.md`:
   - `consertado` — código ou teste mudou; a referência diz o teste que pega o defeito ou o commit;
   - `limite` — comportamento real aceito; a referência diz o motivo;
   - `nao-defeito` — falso positivo ou fora do diff; a referência diz por quê.

   Limite que reaparece numa rodada seguinte ganha linha nova (a referência pode
   apontar a decisão anterior). Não filtre achado já declarado limite: na medição,
   um deles era defeito e só foi consertado porque voltou.
5. **Conserto de código** (bloqueio ou triagem) → nova rodada, obrigatória se
   ainda houver rodada. Antes dela, o checador não pode apontar `PENDENTE`.
6. **Nó morto sem código mudado**: rodar de novo não conta no limite; um segundo
   nó morto seguido vai ao gate `escalation`.
7. **3ª rodada**: bloqueio → gate `escalation`. Conserto pedido pela triagem
   fecha com teste que pegue o defeito, e o relatório de verificação lista esses
   **consertos sem rodada de revisão**. Se o usuário decidir fechar com o
   bloqueio consertado e sem rodada nova, o checador seguirá saindo 1 pelo
   `BLOQUEIO` da cópia da 3ª rodada (a cópia não se edita): o fechamento vale
   com a tabela completa — nenhum `PENDENTE` nem `INVALIDA` —, e o closure-plan
   registra a decisão com data e o conserto com teste e mutante.
8. **Checador**, antes de cada rodada nova e antes do `complete`:
   ```bash
   python "<PLUGIN_ROOT>/scripts/checar_triagem.py" --plano "docs/closure-plan.md" --saidas "<STATE_DIR>/verify"
   ```
   Sai 0 (tudo decidido, última rodada sem bloqueio), 1 (pendente, linha
   inválida ou bloqueio — imprime as linhas prontas para colar: troque destino e
   referência) ou 2 (cópia ilegível).
9. **Ordem no fim:** tabela → checador saindo 0 → evidência de teste final →
   `state_cli complete`. Escrita depois da evidência a transforma em histórico, e
   o hook conta `python` como escrita: o checador vem antes da evidência.
10. **No DONE**, mostre a tabela de destinos ao usuário — `limite` e
    `nao-defeito` são decisão de quem escreveu o código, e a medição achou quatro
    "limites" que eram defeito.

**closure-plan.md** deve conter:
```markdown
# Closure Plan — [task_id]
## Gaps encontrados
- [gap 1]: [descricao + evidencia]
## Delta tasks
- [ ] [task especifica para fechar gap 1]
## Destino dos achados não adjudicados
| Rodada | # | Achado | Destino | Referência |
|---|---|---|---|---|
| wf_… | 7 | início do título do achado (20+ caracteres) | consertado | teste, commit ou motivo |
## Origem
- Verificacao que falhou: [qual teste/check]
- Rodada: 1 de 3
```

## Modelos secundários (Codex/Gemini — opcional)

> **Status:** o review multi-perspectiva primário é o Workflow `wf-verify-multimodel` (agents Claude em paralelo, 5 dimensões + adjudicação). Codex/Gemini são **secundários opcionais**. O plugin `multi-model@local` está **desabilitado** — NÃO dependa dele nem leia `routing.json`.

### Princípio

Claude é sempre o primário (via `wf-verify-multimodel`). Codex/Gemini agregam diversidade de modelo quando disponíveis; nunca bloqueiam o pipeline.

### Como incluir um secundário (quando agregar valor)

- **Codex** (plugin `codex@openai-codex`, habilitado): `Skill(skill="codex:rescue")` ou um agent `codex-rescue` pedindo review do diff.
- **Gemini:** se houver agent/MCP de Gemini conectado, lançá-lo via Agent tool em background com o contexto do stage.
- **Dentro de um Workflow:** dar a um reviewer um `agentType` custom (ex.: `agent(prompt, { agentType: 'codex-rescue', schema })`) para diversificar o fan-out do `wf-verify-multimodel`.

### Degradação graciosa

- Secundário indisponível → pular, seguir com o resultado do `wf-verify-multimodel`.
- Timeout → marcar "skipped", seguir.
- **O pipeline NUNCA trava por causa de modelo secundário.**

## Configurações padrão do autoresearch por etapa

| Etapa | Iterations | Guard | Flags | Quando ajustar |
|---|---|---|---|---|
| `autoresearch:debug` L1 | 10 | — | `--scope <arquivos afetados>` | Aumentar se bug complexo |
| `autoresearch:debug` L2 | 15 | — | `--scope <módulo>` | Aumentar se multi-módulo |
| `autoresearch:fix` L1 | 20 | `pytest` | `--from-debug` se veio de debug | Aumentar se muitos findings |
| `autoresearch:fix` L2 | 30 | `pytest` | `--from-debug` | Aumentar se regressões |
| `autoresearch:security` | 15 | `pytest` | `--fail-on high` | `--fix` para auto-corrigir |
| `autoresearch:predict` | — | — | `--depth standard`, `--chain` se encadear | `--depth deep` para decisões críticas |
| `autoresearch:ship` | — | — | `--dry-run` primeiro | `--auto` se CI/CD confiável |

**Princípio:** Iterations são defaults — o agente ou o usuário podem ajustar conforme a complexidade real da tarefa. Guard é sempre `pytest` para projetos Python (detectado automaticamente).

## State management

A cada transição de etapa, atualizar `~/.claude/harness/state.json`:

```json
{
  "task_id": "t-YYYYMMDD-HHMMSS",
  "classification": "L2-feature",
  "status": "active",
  "pipeline": ["brainstorming", "write-a-prd", "grill-me", "prd-to-plan", "tdd"],
  "current_step": "grill-me",
  "artifacts_so_far": ["docs/prd/feature-name.md"],
  "started_at": "ISO timestamp"
}
```

Use o Edit tool para atualizar state.json. Custo: ~20 tokens por transição.

## DONE — registrar métricas

Ao completar (ou abandonar) o pipeline, **NÃO edite `signals.json` à mão**. Use o helper:

```bash
# Resolva uma vez, no INICIO do pipeline, e anote os tres valores literais:
#   <HARNESS_ROOT>  = $HARNESS_DIR, ou ~/.claude/harness (leia com Read)
#   <PLUGIN_ROOT>   = conteudo de <HARNESS_ROOT>/plugin-root (leia com Read)
#   <STATE_DIR>     = saida de harness_paths.py (passo 2 do Protocolo)
# state.json e o contador vivem no balde; signals.json e agregado na raiz.
#
# UMA CHAMADA POR LINHA. Nada de `;`, `&&`, `$( )` ou barra de continuacao:
# comando composto perde a isencao do contador de escrita. Ver Protocolo,
# passo 2.

# Pipeline concluído com sucesso (--expect-task = task_id do INÍCIO do pipeline;
# aborta com exit 2 se o state foi trocado por outra sessão no meio)
python "<PLUGIN_ROOT>/scripts/record_signal.py" --completed --steps "discuss,write-spec,grill-me,design-doc,tdd,verify-against-spec" --expect-task "t-20260612-033900" --harness-dir "<STATE_DIR>" --signals-dir "<HARNESS_ROOT>"

# Tarefa abandonada (troca de assunto / cancelamento). --expect-task e
# obrigatorio aqui: na troca de assunto a projecao ja e da task NOVA, e o
# abandono sem o id encerraria justamente ela.
python "<PLUGIN_ROOT>/scripts/record_signal.py" --abandoned --reason "user_switch" --expect-task "t-20260612-033900" --harness-dir "<STATE_DIR>" --signals-dir "<HARNESS_ROOT>"
```

> **Escopo do estado (desde 2026-07-28).** `state.json`, `.session-files-count` e
> os traces ficam em `$ROOT/projects/<slug>/`, um bucket por repositório. Antes
> havia um único state para a máquina inteira: o contador chegou a 130 arquivos
> sob um mesmo `task_id`, misturando dois projetos, e a promoção L0→L1 de um repo
> era disparada por edições em outro. `HARNESS_SCOPE=global` restaura o
> comportamento antigo. `signals.json` continua na raiz de propósito — a
> telemetria é agregada e seus registros são chaveados por `task_id`.

O script (idempotente por `task_id`):
1. Com `--expect-task` e a task no `harness.db`: lê `task_id`, `classification`,
   `classification_meta` e arquivos do banco, pelo id. Senão, do `state.json`
2. Conta `files_modified` (banco ∪ contador, o contador só se for da task) e deriva
   `actual_level` (0-1=L0, 2-3=L1, 4+=L2)
3. Acrescenta/atualiza a task em `signals.json` → array `tasks`
4. Recalcula `aggregates`, incluindo o bloco `classify` (`avg_classify_accuracy`,
   `regex_vs_semantic_agreement`, `human_override_count`) — fechando o loop de feedback
5. Com `--abandoned` (exige `--expect-task`), encerra a task esperada como
   `abandoned` no `harness.db` — nunca a da projeção
6. Não escreve `state.json` nem o contador

**Não edite o `state.json` à mão para encerrar.** Desde 2026-09-23 (ramo
`ciclo-de-vida-da-task`) quem responde "há task viva?" é o `harness.db`, não a
projeção: `state_cli.py complete` encerra a task concluída, `record_signal.py
--abandoned` encerra a abandonada, e o `state.json` só acompanha. Uma projeção
editada para `done` com a task viva no banco é reescrita no prompt seguinte, e a
task volta como CONTINUING.

## Artefatos

- **Artefatos de projeto** → `./docs/` (PRDs, planos, issues)
- **Estado operacional** → `~/.claude/harness/` (state.json, signals.json)

## Princípios

- **CLAUDE.md é rei** — usuário pode override qualquer pipeline
- **Degradação graceful** — se uma skill não existe, pula e continua
- **Grill-me sem limite** — convergência natural, não contagem
- **Artefatos são o trace** — PRDs e planos gerados são evidência natural do trabalho

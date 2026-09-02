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
   ROOT="${HARNESS_DIR:-$HOME/.claude/harness}"; PR="$(cat "$ROOT/plugin-root")"
   python "$PR/scripts/confirm_classification.py" \
     --final "<L1-feature|L2-bug|...>" --expect-task "<task_id>" \
     --harness-dir "$(python "$PR/scripts/harness_paths.py")"
   ```

   - **Concorda** → passe `--final` igual ao `suggested`; o script grava `agreed = true`.
   - **Discorda** (ex.: regex marcou L2 por conter "feature", mas é uma adição L1 pequena; ou o oposto) → passe o `--final` correto: o script grava `agreed = false`, corrige `classification` e **troca `pipeline`** sozinho, lendo `scripts/pipelines.json`.
   - Se o usuário corrigir explicitamente depois → rode de novo com `--source human_override`.
   - **Não edite `classification_meta` à mão.** Esse era o protocolo anterior e ele não era cumprido: a auditoria de 2026-07-28 encontrou `agreed = null` em 100% das tasks e `avg_classify_accuracy = null` desde sempre, porque `recompute_aggregates` só conta tasks com `agreed is not None`. Sem este passo a métrica de accuracy é matematicamente incapaz de sair de zero.
3. **Anunciar** — exiba: "Harness v3: {level}-{type} → {pipeline}" (sinalize se houve correção semântica).
4. **Atualizar estado** — registre artefato e avance com `state_cli.py`, sempre passando a revisão esperada; o helper sincroniza `state.json`.
5. **Invocar skills** — na sequência do pipeline, usando Skill tool.
6. **Obrigações** — uma fase existente pode reutilizar artefato válido, mas a transição e sua evidência continuam registradas.
7. **DONE** — grave evidência fresca, execute `state_cli.py ... complete`, então registre a task:
   ```bash
   ROOT="${HARNESS_DIR:-$HOME/.claude/harness}"; PR="$(cat "$ROOT/plugin-root")"
   python "$PR/scripts/record_signal.py" --completed --steps "step1,step2,..." \
     --expect-task "<task_id>" \
     --harness-dir "$(python "$PR/scripts/harness_paths.py")" --signals-dir "$ROOT"
   ```
   (grava em `signals.json` com `classification_meta` e recalcula `avg_classify_accuracy`; idempotente por `task_id`). Para troca de tarefa antes do fim: `--abandoned --reason "<motivo>"`.
   **Sempre passe `--expect-task` com o task_id anotado no INÍCIO do pipeline**: se o `state.json` global tiver sido sobrescrito por outra sessão no meio do caminho (incidente 2026-06-12), o script aborta com exit 2 em vez de registrar uma task fantasma — nesse caso, restaure o state da sua task antes de registrar.

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
  args: { task_id, changed_files: [...], spec_path: "docs/specs/<slug>-spec.md", base_ref: "HEAD" }
})
```

Retorna JSON. Workflows disponíveis (validar com `node scripts/workflows/validate_workflows.cjs`):
- `wf-verify-multimodel.js` → `{ pass, critical_count, findings[], nos_mortos[], summary }` (5 dimensões em paralelo + adjudicação adversarial que refuta falsos-positivos). `nos_mortos` não-vazio força `pass: false`: finding não julgado não é finding liberado.
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

Quando a verificação retorna `pass: false`:
1. Liste os `findings` bloqueantes (critical/high).
2. Gere `docs/closure-plan.md` com APENAS as delta-tasks.
3. Execute as delta-tasks e re-rode a verificação.
4. Máx. 2 iterações; se ainda falhar → gate `escalation`.

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

1. **L2** → rodar o Workflow `wf-verify-multimodel` (fan-out de 5 dimensões em paralelo + adjudicação adversarial). **L1** → `Skill(skill="verify-against-spec")`. **Bug** → `Skill(skill="superpowers:verification-before-completion")` (testes/lint/type-check).
2. Apresentar a tabela de `findings` retornada (já deduplicada e filtrada por confidence pelo Workflow).
3. Se `pass: true` (sem findings critical/high) → pipeline completo, ir para DONE
5. Se FAIL → analisar o que faltou:
   a. Listar gaps especificos (teste falhando, requisito nao implementado, finding critico do review multi-modelo)
   b. Gerar `docs/closure-plan.md` com APENAS as delta-tasks necessarias
   c. Executar as delta-tasks
   d. Re-verificar (volta ao passo 1)
6. Max 2 iteracoes de gap-closure. Se apos 2 ainda falhar → escalar ao usuario

**closure-plan.md** deve conter:
```markdown
# Closure Plan — [task_id]
## Gaps encontrados
- [gap 1]: [descricao + evidencia]
## Delta tasks
- [ ] [task especifica para fechar gap 1]
## Origem
- Verificacao que falhou: [qual teste/check]
- Iteracao: 1 de 2
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
# Resolva uma vez: raiz do harness, plugin, e o bucket DESTE projeto.
# state.json e o contador vivem no bucket; signals.json e agregado na raiz.
ROOT="${HARNESS_DIR:-$HOME/.claude/harness}"
PR="$(cat "$ROOT/plugin-root")"
STATE_DIR="$(python "$PR/scripts/harness_paths.py")"

# Pipeline concluído com sucesso (--expect-task = task_id do INÍCIO do pipeline;
# aborta com exit 2 se o state foi trocado por outra sessão no meio)
python "$PR/scripts/record_signal.py" --completed \
  --steps "discuss,write-spec,grill-me,design-doc,tdd,verify-against-spec" \
  --expect-task "t-20260612-033900" \
  --harness-dir "$STATE_DIR" --signals-dir "$ROOT"

# Tarefa abandonada (troca de assunto / cancelamento)
python "$PR/scripts/record_signal.py" --abandoned --reason "user_switch" \
  --harness-dir "$STATE_DIR" --signals-dir "$ROOT"
```

> **Escopo do estado (desde 2026-07-28).** `state.json`, `.session-files-count` e
> os traces ficam em `$ROOT/projects/<slug>/`, um bucket por repositório. Antes
> havia um único state para a máquina inteira: o contador chegou a 130 arquivos
> sob um mesmo `task_id`, misturando dois projetos, e a promoção L0→L1 de um repo
> era disparada por edições em outro. `HARNESS_SCOPE=global` restaura o
> comportamento antigo. `signals.json` continua na raiz de propósito — a
> telemetria é agregada e seus registros são chaveados por `task_id`.

O script (idempotente por `task_id`):
1. Lê `task_id`, `classification` e `classification_meta` do `state.json`
2. Lê `files_modified` do counter e deriva `actual_level` (0-1=L0, 2-3=L1, 4+=L2)
3. Acrescenta/atualiza a task em `signals.json` → array `tasks`
4. Recalcula `aggregates`, incluindo o bloco `classify` (`avg_classify_accuracy`,
   `regex_vs_semantic_agreement`, `human_override_count`) — fechando o loop de feedback

Depois, marque `status: "done"` no `state.json` com `Edit`.

## Artefatos

- **Artefatos de projeto** → `./docs/` (PRDs, planos, issues)
- **Estado operacional** → `~/.claude/harness/` (state.json, signals.json)

## Princípios

- **CLAUDE.md é rei** — usuário pode override qualquer pipeline
- **Degradação graceful** — se uma skill não existe, pula e continua
- **Grill-me sem limite** — convergência natural, não contagem
- **Artefatos são o trace** — PRDs e planos gerados são evidência natural do trabalho

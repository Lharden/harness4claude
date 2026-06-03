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

## Quando ativar

Ative quando o contexto contiver `<harness-classification>` com `level: L1` ou `level: L2`.
Para L0, NÃO ative — execute direto sem pipeline.

## Protocolo

1. **Ler classificação** — extraia level, type, pipeline do bloco injetado pelo hook. O `state.json` traz `classification_meta.suggested` (vindo do **regex**, rápido/offline) com `final: null` para L1+.
2. **Confirmar classificação (camada semântica)** — ANTES de anunciar, avalie a intenção REAL do prompt do usuário e compare com o `suggested`:
   - **Concorda** → grave `classification_meta.final = suggested`, `source = "regex"`, `agreed = true`.
   - **Discorda** (ex.: regex marcou L2 por conter "feature", mas é uma adição L1 pequena; ou o oposto) → corrija: grave `classification_meta.final = <novo>`, `source = "semantic"`, `agreed = false`, atualize `classification` (string) e **troque `pipeline`** para o do novo level/type (ver tabela Pipelines).
   - Grave com `Edit` no `state.json`. Isso alimenta o loop de accuracy (regex × semântica) em `signals.json`.
   - Se o usuário corrigir explicitamente depois → `source = "human_override"`.
3. **Anunciar** — exiba: "Harness v3: {level}-{type} → {pipeline}" (sinalize se houve correção semântica).
4. **Atualizar state.json** — marcar `current_step` conforme progride no pipeline.
5. **Invocar skills** — na sequência do pipeline, usando Skill tool.
6. **Flexibilidade** — pular etapas se justificar (ex.: spec já existe, bug óbvio).
7. **DONE** — marcar `status: done` e registrar a task executando:
   `python ~/.claude/plugins/local/harness4claude/scripts/record_signal.py --completed --steps "step1,step2,..."`
   (grava em `signals.json` com `classification_meta` e recalcula `avg_classify_accuracy`; idempotente por `task_id`). Para troca de tarefa antes do fim: `--abandoned --reason "<motivo>"`.

## Pipelines

Os nomes abaixo são **fases** (espelham `PIPELINES` em `harness-classify.sh`). O hook grava a fase-lista em `state.json.pipeline`; a seção **Motor de Execução** mapeia cada fase ao mecanismo real (skill direta, Workflow de fan-out, ou gate humano).

| Classificação | Fases |
|---|---|
| **L1-feature** | write-spec-light → tdd → verify-against-spec |
| **L1-bug** | systematic-debugging → tdd → verify |
| **L1-refactor** | write-spec-light → tdd → verify-against-spec |
| **L2-feature** | discuss → brainstorming → write-spec → grill-me → design-doc → validate-plan → tdd → verify-against-spec |
| **L2-bug** | systematic-debugging → grill-me → tdd → verify |
| **L2-refactor** | discuss → write-spec → grill-me → design-doc → validate-plan → tdd → verify-against-spec |
| **L2-architecture** | discuss → brainstorming → write-spec → grill-me → design-doc → validate-plan → tdd → verify-against-spec |

> **Zero skills fantasma:** removidos `triage-issue`, `request-refactor-plan`, `improve-codebase-architecture`, `prd-to-plan`, `write-a-prd`, `execucao`. Cada fase mapeia a um mecanismo real.
> **autoresearch (acelerador opcional, não obrigatório):** em bugs, `tdd`/`verify` podem usar `autoresearch:debug`/`autoresearch:fix` (loops com guard pytest); em L2 com auth/dados/API, rodar `autoresearch:security`; em L2-architecture, `autoresearch:predict` para pré-análise. Se o plugin estiver indisponível, seguir sem ele (degradação graceful).

## Motor de Execução: Workflows + Gates

> **Princípio:** fases SEM humano podem virar **Workflow** (fan-out determinístico em background); fronteiras COM humano são **gates** (`AskUserQuestion`) ENTRE as fases. O `state.json` é a máquina de estados que sobrevive entre turnos — `status: "awaiting_gate"` é retomado pelo hook no próximo prompt.

### Mapa fase → mecanismo

| Fase | Mecanismo | Como |
|---|---|---|
| (classificação) | inline | confirmar semanticamente (Protocolo, passo 2) |
| `discuss` | skill | `Skill(skill="discuss")` → `docs/CONTEXT.md` |
| `brainstorming` | skill | `Skill(skill="superpowers:brainstorming")` |
| (contexto, L2-arch) | **Workflow** | `wf-context-scan` (fan-out de exploração) — opcional |
| `write-spec` / `write-spec-light` | skill | `Skill(skill="write-spec[-light]")` |
| `grill-me` | skill (humano-no-loop) | `Skill(skill="grill-me")` — adversarial, sem limite |
| `design-doc` | skill | `Skill(skill="design-doc")` |
| `validate-plan` | skill | `Skill(skill="validate-plan")` |
| `tdd` | skill | `Skill(skill="superpowers:test-driven-development")` |
| `verify-against-spec` (L2) | **Workflow** | `wf-verify-multimodel` (review multi-perspectiva + adversarial) |
| `verify-against-spec` (L1) | skill | `Skill(skill="verify-against-spec")` (Workflow opcional) |
| `verify` (bug) | skill | `Skill(skill="superpowers:verification-before-completion")` |

### Invocando um Workflow

Na fase mapeada para Workflow, chame a tool **Workflow** com o script e os `args`:

```
Workflow({
  scriptPath: "~/.claude/plugins/local/harness4claude/scripts/workflows/wf-verify-multimodel.js",
  args: { task_id, changed_files: [...], spec_path: "docs/specs/<slug>-spec.md", base_ref: "HEAD" }
})
```

Retorna JSON. Workflows disponíveis (validar com `node scripts/workflows/validate_workflows.cjs`):
- `wf-verify-multimodel.js` → `{ pass, critical_count, findings[], summary }` (5 dimensões em paralelo + adjudicação adversarial que refuta falsos-positivos).
- `wf-context-scan.js` → `{ files[], patterns[], constraints[], risks[] }` (exploração paralela do codebase).

> Workflows exigem opt-in do usuário; **esta skill instruir a chamada já é o opt-in válido**. Não bloqueiam interação — toda decisão humana ocorre em gate ENTRE Workflows.

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
# Pipeline concluído com sucesso
python ~/.claude/plugins/local/harness4claude/scripts/record_signal.py --completed \
  --steps "discuss,write-spec,grill-me,design-doc,tdd,verify-against-spec"

# Tarefa abandonada (troca de assunto / cancelamento)
python ~/.claude/plugins/local/harness4claude/scripts/record_signal.py --abandoned --reason "user_switch"
```

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

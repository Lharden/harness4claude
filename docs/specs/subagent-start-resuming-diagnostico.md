# SubagentStart mandava todo subagente carregar o orquestrador

**Status:** conserto em TDD · aberto em 2026-09-30
**Decisão:** o SubagentStart nunca emite RESUMING (usuário, 2026-09-30)
**Código:** `hooks/harness-lifecycle.py` · **Testes:** `tests/test_lifecycle_context.py`

## 1. Sintoma

No run `wf_8ec3454a-257` (`scripts/workflows/wf-grill.js`, 2026-09-28), lentes
que existem para ler só a spec em janela nova carregaram a skill `harness-workflow`
antes de responder. O `wf-grill` depende de contexto descorrelacionado (regra 4 de
fan-out em `skills/harness-workflow/SKILL.md`, "Descontaminar a aresta").

## 2. Causa

`hooks/harness-lifecycle.py`, evento SubagentStart, emitia para todo subagente:

> HARNESS v3 RESUMING: scoped task … Invoke skill='harness-workflow' and continue
> from this exact state. Return a NodeResult with role, status, findings, …

O texto carregava task, classificação, fase, gate pendente e artefatos, e mandava
o subagente assumir o pipeline do pai. A linha do NodeResult contradizia o schema de
`StructuredOutput` que cada Workflow passa ao agente. `contract/schemas/node-result.schema.json`
não tem consumidor em código. Origem: `058356a` (2026-08-31), paridade de contrato
com o harness4codex.

## 3. Medição

Instrumento: o `usage` gravado pela produção em cada mensagem do transcript,
deduplicado por `message.id`, e não uma estimativa de tokens.

### 3.1 O run do grill

| Lente | Carregou a skill | Contexto na 1ª chamada | Contexto na chamada final | Crescimento |
|---|---|---|---|---|
| ambiguidade | sim | 68 365 | 100 190 | 31 825 |
| edge-case | sim | 68 353 | 96 984 | 28 631 |
| dependencia | sim | 68 357 | 99 603 | 31 246 |
| boundary-ausente | não | 68 374 | 83 748 | 15 374 |
| suposicao | não | 68 361 | 83 901 | 15 540 |

- As cinco receberam o RESUMING (`hook_additional_context` no transcript de cada uma).
- Três de cinco chamaram `Skill(harness4claude:harness-workflow)` na primeira
  mensagem, junto com o `Read` da spec. O corpo injetado tem 28 662 caracteres.
- Custo extra por lente, contra o crescimento de uma lente sem a skill
  (15 374 a 15 540): **de 13,2 mil a 16,4 mil tokens**. Soma no run: cerca de **45,5 mil**.
- Nenhuma das três rodou `state_cli`, `confirm_classification` ou `record_signal`.

### 3.2 Todos os subagentes da máquina

1 525 transcripts em `~/.claude/projects/**/subagents/**/agent-*.jsonl`, com
`agent_type` lido do `hookName` `SubagentStart:<agent_type>`:

| agent_type do hook | Origem | Recebeu RESUMING | Carregou a skill |
|---|---|---|---|
| workflow-subagent | Workflow | 1 266 | 29 |
| general-purpose | ferramenta Agent | 74 | 15 |
| Explore | ferramenta Agent | 45 | 1 |
| analise-complexa | Workflow | 26 | 0 |
| analise-complexa | ferramenta Agent | 26 | 1 |
| busca-leve | ferramenta Agent | 27 | 0 |
| juiz-alto-risco | ferramenta Agent | 27 | 0 |
| execucao-mecanica | ferramenta Agent | 25 | 0 |
| Plan | ferramenta Agent | 8 | 3 |
| claude-code-guide | ferramenta Agent | 1 | 0 |

Dois subagentes que carregaram a skill rodaram scripts **sobre a task do pai**:

- `Plan` "Design execution plan for doc assimilation" (master-project-slb-mestrado):
  `confirm_classification.py` e `state_cli.py`.
- `general-purpose` com haiku, "LiteLLM proxy: recursos e riscos" (master-harness):
  `confirm_classification.py` duas vezes.

## 4. Por que não "só para subagente de fase"

Doc oficial (https://code.claude.com/docs/en/hooks, seção SubagentStart): o payload
traz só `agent_id` e `agent_type`, e o matcher filtra por `agent_type`.

- Um Workflow sem `agentType` chega como `workflow-subagent`.
- Um Workflow com `agentType` chega com o nome do agente. `analise-complexa` aparece
  26 vezes de Workflow (`wf-verify-multimodel`) e 26 vezes da ferramenta Agent.
  O adjudicador do `wf-verify-multimodel` é `juiz-alto-risco`, o mesmo nome da
  tabela de roteamento do CLAUDE.md global.
- Nenhum subagente executa fase: as fases rodam na sessão principal, e os
  subagentes são delegados (busca, julgamento, execução pontual).

A regra "RESUMING só para subagente de pipeline" se aplica a um conjunto vazio, e o
payload não conseguiria achar esse conjunto se ele existisse.

## 5. Conserto

- `main()` continua registrando SubagentStart (`lifecycle.db` no balde, heartbeat)
  e não emite nada.
- Saíram `_load_state`, `_resume_message` e `_emit`, que não tinham outro chamador.
- A retomada da sessão principal não muda: sai pelo SessionStart com source `compact`.

## 6. Prova

- `test_subagent_start_registra_e_nao_emite`: roda para cinco `agent_type`
  (`workflow-subagent`, `analise-complexa`, `juiz-alto-risco`, `general-purpose`, `Plan`),
  com uma task L2 viva e um gate pendente. Exige stdout vazio, o evento registrado e o heartbeat.
  Reprovou nos cinco antes do conserto.
- `test_CONTROLE_codigo_antigo_mandava_subagente_carregar_o_orquestrador`: é a metade
  de falsificação. Roda o mesmo cenário, pela mesma função do teste, contra o hook de
  `OLD_REF = 18ec682` (fixado por SHA) e exige o RESUMING com `Invoke skill='harness-workflow'`.
- `test_retomada_pos_compact_chega_pelo_session_start`, que já existia, continua
  passando: a sessão principal segue sendo retomada.

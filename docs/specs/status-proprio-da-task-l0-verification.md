# Verification Report — status-proprio-da-task-l0

**Date**: 2026-10-01
**Status**: PASS
**Pipeline**: L2-docs (`source-selection → graph-context → documentation → verify-against-spec`), task `t-20261001-134350077505`
**Spec**: o prompt-semente do ramo `status-proprio-da-task-l0` (sessão `1efa70a4-87ed-4747-b758-b73c86bc2a42`). Não há arquivo de spec: o ramo é diagnóstico, e o pedido tem quatro entregas e uma restrição.
**Doc verificada**: `docs/specs/status-proprio-da-task-l0-diagnostico.md`

Não observados: **0**. Afirmações descartadas: **0**.

## Pedido do ramo × doc

| # | Pedido | Onde está na doc | Status |
|---|---|---|---|
| P1 | Mapear todo leitor e escritor do status de uma task L0 (`done`, `TERMINAL_STATUSES`, `pipeline_json = '[]'`, `ACTIVE_STATUSES` em `scripts/`, `hooks/`, `contract/`) | "Escritores do status da L0", "Leitores, por pergunta" | PASS |
| P2 | Medir nos `harness.db` da máquina quantas tasks L0 existem e em que status | "Medição" (227 bancos Claude + 1 Codex) | PASS |
| P3 | Diagnóstico curto com as opções (status novo, `done` mantido, outra) | "Opções" A, B, C, D | PASS |
| P4 | Custo de cada uma no contrato com o harness4codex | "Custo medido de cada opção" (linha "Contrato com o harness4codex" e o parágrafo seguinte) | PASS |
| P5 | Conclusão: opção recomendada, custo medido, se depende do fim do P1 | "Recomendação", "Dependência do P1", "Decisões" E1–E4 | PASS |
| R1 | Restrição: caminho do classify congelado; só diagnóstico e desenho | Nenhum arquivo de `scripts/`, `hooks/`, `contract/`, `schemas/` ou `tests/` alterado (`git status --short` mostra só os dois `.md` deste ramo) | PASS |

## Os quatro itens da verificação de docs

### 1. Toda afirmação da tabela de fontes aparece na doc

| Afirmação | Fonte que manda | Nível | Na doc |
|---|---|---|---|
| L0 nasce `done` no banco e na projeção | `transactional_state.py:555`, `harness-classify.sh:746` | 1 | sim |
| A exceção mora em `_desfecho_registrado` | `transactional_state.py:2138-2140` | 1 | sim |
| A L0 é um terceiro ciclo de vida (escopo / acumula / status muda) | leitores de `ACTIVE_STATUSES` e `TERMINAL_STATUSES` | 1 | sim |
| O par (`done`, `[]`) é exato: 941/941 e 7/7 | medição `mode=ro` | medido | sim |
| 7 pontos de escrita em 3 arquivos + 3 no harness4codex | `transactional_state.py:520, 555, 671, 1150`, `harness-classify.sh:746`, `confirm_classification.py:107, 112`, `state_db.py:260, 359, 706` | 1 | sim |
| Com banco, a projeção da confirmação vem do banco | `confirm_classification.py:201-204` | 1 | sim |
| A projeção do classify não recebe o `status` do banco | `harness-classify.sh:802-809` | 1 | sim |
| `_exige_task_viva` chamado por 7 operações | grep `_exige_task_viva(connection` → linhas 642, 944, 1134, 1195, 1224, 1288, 2044/2063 | 1 | sim |
| Segunda cópia do predicado na migração | `transactional_state.py:505` | 1 | sim |
| 85,5% dos `done` são L0 (932/1 090) | medição | medido | sim |
| Residuais: L0 `active` (science-harness) e L0 `done` + `escalation` (RSL_Project) | medição | medido | sim |
| Contrato: 1.2.0 (canônica, vizinha) × 1.1.0 (harness4codex, harness4contract), `superseded` ausente nos dois últimos | `capabilities.json` e `task-state.schema.json` das 4 árvores | 3 | sim |
| Nenhuma quebra em runtime entre hosts | bancos separados; `task.start` é o único evento do harness4claude no spool e não carrega `status` (`harness-classify.sh:856-865`; `harness-session-start.sh` só drena) | 1 | sim |
| harness4codex sem regra de desfecho terminal | `state_db.py:19`; 0 ocorrências de `TERMINAL`/`desfecho`/`superseded` | 1 | sim |
| 13 asserções em 5 arquivos fixam L0 = `done` | grep de `"done"` em `tests/` (55 linhas, 12 arquivos), lidas uma a uma | 2 | sim |
| `rolled_back` sem escritor em `scripts/`/`hooks/` | grep | 1 | sim |
| Decisões E1–E4 | resposta do usuário em 2026-10-01 (AskUserQuestion) | decisão | sim |

### 2. Todo comando de exemplo roda como escrito

A doc não traz comando de exemplo. Os scripts de medição (`medir_l0.py`,
`residuais_l0.py`) e o conferidor de referências ficaram no scratchpad da
sessão; a doc diz que não são versionados.

### 3. Nenhum `[NEEDS CLARIFICATION]` sobrou sem decisão

As quatro marcações da primeira versão viraram as decisões E1–E4, respondidas
pelo usuário. A quinta (`abandon_task` numa `conversation`) foi decidida nesta
fase sem mudar comportamento (continua no-op), com o motivo escrito na doc.
`Select-String -Pattern 'NEEDS CLARIFICATION'` na doc: 0 ocorrências.

### 4. Nenhum caminho ou identificador citado deixou de existir

Conferidor (`scratchpad/conferir_refs.py`): extrai cada `arquivo:linha`,
`` `:NNN` `` e `` `_funcao:NNN` `` da doc, resolve o arquivo (linha sem arquivo
é de `transactional_state.py`, como a doc declara) e imprime a linha de código.
Última rodada: **90 referências, 0 problemas**, e cada linha impressa lida.

Erro de instrumento achado e corrigido na primeira rodada: 5 referências
`:NNN` herdavam o arquivo errado (`confirm_classification.py:505`,
`:2140`, `:2233`) ou casavam um nome que não era arquivo
(`reclassification_policy.should_promote` lido como `.sh`). As 5 eram
ambíguas para um leitor também; a doc passou a nomear o arquivo em cada uma
(`transactional_state.py:2140, 505, 2233`,
`scripts/reclassification_policy.py:30`) e declara o arquivo padrão da seção
"Leitores".

O código citado é o de `f28d4f0`; `git diff --stat HEAD main -- scripts hooks
contract schemas tests` contra `main` em `5670456` sai vazio, então as linhas
valem para o `main` atual.

## Gaps encontrados

Nenhum na doc depois da correção abaixo. Os achados de passagem (o
`escalation` órfão da L0 que a migração não repara, `state.schema.json` sem
`superseded`/`verified`, `rolled_back` sem escritor, drift entre as quatro
árvores do contrato) estão na seção "Fora do escopo" da doc. Entram na
execução de A (E1) e na sincronia do contrato (E4); o primeiro pede decisão
própria ali, porque A sozinha não o repara.

Correção feita nesta fase: a primeira versão dizia que A resolvia sozinha o
`escalation` órfão. Não resolve: `conversation` não é terminal, e o passo 1 de
`_migrar_status_derivado` só olha terminal. A frase excedia o que A entrega;
foi reescrita (doc, "Fora do escopo" item 1, e o consumidor "migração" em
"Recomendação").

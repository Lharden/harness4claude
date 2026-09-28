# Spec-light: verify pela tabela (`wf-verify-multimodel.js`)

## Objetivo

Fazer o Workflow `wf-verify-multimodel.js` seguir a regra de roteamento de
modelo/esforço do CLAUDE.md global ("Fan-out (Workflow): cada `agent()` com o
tipo da tabela; revisão ampla adjudica só achados críticos e altos"), em vez
de herdar Opus no esforço máximo para todo `agent()` e adjudicar até
finding medium/low.

## Mapeamento dimensão -> agentType

| Dimensão (`DIMENSIONS[].key`) | agentType |
|---|---|
| `spec-coverage` | `analise-complexa` |
| `correctness` | `analise-complexa` |
| `security` | `juiz-alto-risco` |
| `edge-cases` | `analise-complexa` |
| `regressions` | `analise-complexa` |
| Adjudicador (fase Adjudicate) | `juiz-alto-risco` (a descrição do agente diz "adjudica alegações de revisão tentando refutá-las") |

A medida de tokens antes/depois desta mudança fica declarada para a primeira
execução real do workflow (a revisão delta do System One) — não faz parte
desta tarefa mecânica.

## Requisitos

- **REQ-1**: cada chamada `agent()` da fase Review passa `agentType` conforme
  a tabela acima.
- **REQ-2**: a chamada `agent()` da fase Adjudicate passa
  `agentType: 'juiz-alto-risco'`.
- **REQ-3**: só findings com `severity` em `{critical, high}` entram na
  fase de adjudicação (fan-out de `agent()`).
- **REQ-4**: findings com `severity` em `{medium, low}` saem no retorno com
  `adjudicado: false`, sem passar pelo adjudicador, e contam num campo novo
  `nao_adjudicados` (inteiro).
- **REQ-5**: `censoNos` da fase Adjudicate conta só os findings adjudicáveis
  (críticos/altos) — não conta medium/low, que nunca foram enviados.
- **REQ-6**: `pass` e `critical_count` mantêm a semântica de hoje: `pass` é
  `false` se houver crítico/alto confirmado OU nó morto (Review ou
  Adjudicate); `critical_count` é a contagem de crítico/alto confirmados.
- **REQ-7**: o adjudicador continua recebendo só alegação + local + critério
  (severidade alegada), nunca `f.rationale` nem a palavra "justificativa" —
  a aresta descontaminada não pode regredir.

## Acceptance Criteria (Given/When/Then)

- **AC-1** (REQ-1/REQ-2): Given o arquivo `wf-verify-multimodel.js`, When se
  lê cada chamada `agent(...)` das fases Review e Adjudicate, Then cada uma
  declara `agentType` com o valor da tabela.
- **AC-2** (REQ-3/REQ-4): Given uma lista de findings brutos só com
  severidade `medium` ou `low`, When o workflow roda a fase Adjudicate,
  Then nenhum `agent()` de adjudicação é chamado e o retorno traz esses
  findings com `adjudicado: false` e `nao_adjudicados` igual à contagem
  deles.
- **AC-3** (REQ-6): Given um lote só com medium/low (sem crítico/alto, sem
  nó morto), When o workflow calcula `pass`, Then o valor é o mesmo que a
  versão anterior do código produzia para o mesmo lote (`true`).
- **AC-4** (REQ-6): Given um nó morto em Review ou Adjudicate, When o
  workflow calcula `pass`, Then `pass` é `false`, independente da
  severidade dos findings.
- **AC-5** (REQ-7): Given o prompt de refutação do adjudicador, When se
  procura `rationale` ou "justificativa" no texto entre `agent(` e o
  objeto de opções, Then não há ocorrência (teste
  `test_adjudicador_nao_recebe_rationale` permanece verde).

## Boundaries

- **ALWAYS**: manter a aresta descontaminada (adjudicador nunca recebe
  `rationale`); manter `censoNos` cobrindo qualquer `parallel(`.
- **NEVER**: mudar o schema de retorno dos reviewers (`FINDINGS_SCHEMA`) ou
  do adjudicador (`VERDICT_SCHEMA`); mudar a lista de dimensões; tocar em
  `hooks/`, `scripts/state_cli.py` ou estado do harness (outra sessão está
  usando o repo principal).
- **ASK**: se a tabela de roteamento do CLAUDE.md mudar de nomes de agente,
  antes de reescolher o mapeamento aqui.
- Fora de escopo: medir tokens antes/depois (fica para a execução real);
  qualquer mudança de comportamento do harness-workflow fora deste arquivo.

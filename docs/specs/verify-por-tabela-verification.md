# Verification Report — verify-por-tabela

**Date**: 2026-09-28
**Status**: PASS
**Spec**: docs/specs/verify-por-tabela-spec-light.md
**Branch**: feat/verify-por-tabela
**Commit verificado**: d017a6b (feat(workflows): verify-multimodel roteia agent() pela tabela e so adjudica critico/alto)

Não observados: 0. Toda a superfície da spec foi possível conferir por leitura do código (`scripts/workflows/wf-verify-multimodel.js`), execução real do workflow via harness de teste (`tests/wf_run_harness.cjs`) e rodada dos testes-alvo.

## REQs Coverage

| REQ | Descrição | Evidência | Status |
|---|---|---|---|
| REQ-1 | Review usa `agentType` da tabela | `scripts/workflows/wf-verify-multimodel.js:56-88` (array `DIMENSIONS`, cada item com `agentType`); teste `tests/test_verify_por_tabela.py::test_cada_dimensao_de_review_usa_o_agentType_da_tabela` | PASS |
| REQ-2 | Adjudicate usa `agentType: 'juiz-alto-risco'` | `wf-verify-multimodel.js:181` (`agentType: 'juiz-alto-risco'` na chamada `agent(...)` de Adjudicate); teste `test_adjudicador_usa_juiz_alto_risco` | PASS |
| REQ-3 | Só `critical`/`high` entram no fan-out de adjudicação | `wf-verify-multimodel.js:164-165` (`adjudicaveis = findings.filter(...)`); teste `test_medium_low_nunca_geram_agent_de_adjudicacao` | PASS |
| REQ-4 | medium/low saem com `adjudicado:false` e contam em `nao_adjudicados` | `wf-verify-multimodel.js:198-205` (mapeamento de `naoAdjudicaveis`) e `:196` (`nao_adjudicados: naoAdjudicaveis.length`); mesmo teste acima | PASS |
| REQ-5 | `censoNos` do Adjudicate conta só adjudicáveis | `wf-verify-multimodel.js:186` (`censoNos('Adjudicate', adjudicaveis.map(...), adjudicated)`) — a lista de rótulos vem só de `adjudicaveis`, nunca de `naoAdjudicaveis` | PASS |
| REQ-6 | `pass`/`critical_count` mantêm semântica (crítico/alto confirmado OU nó morto) | `wf-verify-multimodel.js:193` (`pass: criticals.length === 0 && nosMortos.length === 0`); testes `test_pass_invariante_lote_so_medium_low` e `test_no_morto_ainda_forca_pass_false` | PASS |
| REQ-7 | Adjudicador nunca recebe `f.rationale`/"justificativa" | `wf-verify-multimodel.js:172-179` (prompt de adjudicação usa só `f.title`, `f.file`, `f.line`, `f.severity`); teste pré-existente `test_adjudicador_nao_recebe_rationale` em `tests/test_workflow_returns.py` (verde na rodada-alvo) | PASS |

## ACs Coverage

| AC | Given/When/Then (resumo) | Teste | Status |
|---|---|---|---|
| AC-1 (REQ-1/2) | Cada `agent()` de Review/Adjudicate declara `agentType` da tabela | `tests/test_verify_por_tabela.py::test_cada_dimensao_de_review_usa_o_agentType_da_tabela`, `::test_adjudicador_usa_juiz_alto_risco` | PASS |
| AC-2 (REQ-3/4) | Lote só medium/low → 0 `agent()` de adjudicação, `adjudicado:false`, `nao_adjudicados` correto | `::test_medium_low_nunca_geram_agent_de_adjudicacao` | PASS |
| AC-3 (REQ-6) | Lote só medium/low sem nó morto → `pass=true` igual à versão anterior | `::test_pass_invariante_lote_so_medium_low` | PASS |
| AC-4 (REQ-6) | Nó morto em Review/Adjudicate → `pass=false` independente de severidade | `::test_no_morto_ainda_forca_pass_false` | PASS |
| AC-5 (REQ-7) | Prompt do adjudicador sem `rationale`/"justificativa" | `tests/test_workflow_returns.py::test_adjudicador_nao_recebe_rationale` (verde na rodada-alvo `test_verify_por_tabela.py tests/test_workflow_returns.py`) | PASS |

## Red-green (Passo 5 da skill)

O próprio arquivo de teste traz a metade de controle: `test_CONTROLE_codigo_antigo_adjudicava_medium_low` roda o mesmo cenário `SOMENTE_MEDIUM_LOW` contra o `wf-verify-multimodel.js` de `main` (via `git show main:...`) e exige que o código antigo adjudique os 2 findings medium/low e não tenha `agentType`/`nao_adjudicados` — provando que os testes novos reprovam por construção contra o comportamento anterior, não são tautológicos. Rodado junto com a suíte-alvo (verde).

## User Stories Coverage

Spec-light não declara user stories com prioridade P1/P2/P3 — é tarefa mecânica de roteamento, não feature com personas. N/A.

## Boundaries Coverage

| Rule | Tipo | Verificação | Status |
|---|---|---|---|
| Manter aresta descontaminada (adjudicador sem `rationale`) | ALWAYS | `wf-verify-multimodel.js:172-179` + `test_adjudicador_nao_recebe_rationale` (verde) | PASS |
| Manter `censoNos` cobrindo qualquer `parallel(` | ALWAYS | Duas chamadas `parallel(...)` no arquivo (Review e Adjudicate), ambas seguidas de `censoNos(...)` — `wf-verify-multimodel.js:99-101` e `:180-186` | PASS |
| NEVER mudar `FINDINGS_SCHEMA`/`VERDICT_SCHEMA` | NEVER | `git diff HEAD~1 -- scripts/workflows/wf-verify-multimodel.js` não toca nenhuma das duas definições de schema (só as chamadas `agent(...)` ganham `agentType`) | PASS |
| NEVER mudar a lista de dimensões | NEVER | `DIMENSIONS` mantém as 5 chaves originais (`spec-coverage`, `correctness`, `security`, `edge-cases`, `regressions`); diff só adiciona o campo `agentType` a cada item | PASS |
| NEVER tocar `hooks/`, `scripts/state_cli.py` ou estado do harness | NEVER | `git show --stat HEAD` mostra só `scripts/workflows/wf-verify-multimodel.js`, `tests/test_verify_por_tabela.py`, `tests/wf_run_harness.cjs` | PASS |
| ASK se a tabela do CLAUDE.md mudar de nomes de agente | ASK | Não aplicável nesta rodada (tabela não mudou) | N/A |

## Success Criteria

Spec-light não declara métricas numéricas de sucesso (latência, cobertura) — declara explicitamente que a medição de tokens fica para a execução real do workflow, fora do escopo desta tarefa mecânica. N/A.

## Evidência de execução

```
$ python -m pytest tests/test_verify_por_tabela.py tests/test_workflow_returns.py -p no:cacheprovider -q
...............                                                          [100%]
15 passed in 0.90s

$ node scripts/workflows/validate_workflows.cjs
wf-context-scan.js -> OK
wf-grill.js -> OK
wf-verify-multimodel.js -> OK
```

Suíte inteira (relatada pelo usuário, não re-executada aqui por já ter rodado): 1606 passaram, 1 pulado, 0 falhas.

## Gaps Encontrados

Nenhum.

## Próximos Passos

Status PASS — pronto para o merge, do ponto de vista desta verificação.

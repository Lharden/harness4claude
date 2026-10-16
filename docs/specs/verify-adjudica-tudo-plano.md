# Plano — o `pass` do verify não sai verde com achado aberto

Task `t-20260930-181933591685` · ramo `claude/verify-adjudica-tudo` ·
spec [v2](verify-adjudica-tudo-spec.md) · [design](verify-adjudica-tudo-design.md).

Regra de execução: teste vermelho antes do código em toda tarefa de código;
testes afetados num processo só; nada de suíte completa (fica para o merge).

## Tarefas

| # | Tarefa | Depende de | Cobre |
|---|---|---|---|
| T1 | `tests/wf_run_harness.cjs` grava `prompt` em `calls.review[]` e `calls.adjudicate[]` | — | REQ-F11 |
| T2 | Fixar o blob de `wf-verify-multimodel.js` em `8cd6884` como `tests/data/wf-verify-multimodel-antes-de-verify-adjudica-tudo.js`, com o hash de blob no teste | — | AC-1.2, AC-3.2 |
| T3 | Testes vermelhos de US-1 em `tests/test_verify_pass_honesto.py` (AC-1.1, 1.3, 1.4, 1.6, 1.7, 1.8, 1.9; REQ-F5 com e sem `repo_root`) e metade-controle AC-1.2; inverter `test_pass_invariante_lote_so_medium_low` | T1, T2 | US-1, REQ-F1–F6 |
| T4 | Implementar US-1 no script: fórmula do `pass`, `n`, `summary`, instrução ao adjudicador, `repo_root` nos prompts de revisor e adjudicador | T3 | US-1, REQ-F1–F6, REQ-NF1 |
| T5 | Testes vermelhos do checador em `tests/test_checar_triagem.py` (AC-2.1 a 2.15; dado real da rodada 1; CLI por subprocess) | — | US-2, REQ-F7, REQ-NF4 |
| T6 | Implementar `scripts/checar_triagem.py` | T5 | US-2, REQ-F7, REQ-NF4 |
| T7 | `SKILL.md`: seção de verify única com o protocolo de REQ-F8, contrato (`:180`), invocação com `repo_root` (`:174`), modelo de `closure-plan.md` com a seção de destino; teste de texto para AC-2.16 (comando literal do checador e destino da cópia) | T6 | REQ-F8, AC-2.16, L-01, L-05 |
| T8 | Pré-registrar `tests/data/verify-rodadas-2026-09-30/medicao-b/conhecidos.csv` (defeitos G/E/F com código presente em `578e0bc`, conferidos por `git show`) e commitar | — | REQ-F12, AC-4.1 |
| T9 | Testes vermelhos de US-3 (AC-3.1, 3.3, 3.4) e metade-controle AC-3.2 | T1, T2 | US-3, REQ-NF2 |
| T10 | Implementar `RUBRICA_SEVERIDADE`; congelar o sha256 do script | T4, T9 | US-3, REQ-F10 |
| T11 | Rodada de medição: worktree destacado em `578e0bc`, script do ramo inline, cópia da saída e do sha256 para `medicao-b/` | T8, T10 | AC-4.2, AC-4.5 |
| T12 | Casamento por `juiz-alto-risco` cego, contagem por AC-4.4, seção 7 da medição, decisão L-06 (ASK na fronteira ou com nó morto); remover o worktree | T11 | AC-4.3, AC-4.4, L-04, L-06 |
| T13 | Só se a rubrica sair: remover constante e testes de AC-3.1/3.2/3.4 no mesmo commit; marcar US-3 "não entrou" | T12 | REQ-F12 |
| T14 | US-5: `spec-template.md:193`, AC-3 de `verify-por-tabela-spec-light.md` marcada superada, `README.md:38-40` | T4, T7 | US-5, REQ-F13 |
| T15 | Testes afetados num processo (`test_verify_pass_honesto`, `test_verify_por_tabela`, `test_workflow_returns`, `test_checar_triagem`, guarda de órfão); `ruff` e `pyright` nos arquivos novos | T4, T6, T7, T10, T13, T14 | REQ-NF3, Success Criteria |

Fase `verify-multimodel` (depois do tdd): o script do ramo inline, com
`repo_root` = este worktree, e a triagem da própria task pelo protocolo novo
(checador sobre `<STATE_DIR>/verify`).

Depois do merge, fora deste plano de código: deploy no cache e a frase no
`CLAUDE.md` global, com OK do usuário (REQ-F9, AC-2.17, L-03).

## Validation Report

Checklist do `validate-plan`:

| Item | Resultado | Evidência |
|---|---|---|
| Cada requisito tem tarefa | PASS | REQ-F1–F6 → T3/T4; F7 → T5/T6; F8 → T7; F9 → pós-merge (declarado, com OK); F10 → T9/T10; F11 → T1; F12 → T8, T11–T13; F13 → T14; NF1 → T4 (AC-1.5 já existente conta adjudicadores); NF2 → T9 (AC-3.3); NF3 → T15 (`test_workflow_pode_ser_invocado_pela_ferramenta`); NF4 → T5/T6 |
| Cada Locked tem tarefa | PASS | L-01 → T3–T7; L-02 → T9/T10; L-03 → AC-1.5 (T15) + pós-merge; L-04 → T8, T11, T12; L-05 → T6/T7; L-06 → T12 |
| Nenhum Deferred no plano | PASS | Opções C/D/E ausentes; dedupe semântico ausente |
| Dependências corretas e sem ciclo | PASS | Grafo acíclico; T10 depois de T4 porque os dois mexem no mesmo arquivo; T8 antes de T11 (pré-registro antes da rodada) |
| Migração de schema | N/A | Nenhum banco nem schema muda (`FINDINGS_SCHEMA`/`VERDICT_SCHEMA` intactos) |
| Interface nova tem teste | PASS | CLI do checador: T5 (inclui subprocess); `repo_root`: T3 |
| API pública tem doc | PASS | Contrato do Workflow no `SKILL.md` (T7); README e template (T14) |
| Ferramentas disponíveis | PASS | `node` v24.16, `python` 3.14; parâmetro `script` do tool Workflow existe; agente `juiz-alto-risco` disponível |
| Nenhuma tarefa "faça tudo" | PASS | Maior tarefa: T7 (texto da skill), um arquivo |

- Status: PASS
- Gaps found: nenhum no plano; um achado de processo — o pipeline não tem fase de plano, então este arquivo nasceu no `validate-plan`
- Revisions applied: none
- Validated at: 2026-09-30T19:05:00-03:00

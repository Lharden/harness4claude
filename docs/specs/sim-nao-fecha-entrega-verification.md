# Verificação — um "sim" não pode fechar a entrega

**Task:** `t-20260928-213959694055` (L2-bug) · **ramo:** `fix/sim-nao-fecha-entrega`
**Commits:** `7abdf79` (testes, vermelho de propósito) · `5095281` (conserto) ·
`201f58a` (premissa do B2b) · este relatório
**Diagnóstico:** `docs/specs/sim-nao-fecha-entrega-diagnostico.md`

## 1. Pedido × evidência

| # | Pedido | Evidência | Resultado |
|---|---|---|---|
| 1 | Reproduzir com resposta curta de aprovação (`yes`, `sim`, `pode`) durante pipeline L1/L2 ativo | `TestRespostaCurtaContinuaAEntrega::test_resposta_curta_continua_a_entrega`, 3 respostas × entrega L1-bug e L2-bug, pelo `harness-classify.sh` de produção | sem o conserto os 6 casos recebem `classification: L0-question ... status: done` no lugar de CONTINUING; com o conserto, CONTINUING e nada gravado |
| 2 | Achar a causa raiz (classificação que supersede e/ou merge da projeção) | diagnóstico §2 (linha do tempo lida do `harness.db` e do `emissions.jsonl` do incidente) e §3 | as duas: F2 fechava a entrega com qualquer prompt, contra a D2; `state_cli._sync` fazia `update` cego sobre a projeção de outra task (e `branch_state._sync_task` também) |
| 3 | Corrigir e provar as duas metades | §2 abaixo | 28 falham / 6 passam sem o conserto; 34 passam com ele |
| 4 | Suíte do repositório continua verde | §3 abaixo | 1638 passed, 1 skipped, 2 deselected — os 2 são os vermelhos pré-existentes de `main`, que continuam falhando iguais quando rodados à parte (§3) |
| 5 | Commit em ramo próprio, mensagens em português, sem merge, sem push | `git log c175fd9..fix/sim-nao-fecha-entrega` | 4 commits, nenhum merge, nenhum push |

## 2. Falha antes, passa depois

Mesmo arquivo de teste (`201f58a`), código trocado por `git checkout`:

```
git checkout 7abdf79 -- scripts/continuation_policy.py hooks/harness-classify.sh hooks/skill_router.py scripts/projecao.py scripts/state_cli.py scripts/branch_state.py
python -m pytest tests/test_sim_nao_fecha_entrega.py -q -p no:cacheprovider   ->  28 failed, 6 passed   (exit 1)
git checkout HEAD -- <os mesmos 6>        # git diff --quiet HEAD confirmou a restauracao
python -m pytest tests/test_sim_nao_fecha_entrega.py -q -p no:cacheprovider   ->  34 passed             (exit 0)
```

Os 6 que passam dos dois lados são exatamente a metade de falsificação — o que o
conserto não podia mudar:

| Teste | Por que tem de passar antes e depois |
|---|---|
| A2 `test_pedido_novo_ainda_fecha_a_entrega` | F2: trabalho novo (L2) não é engolido pela entrega |
| A3 `test_troca_explicita_ainda_fecha_a_entrega` | a porta de saída da D2 (troca explícita, mesmo L0) continua aberta |
| A4 `test_fase_final_sem_evidencia_ja_continuava` | fora da entrega, a resposta curta sempre continuou |
| `test_sem_prompt_a_f2_fica_inteira` | session-start pergunta sem prompt e não muda |
| R2 `test_router_fala_quando_o_pedido_novo_fecha_a_entrega` | o router segue falando quando a entrega fecha |
| B3 `test_propria_task_atualiza_no_lugar` | projeção da própria task é atualizada no lugar, com meta e prompt preservados |

Os 28 vermelhos, por motivo:

| Motivo | Testes |
|---|---|
| comportamento — o defeito do incidente | A1 ×6 (L0-question no lugar de CONTINUING); R1 (router fala sobre a entrega); B1 e B1b (meta `L0-question`/`regex` na projeção da L1); B2 ×2 e B4 (projeção tirada da task viva); B5 (meta de outra task na projeção da dona do ramo) |
| fonte — a regra não está nos escritores | G ×2 (`projection.update(` em `state_cli.py`; nenhum `projetar(` em `branch_state.py`) |
| API nova ausente | política ×12 (`nivel_do_prompt`, `entregue`), B6 (`projecao.projetar`) |

O vermelho por API não prova o defeito, só a assinatura nova; a prova
comportamental da metade 1 é o A1 (pelo hook), e a da metade 2 são B1, B1b, B2,
B4 e B5 (pelo `state_cli` e pelo `branch_state.attach_files` de produção).

## 3. Suíte

| Rodada | Código | Resultado |
|---|---|---|
| base | `c175fd9`, antes de qualquer edição deste ramo | 1604 passed, 2 failed, 1 skipped (17 min 42 s) |
| final | `201f58a` | 1638 passed, 1 skipped, 2 deselected, 6 subtests passed (37 min 51 s, com outra sessão rodando pytest na máquina). 1638 = 1604 da base + 34 novos: nenhum teste perdido, nenhuma falha nova |
| os 2 deselecionados, à parte | `201f58a` | 2 failed — os mesmos da base, pelos mesmos motivos (`assert 0 == 2` no controle; drift de 5 arquivos do verify-por-tabela no cache) |

Os dois vermelhos são de `main` e anteriores a este ramo (diagnóstico §7):
`test_verify_por_tabela.py::test_CONTROLE_codigo_antigo_adjudicava_medium_low`
(controle aponta para `main`, que desde o merge `c175fd9` já é o código novo) e
`test_deploy_drift.py::test_o_cache_reflete_o_publicado` (o cache instalado não
recebeu o deploy desse merge). A rodada final os deseleciona pelo nome — está no
comando gravado como evidência — e roda os dois à parte para mostrar que
continuam os mesmos.

Testes vizinhos, com o conserto: `test_sim_nao_fecha_entrega`,
`test_ciclo_de_vida_da_task`, `test_continuation_policy`, `test_branch_state`,
`test_transactional_branches`, `test_skill_router`, `test_router_golden`,
`test_router_reachability`, `test_record_signal`, `test_classify_prompt`,
`test_classify_anuncio_fantasma`, `test_transactional_state`,
`test_transactional_hook`, `test_receita_do_manual` — 361 passed.

## 4. Lint

`ruff check` nos arquivos Python alterados (`continuation_policy.py`, `projecao.py`,
`state_cli.py`, `branch_state.py`, `skill_router.py`, `tests/conftest.py`): mesma
contagem de achados do HEAD anterior (todos pré-existentes, `RUF100`); `tests/test_sim_nao_fecha_entrega.py`:
0. O repositório inteiro tem 55 achados pré-existentes.

## 5. Harness

Task `t-20260928-213959694055` (L2-bug): as cinco fases passaram por `state_cli transition`; artefato `graph-context` = o diagnóstico. A evidência final foi gravada pelo mesmo processo da suíte (`scratchpad/fechar.py`: suíte, `evidence` e `complete` sem tool call no meio): `tests_collected` 1639, `tests_passed` 1638, `tests_skipped` 1, e o `--command-text` nomeia os 2 deselecionados. `verified=True` em `code_revision` 95; `complete` exit 0, task `done`. Sinal: `record_signal --completed --expect-task` registrou `level=L2, files=10`.

## 6. O que ainda não vale em produção

O plugin instalado (`~/.claude/plugins/cache/harness4claude/harness4claude/4.0.0`)
roda o código antigo: `continuation_policy.py` e `harness-classify.sh` de
2026-09-23, `state_cli.py` de 2026-09-25. Até o merge deste ramo e o
`python scripts/deploy_to_cache.py --apply` de um checkout de `main`, um "sim"
depois de uma entrega verificada continua fechando a task nas sessões vivas.

## 7. Resíduos e tarefas separadas

Declarados no diagnóstico §7 e §8.3. Viraram tarefa separada:

1. `complete`, `transition` e `resolve_gate` reescrevem desfecho terminal.
2. Controle do `test_verify_por_tabela` fixado antes do merge.
3. `record_signal` registrar a task pelo id no `harness.db` (a substituída por
   trabalho vivo hoje fica sem sinal), e o exemplo `--abandoned` do `SKILL.md`
   sem `--expect-task`.
4. SubagentStart injetando `harness-workflow` nas lentes do `wf-grill`.

Mais um, sem tarefa: projeção já parada numa task não-viva que é a própria task
operada é atualizada no lugar pelo `projetar` (não piora nada; o reparo para a
viva é do classify no prompt seguinte).

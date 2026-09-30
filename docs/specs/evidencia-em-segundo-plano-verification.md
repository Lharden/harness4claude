# Verificação — evidência automática de suíte em segundo plano

Task `t-20260930-135548871810` (L2-feature) · ramo `claude/zen-antonelli-368403` (worktree `vibrant-montalcini-9868a6`)
· spec [evidencia-em-segundo-plano-spec.md](evidencia-em-segundo-plano-spec.md)
· design [evidencia-em-segundo-plano-design.md](evidencia-em-segundo-plano-design.md)
· fechamento [../closure-plan.md](../closure-plan.md)

## 1. Resultado

| Frente | Resultado |
|---|---|
| Testes do arquivo novo (`tests/test_evidencia_em_segundo_plano.py`) | 83 passed (74 até a iteração 2; +9 da rodada 3) |
| Testes afetados (12 arquivos, um processo, depois do merge de `main` em `dc7e63e`) | 516 passed (antes da iteração 2); rodada final na seção 6 |
| Controle em `d3fc851` (worktree fixado por SHA) | AC-1.2, AC-1.3, AC-2.1 e insumo real reprovam por comportamento; AC-4.1 reprova só na leitura da tabela nova (o código antigo não captura nada) — quem prova AC-4.1 é M4 |
| Mutação | **29 de 29** mortos (21 até a iteração 2; +8 da rodada 3) |
| `wf-verify-multimodel` rodada 3 (`wf_2c3bbdfb-161`, pedida pelo usuário) | `pass: true`, 0 críticos, 15 médio/baixo → 7 defeitos reais e 2 guardas fechados (seção 8) |
| `wf-verify-multimodel` rodada 1 (`wf_6bd9e71a-c4f`) | `pass: true`, 0 críticos, 23 médio/baixo não adjudicados → 3 defeitos reais de segurança + 1 de cobertura real (10% dos términos) — fechados na iteração 1 |
| `wf-verify-multimodel` rodada 2 (`wf_47eeb4d4-140`) | `pass: true`, 0 críticos, 11 médio/baixo → 5 fechados na iteração 2 (não re-revisados pelo Workflow; cada um com mutante) |
| Performance (maior transcript real, 122 MB, 1 pendente) | captura 0,33 s + `jobs_em_voo` 0,66 s ≈ 1,1 s (pior de 3); sem pendente 1,2 ms; timeout do Stop 15 s |

## 2. Falsificação nas duas metades (L-07)

- **Metade 1** — suíte verde em segundo plano, revisão inalterada, verifica:
  `test_suite_verde_em_segundo_plano_verifica_no_stop` (verde com a feature;
  reprova em `d3fc851`: o Stop bloqueia e não há evidência).
- **Metade 2** — revisão mudou, não verifica:
  `test_revisao_mudou_desde_o_lancamento_vira_historico` (evidência em N',
  `verified=0`, `historico`; reprova em `d3fc851` por falta de evidência, e
  reprova sob **M1** — captura gravando na revisão corrente — e **M5** —
  histórico mexendo em `verified`).
- **Insumo real** `b8jmxvjka` pela função de produção:
  `exit_code=1, tests_collected=1613, tests_passed=1610, tests_skipped=1`.

Metade-controle por SHA fixo (`d3fc851`), não por `main:` (memória
"controle de código antigo por SHA"): `git worktree add --detach <scratch>
d3fc851`, testes deste ramo com `HARNESS_PLUGIN_ROOT` apontando para lá,
worktree removido depois.

## 3. Mutantes (script `scratchpad/mutantes.py` da sessão; cada um uma troca de texto)

| Mutante | Regra removida | Morto por |
|---|---|---|
| M1 | grava na revisão corrente | AC-2.1, AC-2.2 |
| M2 | ignora `tool-use-id` | AC-3.6 |
| M3 | aceita qualquer entrada | AC-3.7 (7 formas) |
| M4 | sem `superado` | AC-4.1 |
| M5 | histórico mexe em `verified` | 4 testes de US-2 |
| M6 | captura depois do teste de `verified` no Stop | 15 testes |
| M7 | arquivo em qualquer lugar | AC-3.8 (nome e `tasks`) |
| M8 | ignora trailer divergente | AC-3.5 |
| M9 | erro de leitura rejeita | AC-3.10 |
| M10 | status sem término normal aceito | AC-3.2 (`stopped`/`killed` com código) |
| M11 | `superado` por `created_at` | AC-4.3 |
| M12 | bloco até o primeiro `</task-notification>` (era: aceita vários blocos) | AC-3.20 |
| M13 | código pela primeira ocorrência | AC-3.14 |
| M14 | trailer opcional | AC-3.13 (2) |
| M15 | cópias sem comparar arquivo | AC-3.11 |
| M16 | sem id / sem transcript fica pendente | AC-3.6 (2) |
| M17 | sem diretório `tasks` | AC-3.8 |
| M18 | campos lidos do bloco inteiro | AC-3.16 |
| M19 | subagente fica pendente | AC-3.17 |
| M20 | sem contagem grava | AC-3.18 |
| M21 | `complete` captura antes da fase | AC-3.19, AC-3.23 |
| M22 | lê todas as strings da entrada | AC-3.21 |
| M23 | resumo até o primeiro `</summary>` | AC-3.20 |
| M24 | `complete` confere fase pelo nome | AC-3.23 |
| M25 | recusa sem a revisão nova | AC-3.22 |
| M26 | falha no registro derruba o hook | AC-3.24 |
| M27 | aviso sempre promete captura | AC-3.24, AC-3.25 |
| M28 | import antecipado de nomes privados | AC-3.26 |
| M29 | captura de qualquer task (os dois filtros) | REQ-F3 (isolamento) |

M10 sobreviveu na primeira rodada: nenhum teste de `stopped`/`killed` trazia
código no resumo. O teste foi escrito e o mutante morreu.

## 4. Cobertura da spec

| Item | Evidência |
|---|---|
| REQ-F1 tabela + migração | `test_lancamento_em_segundo_plano_fica_registrado_sem_evidencia`, `test_banco_antigo_ganha_a_tabela_sem_mexer_no_resto` |
| REQ-F2 registro no PostToolUse(+Failure) | AC-1.1, AC-1.6, `test_post_tool_use_failure_tambem_registra`, `test_comando_composto_em_segundo_plano_nao_e_lancamento` |
| REQ-F3 captura | AC-1.2, AC-1.4, US-3 inteira |
| REQ-F4 código (fim do resumo, trailer obrigatório) | AC-3.2, 3.5, 3.12, 3.13, 3.14; M8, M10, M13, M14 |
| REQ-F5 contagem única | `test_test_counts_do_hook_delega_para_contar_testes`, `test_contar_testes_no_insumo_real` |
| REQ-F6 revisão do lançamento | 4 testes de `record_evidence`; M1, M5 |
| REQ-F7 Stop e `complete` | AC-1.2, AC-1.3, AC-3.19, exceção na captura; M6, M21 |
| REQ-F8 mensagens e evento | AC-5.1, 5.2, 5.3 |
| REQ-F9 lugar e cauda | AC-3.8, sessão retomada, teto de cauda; M7, M17 |
| REQ-F10 ordem de término | AC-4.1, 4.2, 4.3; M4, M11 |
| REQ-F11 um bloco por texto | AC-3.15; M12 |
| REQ-NF1 performance | medição da seção 1 (fora da suíte: arquivo de 122 MB) |
| REQ-NF2 direção do erro | AC-3.10, exceção na captura; M9 |
| REQ-NF3 observabilidade | AC-5.1, 5.2; motivos em toda rejeição |

## 5. Achados de verificação que ficam como limite (com motivo)

Listados em [../closure-plan.md](../closure-plan.md), "Declarados como limite":
janela de CAS do `complete` com Stop concorrente (erra recusando), término sem
carimbo vira `superado` (erra não verificando), `stop_hook_active` antes da
captura (o `complete` e o Stop seguinte capturam), import de símbolos privados
de `trabalho_em_voo` (quebra na coleta, não em silêncio), releitura com pendente
(0,33 s medido), job sem notificação (9%, receita manual), sessão retomada em
balde novo, receita manual inalterada (decisão do usuário, grill #2), escrita
concorrente na janela do lançamento (decisão do usuário, grill #3).

## 6. Rodada final

Registrada depois deste relatório, para nenhuma escrita posterior invalidar a
evidência: testes afetados, um processo (regra de coordenação), com o comando
atômico que o hook grava sozinho.

## 8. Rodada 3 do verify (pedida pelo usuário em 2026-09-30)

`wf_2c3bbdfb-161`: `pass: true`, 0 críticos, 15 médio/baixo não adjudicados.
Pela terceira vez o `pass` escondia defeito real entre os não adjudicados.

| Achado | Conserto | Teste | Mutante |
|---|---|---|---|
| #7 bloco forjado no `cwd`/`gitBranch` da entrada vira evidência verde (POSIX) — reproduzido | lê só `attachment.prompt` / `message.content` | `test_bloco_forjado_em_metadado_da_entrada_nao_vale` | M22 |
| #9 tag `<task-notification>` literal na descrição descarta a notificação legítima | bloco do primeiro `<task-notification>` ao último `</task-notification>`; campos antes do primeiro `<summary>`; resumo até o último `</summary>` | `test_tag_de_bloco_na_descricao_nao_esconde_a_notificacao` | M12, M23 |
| #8 `complete` captura vermelho e recusa; repetir dá `revision mismatch` | a recusa diz `revision=` nova | `test_complete_que_captura_e_recusa_diz_a_revisao_nova` | M25 |
| #12 falha no registro derruba o hook e perde o aviso | `try` + aviso sem promessa | `test_falha_ao_registrar_...` | M26 |
| #5 task terminal: aviso promete captura | registro devolve o estado; aviso escolhido por ele | `test_task_terminal_nao_promete_captura` | M27 |
| #3 fase conferida pelo nome antes da captura | pelo índice | `test_complete_com_fase_repetida_confere_pelo_indice` | M24 |
| #15 import de nomes privados derruba o hook inteiro | referência tardia `_voo.X` | `test_modulo_carrega_mesmo_se_...` | M28 |
| #1 isolamento entre tasks sem guarda | — (código certo) | `test_captura_so_olha_lancamentos_da_task_do_chamador` | M29 |
| #2 dois lançamentos em revisões diferentes sem teste | — | `test_dois_lancamentos_em_revisoes_diferentes_cada_um_na_sua` | M1 |

Ficam como limite (já declarados na seção 5): pendente sem TTL (0,33 s/Stop
medido), término sem carimbo, ordem das recusas do `complete`.

Não houve rodada 4 do Workflow sobre estes consertos; cada um tem mutante que o
derruba.

## 9. Rebase sobre `db42807` (pedido da coordenação, 2026-09-30)

`main` candidato com o lote transacional (`d44fb62`), desfecho (`dab7cb3`) e
xdist (`db42807`). O merge de `dc7e63e` que estava no meio saiu (rebase
lineariza). Um conflito, em `scripts/transactional_state.py`:

- `record_evidence` → `_gravar_evidencia`: a regra de status nova de `main`
  (`_status_derivado`) foi levada para dentro do helper.
- `complete`: as travas novas de `main` (desfecho registrado via
  `_exige_task_viva`, portão pendente) entraram. **O encontro das duas mudanças
  criou um defeito**: sob o lock, as travas vinham DEPOIS da captura, e um
  `complete` recusado por elas já tinha gravado (a mesma classe do re-verify
  #9). Dois testes vermelhos provaram (`revision` 3 → 4 numa recusa); as
  travas agora também rodam na pré-checagem sem escrita, na ordem do
  contrato de `main` — desfecho antes de revisão
  (`test_desfecho_terminal::test_recusa_por_desfecho_vem_antes_da_revisao`
  pegou a primeira ordem, que conferia revisão antes).
- Mutantes M30 (portão só depois da captura) e M31 (desfecho só depois da
  captura) mortos. **Mutação: 31 de 31.** Testes do arquivo novo: **85**.

## 7. Implantação

Nenhum deploy. Os hooks rodam do plugin instalado; o merge em `main` e o deploy
dependem do OK do usuário e da fila de merge (este ramo é o 7º; rebase no
`main` do momento, com o Stop por fase já dentro, e suíte inteira uma vez, na
vez).

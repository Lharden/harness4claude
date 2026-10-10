# Verification Report — branch-camada-j

**Date**: 2026-10-10
**Status**: PASS
**Spec**: `docs/specs/branch-camada-j-spec-light.md` (aprovada, `e06d704`)
**Plano**: `docs/specs/branch-camada-j-avaliacao-plano.md`
**Implementação**: master-harness, ramo `feat/avaliacao-camada-j` (`8ffcb46`, `57c656f`, `5bac18e`, `3ef033c`), base
`9c737da`. Só acréscimos: `git diff --stat main...HEAD` lista 10 arquivos novos ou acrescidos, e `mh/s1` e `mh/replay`
ficam sem diff.

Não observados: 0. Clarifications pendentes: 0 (a CLARIF-1 foi resolvida pela ASSUMPTION-004).

## REQs

| REQ | Descrição | Evidência | Status |
|---|---|---|---|
| REQ-001 | consumidor nomeado; avaliador fora do kit | `mh/avaliacao/camada_j.py` e `mh/avaliacao/cli_avaliacao.py`, ligados a `mh avaliar camada-j` (`mh/cli.py`, `_cli_avaliacao`/`_cmd_avaliar`). O veredito vai ao gate do usuário. A ligação no `branch_sensor` ficou fora, como a spec manda | COBERTO |
| REQ-002 | `noul` com o texto fixado; estado `{ancora, turno}` | `mh/avaliacao/moldes/camada_j_par.json`, com o texto da spec byte a byte; `camada_j_turno.json` é a ablação | COBERTO |
| REQ-003 | redação antes do envio, `PonteJev` do kit, seco revisado, sha conferido | `camada_j.py:125` (`seco`, dentro de `captura()`, `auditada=False`); `:273` (`auditada=True`, log `enviados-C4`); `:283` (`envio_divergente`). A amostra `e024138bb383` foi revisada e liberada pelo usuário nesta janela em 2026-10-10 | COBERTO |
| REQ-004 | quarentena: porta `C4`, nada de P1, sem `causas_da_fase` | `camada_j.py:33` (`PORTA = "C4"`) e `:209-234` (sonda, vazamento e revisão de `C4` chamados um a um). `grep causas_da_fase mh/avaliacao` sai vazio | COBERTO |
| REQ-005 | teto de US$ 0,15 e envelope de US$ 5 | `camada_j.py:39`, `:224` (`teto_excedido`) e `:230` (`envelope_excedido`), antes da sonda | COBERTO |

## Suposições

| ASSUMPTION | Condição | Justifica | Evidência | Estado |
|---|---|---|---|---|
| 001 | instrução em inglês, um candidato só | REQ-002, S1–S3 | só um molde decide (`par`); o molde `turno` é ablação e não decide | VALE |
| 002 | o avaliador mora em `mh/avaliacao/` e importa o kit sem editá-lo | REQ-001, REQ-004 | `git diff --stat main...HEAD -- mh/s1 mh/replay` sai vazio | VALE |
| 003 | k = volume de disparos da A, sem limiar ajustado | S1, S2 | `camada_j.py:309` (`_precisao_em_k`, k = Σ `a`); medido k = 25 | VALE |
| 004 | com `avança`, a ligação começa em modo sombra | REQ-001, ENTREGA | decisão do usuário; a ligação ainda não existe (é a próxima spec) | VALE |
| 005 | rodada real liberada, ~US$ 0,08 com teto de US$ 0,15 | REQ-003, REQ-005 | gasto real de US$ 0,076841 (livro, lote `camada-j-2026-10-10T…`) | VALE |

## ACs

| AC | Resumo | Testes (`tests/avaliacao/test_camada_j.py`) | Red-green |
|---|---|---|---|
| AC-001 | o seco redige e não abre conexão | `test_seco_monta_os_corpos_sem_abrir_conexao`, `test_seco_redige_o_segredo_plantado_e_deixa_o_resto_igual` | a barreira do conftest levanta em qualquer conexão real |
| AC-002 | código errado ou sha divergente | `test_rodar_recusa_codigo_errado_sem_chamar`, `test_rodar_recusa_seco_alterado_depois_da_amostra`, `test_corpo_diferente_do_seco_vira_envio_divergente` | sabotagem medida: código, seco_alterado e envio_divergente, cada um REPROVA |
| AC-003 | `mh/s1` intocado; só `enviados-C4` nasce na casa do S1 | `test_so_o_log_c4_nasce_na_casa_do_s1`, mais o `vigia` do conftest sobre `mh/s1` e a casa real | sabotagem medida: `auditada` REPROVA |
| AC-004 | teto e envelope recusam antes da sonda | `test_teto_da_rodada_recusa_antes_da_sonda`, `test_envelope_do_mes_recusa_antes_da_sonda` | sabotagem medida: teto e envelope REPROVAM |
| AC-005 | três vereditos, reprodutíveis, sem texto | `test_avaliar_avanca_quando_j_separa`, `test_avaliar_nao_avanca_quando_j_e_ruido`, `test_s1_sozinho_reprova…`, `test_s2_sozinho_reprova…`, `test_avaliar_inconclusivo_sem_cobertura`, `test_avaliar_e_reprodutivel_pela_semente`, `test_medir_escreve_relatorio_sem_texto_de_par` | sabotagem medida: cobertura, S1, S2 e S3 REPROVAM. O S1 escapava na primeira rodada e ganhou um teste em que decide sozinho (`57c656f`) |

Placar da sabotagem: 11 de 11 guardas pegas, com o diff restaurado vazio. Um defeito real apareceu no uso: `ver-amostra`
com stdout em cp1252. Ganhou teste RED e depois a correção (`5bac18e`).

## Boundaries

| Regra | Verificação | Status |
|---|---|---|
| fora: mudar o `branch_sensor`, o hook ou `mh/s1` | o diff do harness4claude no ramo `spec/branch-camada-j` só tem docs; no master-harness, `mh/s1` e `mh/replay` ficam sem diff | PASS |
| fora: escolher o limiar de produção | nenhum limiar gravado; k vem da camada A | PASS |
| a rodada real espera OK separado | o "confirmo" e o "Envia tudo" do usuário foram dados nesta janela, depois da revisão da amostra | PASS |

## Critério de sucesso (resultado da rodada real)

Relatório completo: `master-harness/docs/avaliacao/camada-j-resultado.md` (`3ef033c`).

| # | Corte | Medido | Status |
|---|---|---|---|
| S0 | cobertura ≥ 0,95 | 1,000 (1 406 de 1 406 `ok`) | passa |
| S1 | precisão@25 ≥ 0,35 | 0,36 (9 de 25) | passa, **na fronteira**: 8 de 25 reprovaria |
| S2 | IC inferior do Δ precisão@k > 0 | Δ 0,28, IC [0,118, 0,556]; A = 2 de 25 | passa |
| S3 | IC inferior do Δ AUC-PR contra o comprimento > 0 | 0,344 contra 0,073; IC [0,139, 0,422] | passa |
| Ablação | relatada | Δ AUC-PR (par − turno) 0,240, IC [0,114, 0,370]: a âncora conta | — |

**Veredito: avança.**

## Nível de garantia

| Afirmação | O que sustenta | Status |
|---|---|---|
| o J separa *abertura de sessão nova* de *continuação* melhor que a camada A | S0–S3 com IC por sessão | SUSTENTA |
| o J acha ramos no meio de uma sessão | nada ainda: é o modo sombra (ASSUMPTION-004) | não afirmado |

## Gaps e ressalvas para a spec de ligação

- **S1 na fronteira.** 9 de 25 contra um corte de 9. O veredito é `avança` pelo critério fixado, mas a margem é de
  um acerto. O modo sombra deve fixar o próprio critério antes de olhar, sem herdar este.
- **Negativos não humanos.** Na amostra revisada aparecem negativos que não são prompt do usuário: notificação de
  tarefa, corpo de skill, resumo de compactação, "Continue from where you left off.", "[Request interrupted…]". Eles
  facilitam os negativos para o J. É defeito do `harvest_branch_labels.py`, que filtra o rótulo positivo e não o
  negativo, e o fluxo de produção em `UserPromptSubmit` também contém esse tipo de texto. A spec de ligação deve
  decidir se o J pontua essas entradas ou se elas são filtradas antes.
- **Dado da empresa.** 409 dos 703 pares vêm do `slb-mestrado-projeto`, com material interno da SLB. O envio foi decisão
  explícita do usuário (2026-10-10, L-43). A spec de ligação herda essa decisão, ou a revê, para o modo sombra.
- **Integração.** O `main` do master-harness andou para `814ee5a`, que mexe em `tools/orfaos.json`. O ramo toca
  `mh/cli.py`. A integração é do orquestrador.
- **Ambiente medido.** Os testes rodaram com `--capture=sys -n 2`: a captura fd do pytest invalida o stdin dos
  subprocessos nesta máquina (`WinError 6`), e o conserto vai como tarefa separada.

VERIFY_STATUS=PASS

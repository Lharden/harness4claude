# Verification Report: branch-camada-j-ligacao (modo sombra da camada J)

- **Date**: 2026-10-10
- **Status**: PARTIAL. Todo P1 tem implementação e teste verde. O que falta observar depende do deploy D2 e da janela
  real, e os dois esperam o OK do usuário.
- **Spec**: `docs/specs/branch-camada-j-ligacao-spec.md` (v3 aprovada, emenda v4 em `4e13271`)
- **Design**: `docs/specs/branch-camada-j-ligacao-design.md`

Commits nos ramos `feat/camada-j-sombra`:
- harness4claude: `da5e710`, `cb6b8cd`, `4e13271`, `6489edf`, `a34743a`, `e7f5ed4`, mais o commit deste relatório;
- master-harness: `77cc8de`, `3b4f50d`, `e59e913`, `32a3bf5`, `17f9768`, `2ca6bc0`, `9aaa41b`.

## Não observados (5)

| Item | Por quê |
|---|---|
| AC-0.1 (teste de vida do dia 0, na instalação real) | depende do deploy D2 do hook no cache do plugin, que espera o OK do usuário |
| AC-1.8 (p95 no hook real) | medido em teste com o spool real (1 000 chamadas; 300 sessões), não no hook instalado. Sem D2 não há hook instalado |
| US-2 a US-4 com o Jev real | a janela ainda não abriu. Todo o caminho foi exercido com servidor falso e com a barreira de rede do conftest |
| 7 testes de `test_hook_liveness.py` e 11 de `test_interpretador_nos_hooks_json.py` | pulados: "Bash integration runtime is not installed on this host". O motivo é de ambiente e anterior a este ramo |
| ASSUMPTION-009, 010 e 011 | inferências que só a janela real confirma (prefixos, volume, retenção) |

## Rodadas da verify-multimodel

| Rodada | Resultado | Desfecho |
|---|---|---|
| 1 | 1 alto, 25 médios ou baixos | corrigidos em `17f9768` e `6489edf` |
| 2 | 1 alto (regressão do `_sessao_nova`) | escalado ao usuário; redesenho v4 pelo `SessionStart` (`4e13271`, `a34743a`, `2ca6bc0`) |
| 3 | PASS (0 crítico, 0 alto); 22 médios ou baixos | os de privacidade e os baratos corrigidos em `e7f5ed4` e `9aaa41b`; os demais estão listados abaixo |

Achados da rodada 3 tratados:

| Achado | Conserto | Teste | Sabotagem |
|---|---|---|---|
| a varredura rodava sobre o JSON escapado | `varredura.residuos_do_estado` percorre os valores | `test_varredura_do_seco_ve_o_texto_sem_escape_de_json` | pegou |
| cartão escondido por grupo vizinho ou tab | fatias contíguas de grupos (13 a 19 dígitos) e `\s` | `test_varredura_pega_formas_que_escapavam` | pegou (2 mutantes) |
| CPF com palavras entre o rótulo e o número, ou traço Unicode | `cpf\b[^\d\n]{0,40}` e U+2010 a U+2015, U+2212 | idem | pegou (2) |
| telefone sem DDD, ou só dígitos | `9xxxx-xxxx`; `[55]DD9xxxxxxxx` | idem | pegou (2) |
| (contraprova) Luhn | inalterado | `test_varredura_continua_sem_acusar_numero_comum` | pegou |
| o D2 não exigia o hook de início | `abrir` exige `hooks/branch_j_inicio.py` e o registro no `hooks.json` | `test_abrir_recusa_plugin_sem_o_hook_de_inicio` | pegou |
| o `abrir` executava o código do plugin | lê `PREFIXOS` e `MIN_LEN` por `ast.literal_eval` | `test_abrir_le_o_filtro_sem_executar_o_plugin`, `test_abrir_recusa_filtro_que_nao_e_literal`; no h4, `test_filtro_e_literal_e_bate_com_o_sha_para_o_abrir_do_mh` | pegou (2) |
| a saúde não via exclusão incompleta | `*.excluindo` dá DOENTE em qualquer estado | `test_saude_acusa_exclusao_incompleta_com_a_janela_aberta` | pegou |
| saúde: pasta que não lista derrubava | NAO_VERIFICADO | `test_saude_nao_verificado_quando_a_pasta_nao_lista` | pegou |
| exceção na peça da sombra derrubava o `checar` | `_peca_sombra` dá NAO_VERIFICADO | `test_checar_nao_cai_se_a_sombra_levanta` | pegou |
| `excluir-sessao` levantava se o apagar falhasse | marcador antes; aviso e código 3 | `test_excluir_sessao_que_nao_apaga_marca_excluida_e_avisa` | pegou |
| `status`, `seco` e `rodar` caíam com sessão apagada no meio | `FileNotFoundError` tolerado; no `rodar`, o arquivo sumido conta como mudado | `test_status_tolera_sessao_apagada_no_meio`, `test_seco_tolera_sessao_apagada_no_meio` | pegou (2) |
| `rotular` sem `--corrigir` depois do veredito não tinha teste | o código já recusava | `test_rotular_recusa_depois_do_veredito` | pegou |
| o `purgar` não dizia que o log da porta C4 fica | mensagem com `enviados-C4.jsonl` | `test_purgar_avisa_que_o_log_da_porta_fica` | pegou |
| h4: sessão excluída voltava por um `.nova` que sobrou | `.excluida` respeitado no `iniciar_sessao` e no `_registrar` | `test_branch_j_spool_v4b.py` | pegou (2) |
| h4: o hook de início rodava em `resume` e `compact` | `matcher: startup\|clear` | `test_hook_de_inicio_so_dispara_em_startup_e_clear` | pegou |
| texto antigo da spec (Files, D2, saúde, NEVER) | atualizado para a v4 | — | — |

Achados que não exigiram mudança, cada um com o motivo:
- **Apagar o `.nova` antes, no `purgar`.** A janela já passa a `encerrada` antes de qualquer apagar, e o gate do hook
  fecha com ela. Isso está coberto por `test_purgar_encerra_antes_de_apagar_e_segue_quando_um_arquivo_trava`.
- **`comum.jsonl` tolerar linha inválida.** `escores.jsonl` e `rotulos.jsonl` corrompidos devem parar o verbo, não
  ser pulados em silêncio: pular repontuaria ou perderia rótulo sem ninguém ver.
- A sabotagem `sys_path` da emenda v4 perdeu o objeto: o `abrir` não executa mais o plugin, então não há `sys.path`
  para restaurar. O teste `test_abrir_nao_deixa_o_plugin_no_sys_path` fica como regressão barata.

## REQs

| REQ | Evidência | Status |
|---|---|---|
| REQ-001 | `scripts/branch_j_spool.py::registrar`, `iniciar_sessao`; `hooks/branch_j_inicio.py`; `branch_sensor.py::_spool_j` | COBERTO |
| REQ-002 | `branch_j_spool.py::_gate` | COBERTO |
| REQ-003 | `branch_j_spool.py::PREFIXOS`, `motivo_de_filtro` | COBERTO |
| REQ-004 | `mh/avaliacao/camada_j_sombra.py::executar`, `cli_avaliacao.py` | COBERTO |
| REQ-005 | `camada_j_sombra.py::TAU` (0.77), congelado no `janela.json` | COBERTO |
| REQ-006 | `camada_j_sombra.py::TETO_USD`, `_gastos`, `_estimativa` | COBERTO |
| REQ-007 | `mh/avaliacao/varredura.py` (credenciais e dados pessoais, rodando sobre os valores do estado) | COBERTO |
| REQ-008 | `camada_j_sombra.py::abrir` (`janela.json`: τ, semente, fila, critério, `sha_filtro`, `sha_molde`, `spec_commit`) | COBERTO |
| REQ-009 | `camada_j_sombra.py::medir` imprime os passos e não muda settings nem plugin | COBERTO |
| REQ-010 | `mh/avaliacao/comum.py::terminal_humano` (stdin e stdout tty, sem `CLAUDECODE`) | COBERTO |

## ACs

Testes do h4 em `tests/test_branch_j_spool.py` (J) e `tests/test_branch_j_spool_v4b.py` (Jb). Testes do mh em
`tests/avaliacao/test_camada_j_sombra_{abrir,lote,veredito,limpeza,v4,v4b}.py`.

| AC | Teste(s) | Status |
|---|---|---|
| AC-0.1 | `abrir::test_abrir_cria_pasta_com_acl_e_janela` (vida real: não observado) | PASS em teste |
| AC-0.2 | `abrir::test_abrir_recusa_casa_nao_padrao`, `v4::test_abrir_recusa_casa_divergente_no_env_do_hook` | PASS |
| AC-0.3 | `abrir::test_abrir_recusa_segunda_janela`, `test_abrir_recusa_qualquer_janela_previa` | PASS |
| AC-0.4 | `abrir::test_abrir_recusa_plugin_sem_spool`, `_sem_plugin_root`, `_flag_ou_sensor`, `_sem_settings`, `_sem_chave`; `v4b::test_abrir_recusa_plugin_sem_o_hook_de_inicio` | PASS |
| AC-0.5 | `abrir::test_status_conta_sem_mostrar_texto`, `test_status_mostra_linhas_por_sessao`, `v4::test_status_mostra_o_tamanho` | PASS |
| AC-1.0 | J `test_sessionstart_novo_cria_o_marcador`, `_de_retomada_nao_cria_marcador`, `test_hook_de_inicio_*`; Jb matcher | PASS |
| AC-1.1 | J `test_flag_desligada_nao_escreve_nada`, `test_sem_pasta_nem_janela_nao_cria_nada`, `test_janela_que_nao_esta_aberta_nao_escreve`, `test_janela_ilegivel_vale_como_ausente` | PASS |
| AC-1.2 | J `test_primeiro_prompt_humano_depois_do_sessionstart_e_a_ancora`, `test_slash_command_antes_do_primeiro_prompt_nao_consome_o_marcador`, `test_sessao_sem_marcador_nao_entra_nem_conta` | PASS |
| AC-1.3 | J `test_turnos_seguintes_acrescentam_com_indice`, `test_turno_nao_recria_spool_apagado`, `test_turno_em_corrida_com_a_exclusao_nao_recria_o_arquivo` | PASS |
| AC-1.4 | J `test_lista_de_prefixos_e_a_da_spec`, `test_prefixo_nao_humano_nao_grava_e_conta`, `test_prefixo_em_outra_caixa_e_humano`, `test_curto_conta_e_doze_code_points_grava` | PASS |
| AC-1.5 | J `test_entrada_invalida_nao_grava`, `test_sessionstart_invalido_nao_cria_nem_levanta` | PASS |
| AC-1.6 | J `test_falha_de_escrita_e_engolida_e_contada`, `test_contagens_tambem_falhando_nao_levanta`, `test_main_nao_levanta_se_o_spool_explode`, `test_import_quebrado_do_spool_nao_quebra_o_sensor` | PASS |
| AC-1.7 | J `test_spool_cheio_nao_grava_e_conta`, `test_limite_padrao_e_200_mib` | PASS |
| AC-1.8 | J `test_p95_do_registrar_abaixo_de_20_ms`, `test_p95_com_muitas_sessoes_na_pasta` (no hook instalado: não observado) | PASS em teste |
| AC-2.1 | `lote::test_seco_recusa_com_a_janela_aberta` | PASS |
| AC-2.2 | `lote::test_seco_monta_os_turnos_sem_rede_e_sem_a_ancora`, `test_seco_descarta_sessoes_quebradas`, `v4::test_spool_com_campo_*`, `test_sessao_do_item_vem_do_nome_do_arquivo`, `test_surrogate_*`, `v4b::test_seco_tolera_*` | PASS |
| AC-2.3 | `lote::test_residuo_que_escapa_da_redacao_exclui_o_item`, `test_item_com_residuo_fica_fora_*`, `test_rodar_nao_envia_item_com_residuo`, `v4b::test_varredura_do_seco_*` | PASS |
| AC-2.4 | `lote::test_codigo_da_amostra_e_deterministico_e_preso_ao_spool`, `test_ver_amostra_*` | PASS |
| AC-2.5 | `lote::test_rodar_pontua_*`, `_recusa_codigo_errado`, `_sem_seco`, `_kit_mudou`, `_spool_mudou`, `_spool_removido_*`, `test_falha_de_infra_e_retomada`, `test_envio_divergente_nao_conta` | PASS |
| AC-2.6 | `lote::test_rodar_recusa_teto_da_janela_antes_da_sonda`, `test_teto_da_janela_soma_*`, `test_rodar_recusa_envelope_do_mes`, `test_livro_corrompido_*` | PASS |
| AC-2.7 | `lote::test_rodar_recusa_sonda_divergente`, `_sonda_sem_resposta`, `_vazamento_pendente`, `_revisao_c4_pendente` | PASS |
| AC-3.1 | `veredito::test_rotular_recusa_com_infra_pendente` | PASS |
| AC-3.2 | `veredito::test_fila_tem_sinalizados_e_chamarizes_*`, `test_grupo_menor_que_o_teto_entra_inteiro` | PASS |
| AC-3.3 | `veredito::test_tela_nao_mostra_escore_nem_grupo` | PASS |
| AC-3.4 | `veredito::test_pular_conta_*`, `test_resposta_invalida_*`, `test_corrigir_*` | PASS |
| AC-4.1 | `veredito::test_medir_recusa_fila_incompleta` | PASS |
| AC-4.2 | `veredito::test_avanca_*`, `test_nao_avanca_*`, `test_inconclusivo_*`, `test_bootstrap_degenerado_vira_inconclusivo`, `test_pulados_*` | PASS |
| AC-4.3 | `veredito::test_medir_grava_veredito_relatorio_sem_texto_e_trava_tudo`, `v4::test_relatorio_*` | PASS |
| AC-4.4 | `veredito` (corrigir, medir, rodar, seco), `limpeza::test_depois_do_purgar_*`, `v4b::test_rotular_recusa_depois_do_veredito` | PASS |
| AC-4.5 | `v4::test_medir_nao_muda_settings_nem_plugin_e_imprime_os_passos` | PASS |
| AC-5.1 | `limpeza::test_excluir_sessao_*`, `v4::test_excluir_sessao_em_uso_*`, `v4b::test_excluir_sessao_que_nao_apaga_*`; Jb excluída não volta | PASS |
| AC-5.2 | `limpeza::test_purgar_com_veredito_*`, `test_purgar_encerra_antes_de_apagar_*`, `v4b::test_purgar_avisa_*` | PASS |
| AC-5.3 | `limpeza::test_purgar_sem_veredito_recusa`, `test_purgar_abandonar_*` | PASS |
| AC-5.4 | `limpeza::test_saude_*`, `v4::test_saude_acusa_texto_retido_depois_da_purga`, `v4b::test_saude_*`, `test_checar_*` | PASS |

## Suposições

| ASSUMPTION | Estado | Evidência |
|---|---|---|
| 001 a 007, 013, 014 (decisões do usuário) | VALE | decisões registradas na spec; o código segue cada uma (tabelas acima) |
| ~~008~~ | FALSA desde a v4 | riscada na spec; substituída pela 013 |
| 009 (prefixos cobrem os turnos não humanos) | NAO_OBSERVADO | só o `status` da janela real mostra o que escapa |
| 010 (~165 prompts por dia, ~US$ 0,16) | NAO_OBSERVADO | idem |
| 011 (14 dias de prompt cru não cria classe nova de risco) | NAO_OBSERVADO | é um julgamento; a mitigação está no `purgar` e no `mh saude` |
| 012 (edição manual do `janela.json` não é detectada) | VALE | por construção: o hook só compara `estado`, `fim` e `sha_filtro` |

## Boundaries

| Regra | Verificação | Status |
|---|---|---|
| NEVER: rede, processo, chave ou criação de diretório no hook | J `test_sem_rede_sem_processo_sem_ler_segredos` | PASS |
| NEVER: mostrar algo do J ao usuário durante a janela | J `test_main_saida_identica_com_e_sem_flag` | PASS |
| NEVER: ler a pasta `sombra/` fora do hook, dos verbos e do `mh saude` (janela e nomes) | inspeção: `checar_sombra` só lista nomes; nenhum outro leitor no grep | PASS |
| NEVER: executar código do plugin no processo do mh (rodada 3) | `v4b::test_abrir_le_o_filtro_sem_executar_o_plugin` | PASS |
| ALWAYS: terminal humano nos verbos que mexem em texto ou dinheiro | testes `*_fora_de_terminal_humano` | PASS |
| ASK: deploy D2 | não feito; espera o OK do usuário, que o orquestrador pede | PASS (pendente por regra) |

## Success criteria

| Critério | Target | Medido | Status |
|---|---|---|---|
| p95 do hook com a flag ligada | ≤ 20 ms | abaixo de 20 ms nos dois testes, com o spool real | PASS em teste |
| suítes do mh (avaliacao, saude, órfãos, derivados, congelamento, stdin) | verde | 390 passed | PASS |
| `tests/s1/test_s1_fundacao.py` (rodado em separado) | verde | 12 passed | PASS |
| suítes do h4 (branch, hooks, transactional, órfãos, stdin) | verde | 646 passed, 18 skipped (ambiente) | PASS |
| SHA do filtro por AST igual ao do spool real | igual | igual (script de conferência e teste Jb) | PASS |

## Nível de garantia

| Afirmação da spec | O que a sustenta | Status |
|---|---|---|
| medida em modo sombra, sem efeito no usuário | flag desligada por default; saída idêntica; o veredito não muda código | SUSTENTA |
| o texto só sai redigido, e um resíduo conhecido exclui o item | `PonteJev` (kit T2) e a varredura como segunda rede | SUSTENTA, nos padrões listados. A varredura não pega dado pessoal fora deles |

## Próximos passos

1. O usuário decide o deploy D2 (o orquestrador pede).
2. Merge dos dois ramos, pelo orquestrador.
3. Com o OK: `mh avaliar camada-j sombra abrir` num terminal humano, seguido do teste de vida do dia 0.

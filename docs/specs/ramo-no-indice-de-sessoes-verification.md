# Verificação — Ramo visível no índice de sessões

**Task:** t-20260903-025351273585 · spec: `ramo-no-indice-de-sessoes-spec-light.md`

## Cobertura item por item

| item | evidência | veredicto |
|---|---|---|
| REQ-1 `branch_links` varre buckets | `build_sessions_index.py:branch_links` · `test_ac1_vinculo_de_mao_dupla` | coberto |
| REQ-2 catálogo com `branch_of`/`branches` | `build_catalog(..., links=)` · `test_ac5_catalogo_traz_os_campos` | coberto |
| REQ-3 chunk com `branch_of` | `session_chunks(..., links=)` · `test_req3_chunk_carrega_branch_of`, `test_req3_chunk_sem_vinculo_traz_none` | coberto |
| REQ-4 degradação silenciosa | `test_ac2_sem_registro_devolve_vazio`, `test_ac2b_raiz_inexistente_nao_levanta`, `test_ac3_json_quebrado_nao_contamina_os_outros` | coberto |
| REQ-5 ramo sem `session_id` fora | `test_ac4_ramo_pendente_sem_sessao_nao_entra` | coberto |
| AC-1 vínculo de mão dupla | `test_ac1_vinculo_de_mao_dupla` | passa |
| AC-2 sem registro → `{}` | `test_ac2_sem_registro_devolve_vazio` | passa |
| AC-3 JSON inválido isolado | `test_ac3_json_quebrado_nao_contamina_os_outros` | passa |
| AC-4 `session_id` nulo ignorado | `test_ac4_ramo_pendente_sem_sessao_nao_entra` | passa |
| AC-5 campos sempre presentes | `test_ac5_catalogo_traz_os_campos` | passa |
| AC-6 dois ramos, ordem estável | `test_ac6_dois_ramos_do_mesmo_pai_em_ordem_estavel` | passa |
| ALWAYS degradar em silêncio | REQ-4 acima | coberto |
| ALWAYS `git_branch` intocado | campo preservado; `branch_of` é adicional | coberto |
| NEVER build falhar por `branches.json` | `test_ac3_...` (JSON quebrado não propaga) | coberto |
| NEVER inventar vínculo por heurística | `branch_links` só lê `parent_session`/`session_id`; nenhum teste de similaridade existe | coberto por construção |

## Item que a spec não previu e o teste cobre

**Fiação.** As três funções aceitam `links`; nada garantia que alguém passasse.
`test_scan_sessions_usa_o_registro_de_ramos` trava a chamada real. É o modo de
falha que manteve `verify-multimodel` declarado e inalcançável por cinco dias, e
que esta sessão já encontrou duas vezes (`source-selection`, `documentation`).

## Limite conhecido, declarado

O índice em disco continua **sem** os campos novos até ser reconstruído: o build
exige Ollama e não roda aqui. `branch_links` devolve `{}` hoje de qualquer forma
— zero `branches.json` em 35 buckets — então a reconstrução não mudaria nenhuma
linha. O primeiro ramo aceito é o que torna isto observável.

## Evidência de suíte

`python -m pytest -q` — ver commit desta task.

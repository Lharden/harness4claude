# Verification Report — vault-sync-fontes

**Date**: 2026-09-24 · **Status**: PASS no escopo; os dois vermelhos da suíte completa foram consertados ou explicados abaixo, com reverificação focada
**Pipeline**: L2-bug (`systematic-debugging → graph-context → grill-me → tdd → verify`), task `t-20260924-151723410950`
**Branch**: `fix/vault-sync-fontes`, a partir de `main` em `844d3a5` (depois do merge de `fix/vault-sync-recusa-por-hash`).

## Defeitos, medidos com as funções de produção antes do conserto (só leitura)

1. **Sementes de ramo nunca chegavam ao vault pelo hook.** `harness-precompact.sh` passa em `--harness-dir` o balde da SESSÃO, e `vault_sync.branch_seeds` tratava o valor como a RAIZ: resolvia `<balde>/projects/<slug>/branches`, caminho que não existe. `branch_seeds(raiz, cwd)` = 7 (slb-mestrado-projeto) e 4 (harness4claude); `branch_seeds(balde, cwd)` = 0 nos dois. Eram 29 sementes em `~/.claude/harness/projects/*/branches` (5 projetos, 29 nomes únicos) e 1 página em `AI-Brain/wiki/branches`. O escritor, `branch_state.branches_dir(cwd)`, resolve `state_dir(cwd)` a partir da raiz e sem sessão. A suíte ficava verde porque `test_semente_de_ramo_vai_para_o_vault` e `test_is_mirrored_cobre_todo_destino_que_o_sync_escreve` usavam o mesmo diretório como raiz e como balde.
2. **Notas diárias de repos diferentes colidiam em `raw/inbox`.** Todo repo tem `.remember/today-X.md` com os mesmos nomes. No AI-Brain real, 66 páginas `today-*`, 85 fontes vivas em 8 repos, 22 nomes com mais de uma fonte. 33 versões de 7 repos estavam fora do vault porque a nota mais nova de outro repo ocupava o nome. 6 delas eram do slb, todas vencidas por nota mais nova; o enunciado mediu 5 numa cópia, e a diferença não foi investigada.
3. **Nota tirada do inbox voltava** (achado no diagnóstico, incluído por decisão do usuário). `_espelhar` escrevia sempre que a página faltava. 11 nomes estavam ao mesmo tempo em `raw/inbox/_processed/` e em `raw/inbox/`.
4. **Sessão em worktree não espelhava nota nenhuma** (achado do grill). O plugin remember escreve no checkout principal; nenhum dos 9 worktrees medidos tinha `.remember`.

## Decisões (usuário, 2026-09-24)

| # | Decisão |
|---|---|
| D1 | Nome da nota no inbox: `<repo>--today-X.md`. O rótulo é a pasta que contém o `.remember`. |
| D2 | Legado: renomear o que tem dono provado, com ensaio, backup e OK explícito; o resto fica intocado e listado. |
| D3 | Nota consumida não volta, nesta tarefa. |
| D4 | Escopo do nome: só o inbox. `wiki/specs` (1 nome em 2 repos hoje) e `wiki/branches` (0 colisões em 29) ficam registrados como conhecidos. |
| G1 | Fontes: o `.remember` do checkout dono do `cwd` (`_escopo.raiz_do_repo`, a mesma identidade do balde), o do `cwd` quando for outro, e `C:/.remember` com rótulo fixo `maquina`. |
| G2 | Consumida = o manifesto registra a escrita e a página sumiu, OU `_processed/` tem cópia de mesmo conteúdo, com o nome novo ou o antigo. Fonte com conteúdo novo volta. |
| G3 | Migração renomeia por igualdade de bytes OU quando só um repo tem aquele nome (cópia velha; o sync atualiza). As perdidas por colisão entram como páginas novas. |
| G4 | `--harness-dir` continua sendo o BALDE, a convenção de `record_signal.py`, `confirm_classification.py` e `expire_stale_pipeline.py`. Entram `--raiz` e `--cwd`. A linha `p1b-testes-hermeticos-spec.md:264` era limite da task P-1.b e não foi editada. |

Por discrição, declarado ao usuário: a guarda compara sha e origem, e outra origem no mesmo destino cai na regra "a mais nova vence"; `is_mirrored` mantém `raw/inbox/today-*.md` protegido para o legado; o backup guarda páginas, entradas antigas do manifesto e `mapa.json`; a migração é idempotente.

## O que mudou

| Arquivo | Mudança |
|---|---|
| `scripts/vault_sync.py` | `sync()` recebe `raiz` (obrigatório, por nome) separado do balde: sementes e manifesto saem da raiz, traces do balde. `fontes_do_inbox()` substitui `remember_today()` e dá o nome com rótulo. `_foi_consumida()` e o parâmetro `consumo` (ligado só para `raw/inbox`). `remember_global` injetável. CLI: `--raiz`, `--cwd`; `--harness-dir` segue sendo o balde. `is_mirrored` cobre `raw/inbox/<repo>--today-*.md` e mantém o padrão legado. |
| `hooks/harness-precompact.sh` | Passa `--raiz`, `--harness-dir` (balde) e o `cwd` do payload, o mesmo que escolheu o balde. O `--manifesto` explícito saiu: o padrão já é a raiz. Nenhuma emissão nova. |
| `tools/migrar_inbox_rotulado.py` | Novo. Ensaio por padrão; `--aplicar` com `--backup` fora do vault e vazio; `--restaurar` cirúrgico no manifesto e que não desfaz página editada depois. |
| `tools/orfaos.json` | As 4 funções públicas da migração declaradas como `FERRAMENTA_DE_MAO`, com o consumidor. |
| `tests/test_harness.py` | `test_28e`: ponta a ponta pelo hook, com `cwd` do payload diferente do `cwd` do processo. |
| `tests/test_vault_sync.py` | 8 testes novos; as 30 chamadas de `sync` declaram `raiz=`; o teste de semente usa raiz e balde diferentes; a docstring que citava o inbox como colisão ganhou ressalva `[superado ...]`. |
| `tests/test_wiki_e2e.py` | As 7 chamadas de `sync` declaram `raiz=`. |
| `tests/test_migrar_inbox_rotulado.py` | Novo, 13 testes. |
| `README.md`, `tools/README.md` | O que o espelho cobre e a linha da ferramenta. |

## Evidência: RED antes, GREEN depois, e mutantes

RED observado antes de cada conserto: `test_28e` falhou com "semente nao espelhada" e o log vazio; os 7 testes do inbox falharam por comportamento (nome sem rótulo, colisão, worktree vazio, nota que voltava). Isso depois de um primeiro RED por `TypeError`, que foi refeito criando só o parâmetro. Os testes da migração falharam por módulo inexistente. Exceção declarada: `test_nota_consumida_com_rotulo_repetido_so_volta_pela_fonte_mais_nova` foi escrito depois da regra e nasceu verde; a prova de que ele pega o defeito é o mutante abaixo.

Mutantes, cada um aplicado, testado e restaurado byte a byte por script (`restaurado: True` em todos):

| Mutante | Teste que reprovou |
|---|---|
| sementes lidas do balde (`branch_seeds(harness_dir, cwd)`) | `test_28e`, `test_semente_de_ramo_vai_para_o_vault` |
| hook sem `--cwd` | `test_28e` |
| inbox sem rótulo (`rename=None`) | `test_notas_de_repos_diferentes_com_o_mesmo_nome_nao_colidem` |
| guarda de consumo desligada | os 3 testes de nota consumida |
| só o `cwd`, sem o checkout dono | `test_sessao_em_worktree_espelha_as_notas_do_checkout_dono` |
| `C:/.remember` com o rótulo do `cwd` | `test_remember_global_tem_rotulo_fixo_e_uma_pagina_so` |
| `_processed/` só com o nome novo | `test_nota_processada_com_nome_antigo_nao_volta_com_nome_novo` |
| outra origem recria sempre | `test_nota_consumida_com_rotulo_repetido_so_volta_pela_fonte_mais_nova` |
| migração sem o sha da fonte no manifesto | `test_dono_provado_pelos_bytes_e_renomeado_e_o_sync_nao_duplica` |
| migração sem regra de ambiguidade | `test_bytes_iguais_aos_de_dois_repos_e_ambigua` |
| restaurar sem conferir o sha | `test_restaurar_nao_desfaz_pagina_editada_depois_da_migracao` |
| backup aceito dentro do vault | `test_aplicar_recusa_backup_dentro_do_vault_ou_ja_usado` |

## Ensaio da migração no AI-Brain real (só leitura, 2026-09-24)

Com os 8 repos que têm `.remember/today-*`: **41** renomes com dono provado pelos bytes, **5** cópias velhas de dono único, **0** ambíguas, **20** sem fonte viva, **0** conflitos. **37** notas entram no próximo sync: 3 recentes de fonte única, ainda não espelhadas, e 34 de nomes que existem em mais de um repo.

## Suíte completa

Uma execução, com `AI_BRAIN_PATH` temporário: **1512 passed, 2 failed, 2 skipped** (18 min 40 s).

1. `test_console_utf8::test_todo_entrypoint_de_tools_chama_usar_utf8[migrar_inbox_rotulado.py]`. Defeito desta branch: o ponto de entrada novo não chamava `usar_utf8`. Consertado com o mesmo bloco de `tools/vault_maintenance.py`. Reverificado: `test_console_utf8.py` + `test_migrar_inbox_rotulado.py` = 69 passed.
2. `test_deploy_drift::test_o_cache_reflete_o_publicado`. O cache divergia de `main` em 11 arquivos, todos do merge de `fix/vault-sync-recusa-por-hash` e nenhum desta branch. O deploy de `main` foi feito por outra sessão enquanto a suíte rodava. Reverificado depois: `test_deploy_drift.py` = 14 passed.

A suíte inteira não foi repetida (norma de custo do autor: uma suíte completa no fim). Os dois pontos foram reverificados com os testes focados acima.

`bash scripts/health-check.sh`: um FAIL, "MESMA versao, CONTEUDO diferente". Ele existe por construção: compara a árvore desta branch, ainda não instalada, com o plugin instalado. O checkout principal, com o conteúdo de `main`, passa sem FAIL. Instalar só depois do merge, pelo `main`. Um WARN anterior a esta branch: "camada B em cooldown".

`python tools/orfaos.py --report`: sem órfão não declarado (97 declaradas).

## Pendências, com dono

1. **Aplicar a migração no vault real**: depende do merge desta branch e do deploy do plugin, em cada máquina que roda o harness. Com o plugin antigo, o PreCompact seguinte recriaria o nome sem rótulo. Dono: o usuário (OK explícito antes do `--aplicar`).
2. **`AI-Brain/CLAUDE.md:128`** ainda descreve `.remember/today-*.md -> raw/inbox/`. Escrever no vault pede OK do usuário; texto proposto: `<repo>/.remember/today-*.md -> raw/inbox/<repo>--today-*.md`.
3. **Três testes rodam o PreCompact sem isolar o vault** (`test_27`, `test_30`, `test_hook_liveness`): com `VAULT_PATH` definido e `AI_BRAIN_PATH` vazio, o sync deles mira o AI-Brain real. Esta suíte rodou com `AI_BRAIN_PATH` temporário. Conserto de raiz numa sessão separada, aberta pelo usuário.
4. **Conhecidos, fora do escopo (D4)**: `wiki/specs` tem 1 nome em 2 repos (`hce-workflow-post-qualification-spec-light.md`); `wiki/branches` tem 0 colisões em 29 sementes, num namespace plano; a guarda de consumo vale só para `raw/inbox`.

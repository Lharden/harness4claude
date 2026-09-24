# Verification Report — vault-sync-recusa-por-hash

**Date**: 2026-09-24 · **Status**: PASS no escopo, com dois vermelhos anteriores declarados abaixo
**Pipeline**: L1-bug (`systematic-debugging → tdd → verify`), task `t-20260924-135202678393`
**Origem**: item 1.8 do plano `sunny-plotting-teacup` do projeto slb-mestrado-projeto, cuja spec (`docs/specs/registros-vault-vivos-spec.md:257`) manda o conserto do `vault_sync.py` para este repositório.

## Defeitos, reproduzidos com a função de produção antes do conserto

1. **Edição humana apagada.** `mirror` decidia por mtime: fonte mais nova que a página = copiar. A página editada no Obsidian era sobrescrita na primeira mudança da fonte, sem backup. Reproduzido chamando `vault_sync.mirror` num vault temporário.
2. **Um erro derrubava o lote, e o hook engolia tudo.** Um `OSError` num arquivo interrompia o `sync()` inteiro, e `harness-precompact.sh` rodava o script com `>/dev/null 2>&1 || true`. Reproduzido com o destino ocupado por uma pasta.

## O que mudou

| Arquivo | Mudança |
|---|---|
| `scripts/vault_sync.py` | Manifesto `vault-sync-manifest.json` fora do vault: por página, o sha dos bytes escritos, o sha da fonte, a origem e o mtime da fonte. A decisão por página fica em `_espelhar`. `try/except OSError` por página. Eventos (recusa, falha, manifesto ilegível) vão para `sync(..., eventos=)` e saem como WARNING no stderr. Nova flag `--manifesto`. `newer()` removido, já sem uso. |
| `hooks/harness-precompact.sh` | A saída vai para `$HARNESS_DIR/logs/vault-sync.log`, com fallback para `/dev/null` se o log não abre e rotação para `.1` acima de 512 KB. O manifesto é passado na raiz do harness. |
| `tests/test_vault_sync.py` | 13 testes novos. |
| `tests/test_harness.py` | 3 testes de ponta a ponta do PreCompact (28b, 28c, 28d). |
| `README.md` | Uma frase: onde ficam o manifesto e o log. |

### Regra de decisão, por página

| Situação | Ação |
|---|---|
| destino ausente | escreve e registra |
| sem registro, página igual ao que o sync escreveria (a menos de `created`/`updated`/`project` e do fim de linha) | **adota**: registra sem escrever |
| sem registro, página diferente e fonte mais nova que ela | **recusa com aviso** (é onde o espelho por mtime sobrescreveria) |
| sem registro, página diferente e fonte mais velha | não escreve e não avisa (o espelho antigo também não tocaria) |
| com registro, fonte com o mesmo sha | pula |
| com registro de OUTRA origem e esta fonte não é mais nova | pula (colisão de nome: vence a mais nova, como antes) |
| com registro, página com sha diferente do escrito | **recusa com aviso** (edição fora do sync) |
| com registro, página intocada e fonte nova | escreve e registra |

## Duas correções que a medição impôs no meio do caminho

A medição rodou a função de produção (`vault_sync.sync`) sobre uma **cópia** do AI-Brain real (`wiki/specs`, `wiki/decisions`, `wiki/branches`, `raw/inbox`), com os quatro projetos locais na ordem da máquina e o manifesto vazio. Nada escreveu no vault real.

1. **Ping-pong entre projetos.** A primeira versão, só com hash, reescrevia **30 páginas na segunda passagem sem mudança nenhuma** (13 + 8 + 9). Arquivos de mesmo nome de projetos diferentes caem na mesma página (`raw/inbox/today-*.md`, specs), e cada projeto sobrescrevia a do outro. O mtime antigo resolvia a colisão como "a mais nova vence" e parava. Consertado com origem e mtime no registro. Teste: `test_duas_fontes_para_a_mesma_pagina_nao_se_alternam`.
2. **Aviso perpétuo sem ação possível.** Sobraram 5 recusas repetidas a cada PreCompact: páginas de `raw/inbox` de **outro** repositório com `.remember/` de mesmo nome, mais novas que o arquivo do SLB. O espelho antigo nunca as tocaria. Agora o aviso de primeiro contato só sai quando a fonte é mais nova que a página. Teste: `test_primeiro_contato_com_pagina_mais_nova_que_a_fonte_nao_escreve_nem_avisa`.

**Estado final medido sobre a cópia:** primeira passagem com 0 avisos e 17 escritas, todas de páginas ausentes do vault (slb: 3 specs + 3 inbox; science-harness: 10 specs + 1 inbox). Segunda passagem: **0 escritas, 0 avisos** nos quatro projetos.

## Falsificação: 16 mutantes, 16 mortos

Cada mutante reintroduz um defeito no arquivo real. O teste nomeado tem de cair; depois o arquivo é restaurado e o sha256 conferido.

| Mutante | Teste que caiu |
|---|---|
| M1 edição humana não é conferida | `test_edicao_humana_sobrevive_a_mudanca_da_fonte` |
| M2 fonte igual não pula | `test_mtime_novo_sem_mudanca_de_conteudo_nao_reescreve` |
| M3 adoção sempre recusa | `test_primeiro_contato_adota_pagina_igual_a_menos_de_datas_slug_e_fim_de_linha` |
| M4 adoção sempre aceita | `test_primeiro_contato_recusa_pagina_diferente` |
| M5 erro de E/S derruba o lote | `test_erro_num_arquivo_nao_derruba_o_lote` |
| M6 manifesto dentro do vault | `test_manifesto_fica_fora_do_vault`, `test_manifesto_padrao_fica_no_harness_dir` |
| M7 manifesto corrompido derruba o sync | `test_manifesto_corrompido_vale_como_vazio_e_avisa` |
| M8 slug não é ignorado na adoção | teste de adoção |
| M9 fim de linha não é ignorado na adoção | teste de adoção |
| M10 CLI cala os eventos | `test_cli_imprime_recusa_no_stderr_...`, `test_28b_...` |
| M11 hook descarta a saída | `test_28b_erro_do_vault_sync_vai_para_o_log` |
| M12 hook sem `--manifesto` (cai no bucket) | `test_28c_manifesto_do_vault_sync_fica_na_raiz` |
| M13 hook sem rotação | `test_28d_log_do_vault_sync_rotaciona` |
| M14 fontes de mesmo nome se alternam | `test_duas_fontes_para_a_mesma_pagina_nao_se_alternam` |
| M15 primeiro contato avisa com a página mais nova | `test_primeiro_contato_com_pagina_mais_nova_que_a_fonte_nao_escreve_nem_avisa` |
| M16 primeiro contato cala sempre | `test_primeiro_contato_recusa_pagina_diferente` |

Os dois testes de mtime (M2 e o da página com mtime mexido) caíram também contra o código antigo, sem mutante. Os demais caíram antes do conserto por API ausente; a metade comportamental deles é a dos mutantes.

## Suíte e diagnóstico

- Arquivos afetados, na árvore final (`test_vault_sync.py`, `test_wiki_e2e.py`, `TestPrecompact`, `test_harness_dir_resolution.py`, `test_orfaos.py`, que lê o README): **77 passando**. `ruff check`: limpo.
- Suíte completa, uma vez (`python -m pytest -q`, 14 min): **1487 passando · 1 pulado · 1 falha**. A falha é `test_deploy_drift.py::test_o_cache_reflete_o_publicado`, que compara o cache do plugin com `main` e não depende deste ramo; a causa está na tabela abaixo.
- `bash scripts/health-check.sh`: tudo OK, exceto a proveniência (tabela abaixo).

## Vermelhos anteriores, declarados com causa, ação e dono

| Guarda | Causa medida | Ação | Dono |
|---|---|---|---|
| `test_deploy_drift.py::test_o_cache_reflete_o_publicado` | `main` recebeu `59b3760` em 2026-09-24 10:20 (bloco do CLAUDE.md, regra L0) e o cache do plugin não foi reimplantado; o arquivo no cache é de 2026-08-31. Falha igual em `main`. | `python scripts/deploy_to_cache.py --apply` a partir de um checkout de `main` | quem mesclou o `59b3760` (frente System One), com o OK do autor |
| `health-check.sh`, proveniência: "MESMA versão, CONTEÚDO diferente" | compara a árvore de trabalho com o cache instalado; falha em `main` pela mesma causa acima e em qualquer ramo aberto com mudança | a mesma do deploy, depois do merge deste ramo | idem |

## Fora do escopo, registrado

- **Sementes de ramo nunca chegam ao vault pelo hook.** O hook passa o balde da sessão como `--harness-dir`, e `branch_seeds` o trata como raiz. Medido: 0 sementes pelo balde, contra 7 (slb) e 4 (harness4claude) pela raiz; há 29 sementes nos baldes e 1 página em `wiki/branches`. Defeito anterior, contradiz `branch-keeper-design.md:50`. Registrado como tarefa separada.
- **Notas diárias de repositórios diferentes colidem em `raw/inbox`.** Só a mais nova de cada nome sobrevive no espelho. Mudar o nome de destino afeta quem consome `raw/inbox`, e por isso é decisão do autor. Registrado na mesma tarefa.
- **`tools/vault_maintenance.py` (`normalize`/`organize`) reescreve páginas espelhadas.** Com o manifesto, uma página normalizada por ele passa a ser recusada na próxima mudança da fonte: nada se perde, mas o espelho dela para até alguém resolver. Hoje há 0 casos. Excluir as pastas espelhadas da manutenção é decisão pendente do autor.
- **Transição.** Uma página existente sem registro, diferente da fonte e mais velha que ela é recusada com aviso na primeira execução, mesmo que ninguém a tenha editado, porque sem histórico não há como distinguir. Na cópia do vault real, isso deu 0 casos.

## Nível de garantia

| Afirmação | O que sustenta | Status |
|---|---|---|
| "Página editada no vault não é sobrescrita pelo PreCompact" | M1 e M4 mortos; adoção e recusa testadas; ponta a ponta pelo hook (28b) | SUSTENTA |
| "Erro do sincronizador aparece no log" | M5, M10 e M11 mortos; 28b lê o log gravado pelo hook real | SUSTENTA |
| — | nada afirma que as sementes de ramo são espelhadas; não são, e está declarado acima | SUSTENTA |

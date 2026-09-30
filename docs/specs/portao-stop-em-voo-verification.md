# Portão de Stop com trabalho em voo — verificação

Ramo `claude/magical-shamir-b49924`, task `t-20260930-053203156690`, pipeline
L2-bug. Diagnóstico e decisões: `portao-stop-em-voo-diagnostico.md`.

## O que mudou

| Arquivo | Mudança |
|---|---|
| `scripts/trabalho_em_voo.py` (novo) | `jobs_em_voo(transcript_path, *, desde, agora)`: jobs lançados em segundo plano por esta sessão, depois de `desde`, sem término no transcript |
| `scripts/transactional_state.py` | `register_stop_continuation(..., em_voo=())`: com job em voo não conta, não escala e grava `stop_nao_cobrado` em `events`; `stops_nao_cobrados(task_id)` lê a contagem |
| `hooks/harness-transactional.py` | `_handle_stop` lê o transcript e entrega a lista ao banco; `_motivo_do_gate` abre com "este Stop NAO foi cobrado" e traz `stops_nao_cobrados=N`; `_handle_post_tool` não grava evidência do lançamento em segundo plano e avisa com a receita manual. Mais o conserto absorvido de `fix/portao-subagente-fora-da-raiz` (seção própria abaixo) |
| `tests/test_portao_em_voo.py` (novo) | 36 testes |
| `tests/test_transactional_hook.py` | só a docstring de `test_aviso_de_background_quando_nao_ha_casos`, que descrevia a gravação vazia como comportamento atual |

## Cobertura, decisão por decisão

| Item | Evidência |
|---|---|
| Causa: fim de turno com job em voo contava como tentativa de encerrar | `test_incidente_job_em_voo_bloqueia_sem_cobrar` — vermelho em `c175fd9` (`assert 2 == 0`: o contador subiu e o terceiro Stop abriu `escalation`), verde depois |
| D1 — bloqueia, não cobra | mesmo teste: três `decision: block`, `stop_continuations == 0`, `pending_gate is None` |
| D2 — só jobs da task atual | `test_so_conta_lancamento_depois_do_inicio_da_task`, `test_CONTROLE_job_de_antes_da_task_nao_isenta` |
| D3 — sem teto, contagem visível | `test_escalada_mostra_quantos_stops_nao_foram_cobrados`, `test_register_stop_continuation_com_job_em_voo_nao_mexe_no_contador` |
| D4 — lançamento não é resultado | `test_suite_lancada_em_background_nao_grava_evidencia[forma-real-do-payload, so-texto]` — vermelho em `c175fd9` (`assert 1 == 0`: uma linha em `evidence`), verde depois |
| Contador congela, não zera | `test_contador_fica_congelado_durante_o_voo_e_nao_zera` — vermelho em `c175fd9` (`escalation` aberto com job em voo) |
| Metade 2: job terminado não isenta | `test_CONTROLE_job_terminado_nao_isenta` (verde antes e depois) |
| Metade 2: sem transcript legível cobra como hoje | `test_CONTROLE_sem_transcript_legivel_cobra_como_hoje[ausente, sem-campo]` |
| Metade 2: primeiro plano continua gravando | `test_CONTROLE_suite_em_primeiro_plano_continua_gravando` |
| Quatro formas de término | `test_notificacao_com_status_encerra_o_job` (5 status, incluindo um inexistente), `test_termino_vale_em_qualquer_entrada_do_host` (user, queue-operation, queued_command), `test_monitor_expirado_esta_encerrado`, `test_monitor_alem_do_prazo_do_host_esta_encerrado`, `test_resumo_de_orfaos_encerra_todos_os_ids_do_bloco`, `test_task_stop_bem_sucedido_encerra_sem_notificacao` |
| Lançamento só por chave, nunca por texto | `test_texto_que_cita_o_campo_nao_e_lancamento` |
| Leitura tolerante | `test_linha_quebrada_nao_esconde_as_outras`, `test_transcript_ausente_devolve_nada` |

## Medido com a função de produção

`scripts/trabalho_em_voo.jobs_em_voo` sobre o transcript real do incidente
(`great-dirac-dbb262/6b215b3f-....jsonl`), cortado no instante de cada Stop,
`desde` = `started_at` da task (`2026-09-29T13:07:10.193197+00:00`):

| Stop (UTC) | Na época | `jobs_em_voo` |
|---|---|---|
| 13:09:42 | cobrou #1 | `bzrg3z3z3` (shell) |
| 13:20:05 | cobrou #2 | `b36bf65ya` (shell), `blkfa974t` (monitor) |
| 13:20:51 | abriu `escalation` | `b36bf65ya` (shell), `blkfa974t` (monitor) |
| 13:13:30 (controle) | — | nenhum — `bzrg3z3z3` terminou às 13:13:11 |
| 13:31:00 (controle) | — | nenhum — `b36bf65ya` terminou às 13:28:40, `blkfa974t` parado por `TaskStop` às 13:29:07 sem notificação |

O último controle só dá vazio porque o `TaskStop` conta como término: o
`blkfa974t` nunca recebeu `<task-notification>` terminal.

Custo: maior transcript da máquina (129 MB, sessão `86459dbf`) em **0,72 s**
com a função de produção; timeout do hook de Stop, 15 s. O único job que ela
acusa em voo ali é o `bh9quzdpy`, lançado em 2026-09-10 e sem término nenhum
no transcript — o job perdido do diagnóstico. Com o recorte por task (D2) ele
só isentaria a task em que nasceu.

Script da medida: `replay.py` no scratchpad da sessão `36cef71c`.

## Falsificação por mutação

`mutar.py` (scratchpad da sessão `36cef71c`): copia `hooks/`, `scripts/` e
`contract/` para uma pasta temporária, aplica UMA mutação, aponta
`HARNESS_PLUGIN_ROOT` para a cópia e roda `tests/test_portao_em_voo.py` +
`tests/test_transactional_hook.py`. Cada mutação exige âncora de texto com uma
única ocorrência no arquivo; âncora ausente ou repetida é recusada.

| Mutante | Resultado | Testes que caem |
|---|---|---|
| M0 código consertado | 123 passed | — |
| M1 banco ignora `em_voo` | 4 failed | incidente, contador congelado, escalada mostra contagem, `register_stop_continuation` com job em voo |
| M2 voo zera o contador | 2 failed | contador congelado, `register_stop_continuation` com job em voo |
| M3 ignora notificação terminal | 14 failed | os 5 status, as 3 entradas do host, órfãos, … |
| M4 sem recorte por task | 2 failed | recorte por `started_at` (unidade e hook) |
| M5 só o primeiro `<task-id>` do bloco | 1 failed | resumo de órfãos |
| M6 ignora `TaskStop` | 1 failed | `TaskStop` sem notificação |
| M7 Monitor persistente conta | 1 failed | Monitor persistente |
| M8 com job em voo não bloqueia (forma a1) | 2 failed | incidente, contador congelado |
| M9 lançamento grava evidência | 3 failed | as 2 formas do lançamento + `test_aviso_de_background_quando_nao_ha_casos` |
| M10 mensagem sem `stops_nao_cobrados` | 1 failed | escalada mostra contagem |
| M11 só `completed` encerra | 6 failed | 4 status, órfãos, contador congelado (`failed`) |
| M12 ignora prazo do Monitor | 1 failed | Monitor além do prazo |
| M13 lançamento de subagente conta | 1 failed | subagente |
| M14 Monitor expirado não encerra | 1 failed | Monitor expirado |
| M15 hook não lê o transcript | 3 failed | incidente, contador congelado, escalada mostra contagem |
| M16 lançamento lido de texto | 1 failed | texto que cita o campo |

Dois erros de instrumento, registrados porque quase viraram conclusão: na
primeira rodada **M6 foi recusado** (âncora com indentação de 8 espaços, o
código tem 16) e **M16 sobreviveu** — a regex do mutante procurava
`backgroundTaskId": "` num `json.dumps`, onde as aspas aninhadas saem
escapadas, então o mutante nunca lia lançamento nenhum. Consertados os dois
mutantes, os dois morreram. O teste estava certo nas duas vezes; o mutante é que
não mutava.

## Absorção de `fix/portao-subagente-fora-da-raiz` (2026-09-30)

Pedido do usuário, repassado pela sessão de coordenação: avaliar o ramo órfão
(`815f8a8`, 2 commits, base `7795a5a`) antes do merge deste; absorver se o
defeito ainda existir e o conserto couber junto, ou dizer por que não.

| Pergunta | Medida | Resposta |
|---|---|---|
| O defeito ainda existe em `main`? | `git log 7795a5a..main -- hooks/harness-transactional.py tests/test_transactional_hook.py` vazio; os testes do ramo contra o hook de `main` em `18ec682`: **32 failed, 110 passed** — reproduções vermelhas, guardas verdes, o mesmo desenho do RED original dele | sim |
| Cabe junto deste? | `git apply` sem conflito nos dois sentidos; o bloco dele em `_handle_post_tool` é o de toque, o deste é o de evidência; `main` + os dois consertos: **296 passed** nos vizinhos do portão | sim |
| A contagem fecha? | `test_transactional_hook.py`: 87 em `main`, 142 com o ramo, +55 medidos — 241 + 55 = 296. O relatório dele diz "86 + 56"; a diferença de 1 na divisão não foi investigada, o total 142 bate | sim |

Absorvido sem commit: o diff dele de hook e testes aplicado sobre este
worktree, e os dois documentos dele copiados de `815f8a8`
(`portao-subagente-fora-da-raiz-diagnostico.md` e `-verification.md`). O
resultado ficou byte a byte igual ao da worktree de teste que deu 296 passed.
O worktree do ramo órfão e o `docs/CONTEXT.md` modificado dele não foram
tocados; o ramo não foi apagado.

**O que a absorção traz junto, e fica declarado:** o risco nº 1 do relatório
dele — programa lançado de FORA do checkout que escreve DENTRO por caminho que
não aparece na linha (arquivo-ponteiro, variável de ambiente herdada,
`subprocess(cwd=...)`) deixa de contar. É um afrouxamento estreito e medido
(178 recusas no incidente dele, nenhuma citando o checkout), com o fechamento
nomeado: o digesto da árvore (`portao-mede-atividade-verification.md` §3,
0,225 s medidos).

## Suíte

Base avançada de `c175fd9` para `main` em `6bd2962` antes de medir (o
`escalation` aprovável, `69bbea5`, mexe no mesmo `transactional_state.py`;
reaplicação sem conflito). Medido sobre o estado integrado:

| Onde | Coletados | Resultado |
|---|---|---|
| `main` `6bd2962` (`--collect-only`) | 1613 | — |
| este ramo, integrado | 1649 = 1613 + 36 novos | **1647 passed, 1 skipped, 1 failed**, 826,9 s |
| `main` `18ec682` (`--collect-only`; entrou o `transition` recusa com portão pendente) | 1621 | — |
| este ramo sobre `18ec682` | 1657 = 1621 + 36 | **1655 passed, 1 skipped, 1 failed**, 950,4 s |
| este ramo sobre `18ec682` + `portao-subagente-fora-da-raiz` absorvido | 1712 = 1657 + 55 | **1710 passed, 1 skipped, 1 failed**, 896,8 s |
| vizinhos do portão (`test_portao_em_voo`, `test_portao_escalation`, `test_transactional_hook`, `test_ciclo_de_vida_da_task`, `test_portao_de_docs`, `test_receita_do_manual`) | 233 | 233 passed |

### Vermelho declarado — não vem deste ramo

`tests/test_deploy_drift.py::TestOQueRodaEOQueFoiPublicado::test_o_cache_reflete_o_publicado`

- **Causa:** o cache instalado (`~/.claude/plugins/cache/harness4claude/harness4claude/4.0.0`)
  diverge de `main` em `tests/test_verify_por_tabela.py`, que `fffa32e` mudou
  e ninguém reimplantou. Arquivo que este ramo não toca. Com `main` em
  `18ec682` a divergência passou a 4 arquivos — os 3 do `transition` recusa com
  portão pendente somados —, mesma causa.
- **Prova de que é anterior:** o mesmo teste, rodado no checkout de `main`
  limpo em `6bd2962`, dá `1 failed, 13 passed` com a mesma mensagem.
- **Ação:** de um checkout de `main`, `python scripts/deploy_to_cache.py --apply`
  (a própria mensagem do teste).
- **Dono:** o usuário. Implantar é consequência de merge e não foi feito aqui
  para obter verde. Decidido por ele em 2026-09-30: UM deploy só, depois do
  merge deste ramo, levando junto o `transition` de `18ec682`, antes do
  `montar` e do selo de P1 do System One.

A suíte inteira passa de 10 min e foi para segundo plano — o caso nº 2 deste
diagnóstico, ao vivo: o hook instalado gravou a linha vazia do lançamento duas
vezes e cobrou dois Stops enquanto ela rodava.

### Ramos paralelos na mesma área

O diff deste ramo (os três arquivos modificados) aplica **sem conflito** sobre
`claude/exciting-allen-825ca2` (transition recusa com portão pendente, mexe em
`transactional_state.py`) e sobre `fix/portao-subagente-fora-da-raiz`
(`815f8a8`, mexe em `_handle_post_tool`), testado com `git apply --3way` em
worktrees temporárias, já removidas. A mudança daqui no `_handle_post_tool` é
no bloco de evidência; a de lá, no bloco de toque.

## Fora do escopo, registrado

- Captura automática da evidência quando a suíte em segundo plano termina (D4):
  ramo próprio, sugerido nesta sessão.
- Job em segundo plano que escreve na árvore depois de a evidência ser gravada
  (grill #52): lacuna anterior, mesma classe que `shell-placeholder`.
- `harness4codex` tem Stop de mesmo desenho (`hook.py:464-484`, citado em
  `portao-stop-sem-codigo-diagnostico.md`); `contract/` não trata continuação,
  então nada aqui obriga paridade. Não conferido lá.
- Implantação: os hooks rodam do plugin instalado. O merge não implanta.

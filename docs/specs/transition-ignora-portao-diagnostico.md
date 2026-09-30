# `transition` ignora portão pendente — diagnóstico

Ramo `claude/exciting-allen-825ca2`, pipeline L1-bug (regex sugeriu L2-docs;
confirmação semântica corrigiu), task `t-20260930-053158958056`. Base: `main`
em `6bd2962`, que já contém o conserto do `escalation` (merge `69bbea5` do
ramo `claude/exciting-bhabha-3659cc`). O motivo de a base precisar dele está
em "Dependência", abaixo.

Origem: `docs/specs/portao-escalation-inaprovavel-diagnostico.md`, seção "Fora
do escopo", item 1.

## Reprodução

Contra o código de `c175fd9`, num banco novo, pelos métodos de
`HarnessDatabase`. Três portões, o mesmo defeito:

| Caso | Antes de `transition` | Depois de `transition` |
|---|---|---|
| `escalation` (Stop 3×) | `awaiting_gate`, fase `systematic-debugging`, `pending_gate=escalation`, `stop_continuations=2` | **passa**: `active`, fase `tdd`, `pending_gate=escalation` |
| … e o Stop seguinte | | `awaiting_gate` de novo: o contador ficou no limite |
| `branch-open` (`create_branch`) | `awaiting_gate`, `pending_gate=branch-open:b-1` | **passa**: `active`, fase `tdd`, `pending_gate=branch-open:b-1` |
| `approve-spec` (fase-portão) | `awaiting_gate`, fase `approve-spec`, `pending_gate=approve-spec` | **passa**: `active`, fase `design-doc`, `pending_gate=approve-spec` |

O terceiro caso é o mais grave e não estava no relato de origem: `transition`
**pula o portão humano** `approve-spec` (e `approve-plan`, pelo mesmo caminho).
A fase-portão existe para esperar a decisão do usuário, e basta pedir a fase
seguinte para sair dela sem decisão nenhuma. A linha de `gates` fica `pending`
para sempre, a task vai para `active` e o pipeline segue.

Reproduz sempre; não depende de hook, de balde nem de tempo.

## Causa raiz

`transition` (`scripts/transactional_state.py:970`) confere revisão (CAS),
`owner_epoch`, a ordem das fases e a obrigação de artefato da fase que sai.
**Não lê a tabela `gates`.** Depois escreve `status` a partir só da fase de
destino:

    status = "awaiting_gate" if to_phase in HUMAN_GATES else "active"

Todo portão pendente põe a task em `awaiting_gate` quando nasce
(`register_stop_continuation`, `create_branch`, `request_branch_approval`,
`open_gate`, e o próprio `transition` ao entrar numa fase-portão). `transition`
sobrescreve esse status sem perguntar por quê ele estava ali. O resultado é o
estado incoerente `status=active` com `pending_gate` preenchido.

No `escalation` isso ainda devolve a task ao portão no Stop seguinte, porque
`stop_continuations` não foi tocado e já está no limite. No `branch-open` o
ramo oferecido perde a espera. Na fase-portão, a decisão humana é pulada.

Um único ponto: `resolve_gate` e `resolve_branch_decision` já calculam o status
a partir dos portões pendentes (`still_pending`). `transition` é o escritor de
status que não calcula.

## Chamadores de `transition` (auditados antes de mudar a semântica)

| Chamador | Onde | Pode chamar com portão pendente? |
|---|---|---|
| `state_cli.py transition` | `scripts/state_cli.py:146` | Sim — é a superfície do protocolo `harness-workflow`. É onde a recusa aparece, como `erro: ...` e exit 2 |
| `test_transactional_state.py` | `:26-27` | Não: pipeline L1-feature sem portão |
| `test_transactional_state.py` | `:71` | Não: espera falha de `owner epoch`, que é conferido antes |
| `test_portao_de_docs.py::_ate_a_fase_final` | `:113` | Não: pipelines de docs não têm fase-portão |
| `test_ciclo_de_vida_da_task.py::_l1_na_fase_final` | `:506` | Não: task nova, sem portão |
| `test_portao_escalation.py::test_approve_spec_continua_avancando_a_fase` | `:151` | Não: entra na fase-portão sem outro pendente |

Nenhum hook chama `transition` (`grep` em `hooks/`). Nenhum chamador legítimo
avança fase com portão pendente: a recusa não quebra fluxo previsto.

## Regra

`transition` **recusa** enquanto a task tiver qualquer linha `pending` em
`gates`. A recusa é conferida depois do CAS e do `owner_epoch` e antes de
qualquer escrita: recusar não sobe `revision`, não grava `transitions`, não
muda fase nem status.

A mensagem segue a regra do ambiente para portões: reprovar, e trazer a linha
copiável que resolve — linha que roda de verdade e que não promete mais do que
entrega.

- Nomeia **todos** os portões pendentes.
- Imprime o comando de **um** deles: o que `pending_gate` mostra (o mais
  recente). Com mais de um, o comando de cada resolução muda a revisão, e um
  segundo comando impresso com a mesma revisão falharia. A mensagem diz para
  rodar a transição de novo depois: a próxima recusa imprime o comando do
  próximo portão, com a revisão certa.
- Pede a decisão explícita do usuário antes do comando. O comando executa a
  decisão; não a toma.

### Comando por portão

| Portão | Quem resolve | Linha impressa |
|---|---|---|
| `escalation` | `resolve_gate` (depois do conserto do bhabha) | `python "<scripts>/state_cli.py" --home "<balde>" gate --task <id> --type escalation --decision approve --expect-revision <N>` |
| `approve-spec`, `approve-plan`, na própria fase | `resolve_gate` (avança a fase) | a mesma, com `--type <portão>` |
| `branch-open:<branch_id>` | `branch_state.py` (`resolve_branch_decision`) | pergunta ao usuário; "agora não": `python "<scripts>/branch_state.py" decision --slug <slug> --decision park`; descartar: `--decision discard`; abrir: skill `branch-out` |
| qualquer outro (hoje, `answer-clarifications` aberto por `open_gate`) | ninguém | diz que nenhum comando resolve o portão, sem imprimir linha que falharia |

"Quem aceita pelo `resolve_gate`" vira um predicado só, usado pelo
`resolve_gate` e pela mensagem. Duas cópias da mesma condição é o defeito que
este repositório já pagou em `REGRA_EVIDENCIA_VALIDA`: a mensagem prometeria
comando para um portão que o resolvedor recusa.

`branch-open` não passa pelo `state_cli gate`: o `resolve_gate` o recusa de
propósito, porque aprová-lo por ali pularia `branches.approved_at`
(`test_portao_escalation.py::test_branch_open_segue_fora_do_resolve_gate`).
`branch_state.py` sem `--cwd` usa o diretório corrente, que é o do projeto
quando o modelo roda a linha; o banco não sabe o `cwd` do projeto, só o balde.

## Dependência

Antes do bhabha (`c175fd9`), `state_cli gate --type escalation --decision
approve` devolvia exit 2 com `gate is not at an advanceable phase:
escalation`. Uma recusa que imprimisse essa linha entregaria instrução
impossível de seguir, e pior: com a recusa nova, a task com `escalation`
pendente não teria saída nenhuma — nem avançar, nem aprovar. Este conserto só
vale em cima do bhabha, que torna o `escalation` aprovável. O ramo começou
por fast-forward sobre ele e, quando o `main` o mesclou (`69bbea5`), avançou
para o `main`.

## Falsificação

- **Metade 1, o defeito:** com cada um dos três portões pendentes,
  `transition` tem de recusar e não escrever nada. Os testes reprovam em
  `41b96c8` (a transição passa) e passam depois.
- **Metade 1b, a superfície do usuário:** `state_cli transition` em
  subprocesso sai com exit 2; a linha extraída da mensagem roda com exit 0; a
  mesma `transition` passa em seguida.
- **Metade 2, o que não pode mudar:** sem portão pendente, `transition` segue
  avançando (suíte existente); portão já resolvido não bloqueia; entrar numa
  fase-portão segue abrindo o portão.
- **Borda:** dois portões pendentes — a mensagem nomeia os dois e imprime um
  comando só; portão sem resolvedor não ganha linha.
- **Mutação:** cópias de `transactional_state.py` com a regra removida, com a
  mensagem sem comando e com o predicado desacoplado do `resolve_gate`, cada
  uma derrubando o teste que a cobre.

### Medido

`tests/test_transition_portao_pendente.py`, 8 testes, junto com os 6 de
`tests/test_portao_escalation.py` (o conserto do bhabha, que este toca ao
extrair o predicado). Rodados contra o código consertado e contra cópias
mutadas de `scripts/transactional_state.py` (`HARNESS_PLUGIN_ROOT` apontando
para a cópia; toda âncora de mutação exige ocorrência única no arquivo).

| Código | Resultado | Testes que caem |
|---|---|---|
| consertado | 14 passed | — |
| M0 `41b96c8` (bhabha, sem este conserto) | 8 failed | os 8 novos: a transição passa (`DID NOT RAISE`); pelo CLI, exit 0 com `phase=tdd, status=active, pending_gate=escalation` |
| M1 recusa removida | 8 failed | os mesmos 8 |
| M2 recusa sem a linha do `state_cli` | 2 failed | `cli_recusa_e_a_linha_impressa_destrava_o_escalation`, `linha_impressa_na_fase_portao_aprova_e_avanca` |
| M3 linha do `state_cli` para qualquer portão | 1 failed | `portao_sem_resolvedor_nao_ganha_linha` |
| M4 `branch-open` sem caminho próprio | 2 failed | `linha_impressa_para_branch_open_parkeia_e_destrava`, `dois_portoes_pendentes_nomeia_os_dois_e_imprime_um_comando` |
| M5 fase-portão manda repetir a transição | 1 failed | `linha_impressa_na_fase_portao_aprova_e_avanca` |
| M6 predicado aceita qualquer portão | 2 failed | `portao_sem_resolvedor_nao_ganha_linha`, `test_portao_escalation.py::test_branch_open_segue_fora_do_resolve_gate` |

Suíte inteira sobre `6bd2962` com o conserto: **1619 passed, 1 skipped, 1
failed** (`python -m pytest -q`, 15 min). A falha é
`tests/test_deploy_drift.py::TestOQueRodaEOQueFoiPublicado::test_o_cache_reflete_o_publicado`
e não vem deste ramo:

- **Causa:** o plugin instalado (`~/.claude/plugins/cache/harness4claude/.../4.0.0`)
  diverge do `main` publicado em `tests/test_verify_por_tabela.py`. O `main`
  recebeu `fffa32e` (merge `6bd2962`, ramo `claude/vibrant-montalcini-9868a6`)
  durante esta sessão, sem deploy depois. O teste compara `main` com o cache;
  este ramo não toca esse arquivo, então a falha é a mesma em qualquer checkout.
- **Ação:** num checkout de `main` (não neste worktree),
  `python scripts/deploy_to_cache.py --apply`.
- **Dono:** quem mesclou `6bd2962` — deploy é consequência do merge dele.

Os 293 testes dos arquivos que exercitam `transition` e os portões
(`test_transition_portao_pendente`, `test_portao_escalation`,
`test_transactional_state`, `test_branch_state`, `test_ciclo_de_vida_da_task`,
`test_portao_de_docs`, `test_transactional_hook`) passam.

M5 saiu de um erro achado na primeira versão verde: a recusa dizia "depois,
rode a transição de novo" também na fase-portão, onde `resolve_gate` já
avança. Seguida ao pé da letra, a instrução pedia `tdd` de novo e saía com
`next phase must be verify`. O teste passou a conferir que a mensagem da
fase-portão nomeia a fase para onde a aprovação leva e não manda repetir.

## Fora do escopo (achados de passagem)

1. **Outros escritores de `status` também não calculam a partir de `gates`.**
   Medido, cada um com um portão pendente:
   - `confirm_classification` e `reclassify` põem `status = active` (ou
     `done`) e zeram `phase_index` com `escalation` pendente
     (`active`, `pending_gate=escalation`).
   - `resolve_gate`, no caminho da fase-portão, põe `active` mesmo com outro
     portão pendente: `approve-spec` aprovado com `branch-open:b-1` aberto
     deixa `active`, fase `tdd`, `pending_gate=branch-open:b-1`. O caminho do
     `escalation` (bhabha) e `resolve_branch_decision` já calculam
     `still_pending`; este não.

   É a mesma família deste defeito — `status` é cópia denormalizada de "há
   portão pendente?", escrita por várias funções de `HarnessDatabase` que
   decidem cada uma por conta própria. `transition` deixa de ser uma delas porque passa a recusar
   com portão pendente, e sem portão pendente o status que ela escreve é o
   certo. As outras ficam para um conserto próprio: a causa comum pede o status
   derivado dos portões num lugar só, e numa reclassificação a decisão sobre o
   portão pendente (cancelar ou preservar) é de desenho.

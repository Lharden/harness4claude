# Portão `escalation` inaprovável — diagnóstico

Ramo `claude/exciting-bhabha-3659cc`, pipeline L1-bug (regex sugeriu L2-bug;
confirmação semântica corrigiu), task `t-20260929-144950366362`. Base: `main`
em `c175fd9`.

## O incidente

2026-09-29, plugin instalado 4.0.0. Task `t-20260929-130710180778`, balde
`master-harness-5a8ec6a2/sessions/6b215b3f-...-06d59ca1`, pipeline L1-bug
`[systematic-debugging, tdd, verify]`, fase `systematic-debugging`, status
`awaiting_gate`, `pending_gate = escalation`, `stop_continuations = 2`.

O usuário aprovou o portão explicitamente. O comando

    python scripts/state_cli.py --home <balde> gate --task t-20260929-130710180778 \
        --type escalation --decision approve --expect-revision 73

devolveu exit 2: `gate is not at an advanceable phase: escalation`. Não há
outro caminho de aprovação: a task fica em `awaiting_gate` para sempre.

## Reprodução

Contra o código de `main` (`c175fd9`), num banco novo:

| Passo | `status` | `pending_gate` | `stop_continuations` |
|---|---|---|---|
| `register_stop_continuation` #1 | active | — | 1 |
| `register_stop_continuation` #2 | active | — | 2 |
| `register_stop_continuation` #3 | awaiting_gate | escalation | 2 |
| `resolve_gate(..., "escalation", "approve")` | **StateTransitionError** | | |

Reproduz sempre; não depende de hook, de balde nem de tempo.

## Causa raiz

`resolve_gate` (`scripts/transactional_state.py:1012`) trata **todo portão
como fase do pipeline**. A condição da linha 1034,

    if pipeline[current_index] != gate_type or current_index + 1 >= len(pipeline):
        raise StateTransitionError(f"gate is not at an advanceable phase: {gate_type}")

só é satisfeita por `approve-spec` e `approve-plan`, os dois portões que são
fase em `contract/pipelines.json`. `escalation` não é fase de pipeline nenhum:
nasce de `register_stop_continuation` (`:1233`) quando o contador de
continuações do Stop atinge o limite, em qualquer fase.

A cronologia explica o descompasso:

- `d7fa6d8` (2026-08-28) escreveu `resolve_gate` quando os únicos portões
  resolvíveis por ele eram fases.
- `29d219b` (2026-09-01) acrescentou o `escalation` do Stop e **não** tocou
  `resolve_gate`. O portão ganhou produtor e ficou sem consumidor.

Por que nenhum teste pegou: nenhum teste chama `resolve_gate`. A sonda
`workflow.human-gates` do harness4claude (`contract/behavioral-probes.json`)
aponta para `test_branch_state.py`, que cobre só `branch-open` — o outro
portão fora de fase, que tem resolvedor próprio. `test_transactional_hook.py::
test_stop_blocks_twice_then_opens_escalation_gate` prova que o portão abre e
para aí; ninguém tentava fechá-lo.

## Padrão que já funciona

`resolve_branch_decision` (`:838`) resolve `branch-open`, o outro portão fora
de fase: marca a linha de `gates` como resolvida, **não mexe em fase**, e põe a
task em `awaiting_gate` se ainda houver outro portão pendente, `active` se não.

## Mapa dos portões humanos (`HUMAN_GATES`, `:23`)

| Portão | É fase? | Quem abre em produção | Quem resolve |
|---|---|---|---|
| `approve-spec` | sim | `transition` | `resolve_gate` (avança fase) |
| `approve-plan` | sim | `transition` | `resolve_gate` (avança fase) |
| `branch-open` | não | `create_branch` / `request_branch_approval` | `resolve_branch_decision` |
| `escalation` | não | `register_stop_continuation` | **ninguém** — o defeito |
| `answer-clarifications` | não | ninguém (`open_gate` só é chamado em teste) | ninguém |

`answer-clarifications` tem o mesmo formato de defeito, mas não tem produtor:
nenhum código de produção abre esse portão. Sem consumidor nomeado, não entra
neste conserto.

## Correção

Em `resolve_gate`, `escalation` ganha caminho próprio, no molde de
`resolve_branch_decision`:

1. a linha pendente de `gates` vira `resolved` com a decisão;
2. `stop_continuations` volta a 0 — a aprovação humana é "continue", e o Stop
   ganha de novo as duas continuações antes de escalar outra vez;
3. `status` vai para `awaiting_gate` se ainda houver outro portão pendente,
   `active` se não;
4. `phase_index` não muda e nenhuma linha entra em `transitions`, porque
   `escalation` não é fase;
5. `revision` sobe 1 (CAS, como toda escrita).

`approve-spec` e `approve-plan` seguem exatamente o caminho de hoje.
`branch-open` continua recusado por `resolve_gate`: aprová-lo por aqui pularia
`branches.approved_at`.

## Falsificação

- **Metade 1, o defeito:** teste que leva a task ao `escalation` pelo mesmo
  caminho do Stop (`register_stop_continuation` três vezes) e aprova. Tem de
  reprovar em `c175fd9` com a mensagem do incidente e passar depois.
- **Metade 1b, a superfície que o usuário usou:** o mesmo, pelo
  `state_cli.py gate` em subprocesso — exit 2 antes, exit 0 depois.
- **Metade 2, o que não pode mudar:** `approve-spec` resolvido pelo
  `resolve_gate` avança para a fase seguinte e grava a linha em `transitions`,
  antes e depois. Hoje não existe teste disso; ele entra como guarda.
- **Borda:** `escalation` aprovado com outro portão ainda pendente deixa a task
  em `awaiting_gate`.

### Medido

`tests/test_portao_escalation.py`, 6 testes, rodados contra o código
consertado e contra cinco cópias mutadas de `scripts/transactional_state.py`
(`HARNESS_PLUGIN_ROOT` apontando para a cópia; cada mutação exige âncora de
texto única no arquivo):

| Código | Resultado | Testes que caem |
|---|---|---|
| consertado | 6 passed | — |
| M0 `main` em `c175fd9` | 4 failed | os quatro de `escalation`, com `gate is not at an advanceable phase: escalation`; o do CLI com exit 2 e a mesma mensagem |
| M1 todo portão tratado como não-fase | 2 failed | `approve_spec_continua_avancando_a_fase`, `branch_open_segue_fora_do_resolve_gate` |
| M2 `stop_continuations` não zera | 3 failed | aprovar, outro portão pendente, Stop ganha de novo duas continuações |
| M3 ignora outro portão pendente | 1 failed | `escalation_aprovado_com_outro_portao_pendente_segue_aguardando` |
| M4 aprovar `escalation` grava transição | 1 failed | `aprovar_escalation_do_stop_limpa_o_portao_sem_avancar_fase` |

Os dois guardas da metade 2 passam em M0: descrevem o comportamento que já
existia e que o conserto não pode mudar.

Erro de instrumento, registrado porque quase virou conclusão: a primeira
rodada de M3 **sobreviveu**. A âncora `"awaiting_gate" if still_pending else
"active"` aparece duas vezes no arquivo, e `str.replace(..., 1)` mutou
`resolve_branch_decision` (`:874`), que vem antes. O teste estava certo; o
mutante estava na função errada. O script de mutação passou a recusar âncora
com mais de uma ocorrência.

## Fora do escopo (achados de passagem)

1. **`transition` ignora portão pendente.** Com `escalation` pendente,
   `transition(..., "tdd")` passa, põe `status = active` e deixa
   `pending_gate = escalation` — estado incoerente. No Stop seguinte,
   `stop_continuations` ainda está no limite e a task volta direto a
   `awaiting_gate`. Reproduzido no mesmo banco. Defeito separado.
2. **Stop durante medição longa em segundo plano.** No incidente, o Stop cobrou
   evidência enquanto o trabalho era medição legítima (só comandos de shell,
   registrados como `shell-placeholder`, nenhum arquivo do projeto mudou). A
   escalada saiu sem falha real de verificação. É a política de quando o Stop
   cobra, não a resolução do portão.

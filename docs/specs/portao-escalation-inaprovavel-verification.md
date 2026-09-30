# Portão `escalation` inaprovável — verificação

Task `t-20260929-144950366362`, pipeline L1-bug. Diagnóstico em
`docs/specs/portao-escalation-inaprovavel-diagnostico.md`.

## O que mudou

| Arquivo | Mudança |
|---|---|
| `scripts/transactional_state.py` | `resolve_gate` despacha `escalation` para `_resolve_escalation`, que resolve a linha de `gates`, zera `stop_continuations`, põe a task em `awaiting_gate` se sobrar portão pendente ou `active` se não, e não mexe em fase nem grava transição. O caminho dos portões que são fase só ganhou um nível de indentação (`else:`). |
| `tests/test_portao_escalation.py` | 6 testes novos: 4 do defeito, 2 guardas do que não pode mudar. |

## Pedido × evidência

| Item do pedido | Evidência |
|---|---|
| 1. Reproduzir num teste: Stop até o limite, `pending_gate = escalation`, `resolve_gate(..., "escalation", "approve")` falha | `_escalar_pelo_stop` chama `register_stop_continuation` três vezes e confere `awaiting_gate` / `escalation` / `stop_continuations = 2`. No código de `main` (`c175fd9`) os quatro testes de `escalation` caem com `gate is not at an advanceable phase: escalation`; o do CLI com exit 2 e a mesma mensagem do incidente. |
| 2. Aprovação limpa o portão: `gates` resolvido, `status` volta a `active`, `stop_continuations` zera, sem avançar fase | `test_aprovar_escalation_do_stop_limpa_o_portao_sem_avancar_fase` confere sete coisas: `status`, `pending_gate`, `stop_continuations`, `phase`, `revision + 1`, linha de `gates` = `(resolved, approve)`, e nenhuma linha nova em `transitions`. `test_cli_gate_aprova_escalation_como_o_usuario_rodou` faz o mesmo pelo `state_cli.py gate` em subprocesso e confere a projeção `state.json`. |
| 3. As duas metades: falha antes, passa depois; `approve-spec` / `approve-plan` seguem avançando | Tabela de mutantes abaixo. `test_approve_spec_continua_avancando_a_fase` passa antes e depois, e cai no mutante que trata todo portão como não-fase. `approve-plan` usa o mesmo ramo de código que `approve-spec` (a condição é `pipeline[phase_index] == gate_type`, sem distinção de nome). |
| 4. Conferir as worktrees `portao-*` antes de começar | `portao-fd-e-pin`, `portao-mede-atividade` e `portao-stop-sem-codigo` já estão em `main`. `fix/portao-subagente-fora-da-raiz` mexe só em `hooks/harness-transactional.py`. `fix/sim-nao-fecha-entrega` mexe em `state_cli._sync` e cita `escalation` num teste de `continuation_policy`, sem tocar `resolve_gate`. Nenhuma duplicação. |

## Falsificação medida

Os 6 testes rodados contra o código consertado e contra cinco cópias de
`scripts/transactional_state.py`, cada uma com UMA mutação de âncora única:

| Código | Resultado | Caem |
|---|---|---|
| consertado | 6 passed | — |
| M0 `main` (`c175fd9`) | 4 failed, 2 passed | os 4 de `escalation` |
| M1 todo portão como não-fase | 2 failed | `approve_spec_continua_avancando_a_fase`, `branch_open_segue_fora_do_resolve_gate` |
| M2 contador não zera | 3 failed | aprovar; outro portão pendente; Stop ganha de novo duas continuações |
| M3 ignora outro portão pendente | 1 failed | `escalation_aprovado_com_outro_portao_pendente_segue_aguardando` |
| M4 aprovar grava transição | 1 failed | `aprovar_escalation_do_stop_limpa_o_portao_sem_avancar_fase` |

Todo teste novo cai em pelo menos um mutante; todo mutante é morto.

## Suítes

- Arquivos que cobrem portão, Stop e ciclo de vida
  (`test_portao_escalation`, `test_transactional_state`,
  `test_transactional_hook`, `test_branch_state`, `test_ciclo_de_vida_da_task`,
  `test_portao_de_docs`): **285 passed** em 83 s.
- Suíte inteira: **1610 passed, 1 skipped, 2 failed** em 20 min 48 s. As duas
  falhas vêm de `main` e não passam pelo código mexido:

  | Teste | Causa | Ação |
  |---|---|---|
  | `test_deploy_drift.py::TestOQueRodaEOQueFoiPublicado::test_o_cache_reflete_o_publicado` | O cache instalado 4.0.0 diverge de `main` em 5 arquivos, todos do merge `c175fd9` (verify-por-tabela): o merge não foi publicado. O teste compara cache com `main`, não com este worktree. | `python scripts/deploy_to_cache.py --apply` a partir de um checkout de `main`. Decisão de deploy é do usuário. |
  | `test_verify_por_tabela.py::test_CONTROLE_codigo_antigo_adjudicava_medium_low` | O controle lê o "código antigo" de `main:scripts/workflows/wf-verify-multimodel.js`. Depois do merge `c175fd9`, `main` é o código novo, e o controle reprova por construção. | Fixar o ref no commit anterior à mudança (`d017a6b^`) em vez de `main`. Tarefa separada. |

## Fora do escopo

1. `transition` ignora portão pendente: com `escalation` aberto,
   `transition(..., "tdd")` passa e deixa `status = active` com
   `pending_gate = escalation`.
2. Stop durante medição longa em segundo plano. Reproduzido nesta própria
   sessão: a suíte inteira leva ~21 min, passa do limite de 10 min da
   ferramenta e vai para segundo plano; encerrar o turno ali contaria
   continuações do Stop sem falha de verificação nenhuma.
3. `answer-clarifications` também não é fase e não tem resolvedor, mas não tem
   produtor em código de produção.
4. A sonda `workflow.human-gates` do harness4claude em
   `contract/behavioral-probes.json` cobre só `branch-open`. O contrato é
   compartilhado com o harness4codex; mudar a sonda fica para quem mexe no
   contrato.

# Portão de Stop antes da implementação — diagnóstico

Ramo `claude/cool-lichterman-5718b9` (sessão `3830e28c`), pipeline L2-bug,
task `t-20260930-135603970891`. Base: `main` em `18ec682`.

## O incidente

Sessão `e0209356` no projeto `moneytree_farmer` (repositório sem código nenhum,
só pesquisa e planejamento). O regex sugeriu `L2-feature`; a confirmação
semântica corrigiu para `L2-architecture`. Pipeline gravado:
`discuss -> brainstorming -> graph-context -> write-spec -> grill-me ->
approve-spec -> design-doc -> validate-plan -> approve-plan -> tdd ->
verify-multimodel`.

| Hora (UTC) | Portão | Fase | `code_revision` | `evidence` |
|---|---|---|---|---|
| 05:46:25 | verification gate | discuss (1 de 11) | 60 | 0 linhas |

A mensagem pediu `--type test` com `python -m pytest -q` e a régua
`tests_passed > 0`. Não existia suíte nem código para ela medir: a única saída
"verde" seria fabricar evidência de pytest. A sessão investigou o portão e
abriu esta task (linha 587 do transcript).

Fontes, todas só leitura:

- transcript `e0209356-f874-474d-9089-568acf4e7838.jsonl`, linha 316
  (`attachment` `hook_blocking_error`, `hookName: Stop`);
- cópia do `harness.db` do balde
  `moneytree_farmer-94bf93ff/sessions/e0209356-...-be08df42`: task
  `t-20260930-053116733897`, `kind=architecture`, 0 linhas em `evidence`,
  `stop_continuations=1` (o bloqueio acima), 949 toques — todos em `docs/`
  (`CONTEXT.md`, `research/`, `specs/`). Hoje a task está em `approve-spec`
  (6 de 11), `awaiting_gate`.

## Causa raiz

`cobra_evidencia_nesta_fase` (`scripts/transactional_state.py:114-122`) devolve
`True` em toda fase quando o tipo de evidência é `test`:

```python
if tipo_de_evidencia(kind) not in EVIDENCIA_NA_FASE_FINAL:
    return True
```

O docstring diz "Codigo: em toda fase, como sempre foi". Só `docs` tem a
exceção D1 (cobrar só na última fase). As fases das pipelines de código que
vêm antes da implementação não produzem teste — produzem `CONTEXT.md`, spec,
design, e decisões humanas —, então o portão cobra algo que ali não pode
existir. É o mesmo defeito que `_motivo_do_gate` descreve para docs antes de
2026-09-25 (sessão `146dc03e`, 127 testes escritos sobre o próprio texto).

D1 não decidiu que as fases pré-implementação de código devem ser cobradas.
`portao-stop-sem-codigo-plano.md` registra "Pipelines de código não mudam" como
limite de escopo daquele ramo, e o controle `test_AC4` mede só a fase 1 de
`L2-bug`.

### Censo — a função de produção sobre o contrato

`contract/pipelines.json` inteiro, cada fase passada a
`cobra_evidencia_nesta_fase` (script `censo_portao.py` no scratchpad da
sessão, importando o módulo de produção):

| Pipeline | Tipo | Fases cobradas hoje |
|---|---|---|
| L1-feature | test | as 3, incluindo `write-spec-light` |
| L1-bug | test | as 3 |
| L1-refactor | test | as 3, incluindo `write-spec-light` |
| L1-review | test | as 2 |
| L1-docs | docs | só `verify` (D1) |
| L2-feature | test | as 11, incluindo 9 antes de `tdd` |
| L2-bug | test | as 5 |
| L2-refactor | test | as 10, incluindo 8 antes de `tdd` |
| L2-architecture | test | as 11, incluindo 9 antes de `tdd` |
| L2-review | test | as 3 |
| L2-docs | docs | só `verify-against-spec` (D1) |

53 de 58 pares (pipeline, fase) cobrados. **28** deles são fases antes da
primeira fase de implementação de uma pipeline de código:
`write-spec-light` (L1-feature, L1-refactor); `discuss`, `brainstorming`,
`graph-context`, `write-spec`, `grill-me`, `approve-spec`, `design-doc`,
`validate-plan`, `approve-plan` (L2-feature, L2-architecture; L2-refactor sem
`brainstorming`).

### Quem lê a regra

- `_handle_stop` (`hooks/harness-transactional.py:1085`) — decide bloquear.
- `register_stop_continuation` (`scripts/transactional_state.py:1388`) — recusa
  contar continuação quando a regra diz que a fase não é cobrada.

Os dois chamam a mesma função: o conserto nela vale para os dois, e hook e
banco não podem discordar.

Não leem a regra, e não mudam:

- `complete` (`:1417`) exige evidência fresca **e** fase final — a fase final
  de toda pipeline de código vem depois da implementação.
- `continuation_policy.continua` só olha a fase final (entrega sem `complete`).

A cópia instalada (`plugins/cache/harness4claude/harness4claude/4.0.0`) é
byte-idêntica a `HEAD` nos dois arquivos: o incidente rodou este código.

## A regra proposta

A fase de implementação é a que produz o teste: `tdd` nas pipelines de
feature, refactor e architecture; `systematic-debugging` nas de bug, cuja
primeira fase já parte de código existente e fecha num teste de reprodução.
Para o tipo `test`, o Stop cobra a partir da **primeira** fase de
implementação da pipeline da task, e em todas as seguintes.

- A pipeline vem da task (`pipeline_json`), que a copiou do contrato na
  classificação. Nenhuma lista de fases por pipeline é duplicada: o que o
  código conhece é o nome das fases que produzem teste.
- Pipeline de tipo `test` sem nenhuma fase de implementação (hoje `review`)
  cobra em toda fase, como antes — falha fechada, e declarada.
- Fase fora da pipeline: cobra (falha fechada).
- `docs` continua com D1, intocado.

Censo esperado depois do conserto: 25 de 58 pares cobrados — os 53 de hoje
menos os 28 acima.

## Paridade

- **harness4codex**: mesmo defeito, mais largo. `_handle_stop`
  (`harness4codex/hook.py:464-484`) bloqueia sempre que `status == active`,
  há pipeline e `verified` é falso: toda fase, todo `kind`, sem nem a exceção
  D1 de docs. Só `evidence_type='test'` liga `verified`
  (`harness4codex/state_db.py:777`, `:878-882`).
- **harness4contract**: não implementa portão de Stop; o contrato não carrega
  semântica de fase para evidência. `data/pipelines.json` tem as mesmas fases
  que `contract/pipelines.json` daqui, e que `harness4codex/_contract/`.
- O rótulo de versão diverge — `1.2.0` aqui, `1.1.0` nos outros dois — com
  pipelines idênticas. Não é deste conserto.

## Contexto estrutural (fase graph-context)

`graphify-out/graph.json` do worktree gerado em 2026-09-30 10:56 (-03), depois
do commit de `HEAD` (04:57): conferido. `graphify query "who calls
cobra_evidencia_nesta_fase"` devolve exatamente dois chamadores —
`_handle_stop` (`hooks/harness-transactional.py:1085`, aresta INFERRED por
import) e `register_stop_continuation` (`scripts/transactional_state.py:1388`,
EXTRACTED) — e um chamado, `tipo_de_evidencia`. Bate com o `Grep` da fase 1:
nenhum outro consumidor.

## Trabalho vizinho

O worktree `magical-shamir-b49924` tem mudança não commitada em
`register_stop_continuation` (parâmetro `em_voo`) e em `_handle_stop` (jobs em
segundo plano). Nenhum dos dois hunks toca `cobra_evidencia_nesta_fase`; este
conserto fica dentro dela e dos comentários do bloco de constantes, para não
sobrepor texto.

## Grill (2026-09-30)

`wf-grill`, run `wf_d1baaf65-0b1`: 5 de 5 lentes vivas, 50 perguntas, 8
bloqueantes. As respondidas pelo código:

- **Recuo de fase** [4, 5, 23]: `transition` só aceita a fase seguinte
  (`transactional_state.py:1000-1003`). O único recuo é a reclassificação
  (`confirm_classification`, `reclassify`), que volta ao índice 0 — tratado em
  D-G3.
- **Fonte da pipeline** [1, 12, 25, 26]: a da task (`pipeline_json`), lida do
  mesmo banco pelos dois chamadores; o hook recebe a task renderizada por
  `_montar`. Nenhum lê `state.json`. Fase fora da pipeline, nula, ou pipeline
  vazia com task ativa: cobra (falha fechada). `_handle_stop` já sai antes
  quando não há pipeline.
- **Critérios e censo** [2, 6, 28, 31]: seção abaixo; o censo vira teste.
- **Nomes de fase** [7, 8, 13, 17, 44]: `FASES_DE_IMPLEMENTACAO` amarrado ao
  contrato por teste — nome inexistente no contrato reprova, e pipeline de tipo
  `test` sem fase de implementação reprova até ser declarada.
- **Tasks abertas no deploy** [14, 33, 42, 45]: o contador não é mexido. A
  task do incidente chega ao `tdd` com `stop_continuations=1` e escala um Stop
  mais cedo; aprovar o `escalation` zera. Declarado.
- **Deploy** [36, 47]: fora deste ramo; o cache instalado só muda com OK do
  usuário.

Decisões do usuário:

- **D-G1.** Regra posicional: em L2-bug, `graph-context` e `grill-me` (depois
  de `systematic-debugging`) continuam cobrados. [0]
- **D-G2.** Pipeline de tipo `test` sem fase de implementação (`review`) cobra
  em toda fase, como hoje; declarado no teste de contrato. [9]
- **D-G3.** Marca d'água: task que já avançou por uma fase de implementação
  continua cobrada depois de reclassificada para o índice 0.
- **D-G4.** A marca é um evento gravado pelo AVANÇO — `transition` e
  `resolve_gate` quando saem de uma fase de implementação ou entram nela —, e
  nunca por `confirm_classification`. Motivo: `systematic-debugging` é a fase 1
  de toda pipeline de bug, e a correção semântica de um L1-bug do regex para
  L2-feature grava `systematic-debugging -> discuss` em `transitions` antes de
  qualquer trabalho; ler a marca de `transitions` traria este defeito de volta.
- **D-G5.** harness4codex: nota de paridade aqui e task separada; nada é
  editado lá deste ramo. [11, 15, 49]

## Critérios de aceite (1 AC = 1 teste, `tests/test_portao_pre_implementacao.py`)

- **AC-1 (reprodução).** Task `L2-architecture` em `discuss`, sem evidência:
  o Stop não bloqueia e `stop_continuations` fica 0. **Falha antes do
  conserto.**
- **AC-2.** Mesma task em `write-spec` e em `design-doc`, e `L1-feature` em
  `write-spec-light`: o Stop não bloqueia; `register_stop_continuation` recusa.
- **AC-3 (controle).** `L2-architecture` em `tdd` e em `verify-multimodel`, sem
  evidência: o Stop bloqueia pedindo `--type test` e conta a continuação.
- **AC-4 (controle).** Nas mesmas duas fases, a receita impressa pelo portão,
  preenchida com números válidos, grava a evidência e o Stop libera.
- **AC-5.** `L2-bug` cobra nas 5 fases (D-G1).
- **AC-6.** `L1-review` e `L2-review` cobram em toda fase (D-G2).
- **AC-7.** Task que avançou até `tdd` e foi reclassificada para `L2-feature`
  (volta a `discuss`) continua cobrada (D-G3).
- **AC-8.** Task que nasceu `L1-bug` e foi corrigida para `L2-feature` antes de
  avançar não fica marcada: `discuss` não cobra (D-G4).
- **AC-9.** `transition` para `tdd`, `transition` saindo de
  `systematic-debugging`, e `resolve_gate` de `approve-plan` (que avança para
  `tdd`) gravam a marca; `confirm_classification` não grava.
- **AC-10.** Contrato: todo nome de `FASES_DE_IMPLEMENTACAO` existe em
  `contract/pipelines.json`; pipeline de tipo `test` sem fase de implementação
  só passa se declarada; censo = 25 de 58 pares cobrados.
- **AC-11.** Fase fora da pipeline cobra.

Docs (D1) não muda: coberto por `tests/test_portao_de_docs.py`.

## Falsificação (fase tdd)

- **RED antes do conserto:** 8 de 17 reprovados — AC-1, as três de AC-2, AC-8,
  AC-9, as duas de AC-10 (censo 53 de 58). Os controles AC-3 a AC-7 passavam: o
  código antigo cobrava tudo.
- **GREEN depois:** 17 de 17; vizinhos (13 arquivos, `test_portao_de_docs`,
  `test_transactional_*`, `test_portao_escalation`, `test_ciclo_de_vida_da_task`
  e outros) 374 de 374.
- **Mutantes** (script `mutantes.py` no scratchpad, que restaura o arquivo
  sempre, um pytest por vez): os seis morreram.

| Mutante | Reprovou |
|---|---|
| M1 teste nunca cobra (`return False`) | AC-5, AC-6 ×2, censo |
| M2 ignora a marca | AC-7 |
| M3 nunca grava a marca | AC-7, AC-9 |
| M4 pipeline sem implementação nunca cobra | AC-6 ×2, censo |
| M5 grava a marca em toda transição | AC-2 ×2, AC-9 |
| M6 fase fora da pipeline libera | AC-11 |

M1 não derruba AC-3 nem AC-4, e isso é redundância, não buraco: a task chega a
`tdd` por `transition`, que grava a marca, então o `tdd` fica coberto pelos dois
mecanismos. A metade posicional sozinha é medida pelo censo e por AC-5.

## Perguntas para o grill (entrada original)

1. **Pipeline de código sem fase de implementação** (`L1-review`,
   `L2-review`): cobrar em toda fase (como hoje), em nenhuma, ou só na final?
2. **Código escrito antes da primeira fase de implementação.** Três caminhos
   põem uma task numa fase pré-implementação com código já alterado:
   a promoção L0 -> L1-feature por `harness-reclassify.sh` (3+ arquivos,
   pousa em `write-spec-light`); a confirmação semântica ou humana que troca a
   pipeline e volta ao índice 0 (`confirm_classification`); e o modelo que
   escreve código fora de ordem. Pela regra posicional o Stop não cobra nessa
   janela; `complete` continua exigindo evidência na fase final.

# Portão de Stop sem código — diagnóstico

Ramo `portao-de-stop-sem-codigo` (sessão `bd081494`), pipeline L2-bug,
task `t-20260925-022237730816`. Base: `origin/main` em `27c0002`.

## O incidente

Sessão `146dc03e` (cwd `Documents/mestrado/pdtd`, sem `.git`), montando a
apresentação PPEGPS. O regex sugeriu `L2-feature`; a confirmação semântica
corrigiu para `L2-docs` às 01:40:15 UTC, **antes** do primeiro bloqueio. O
pipeline gravado foi `source-selection -> graph-context -> documentation ->
verify-against-spec`.

| Hora (UTC) | Portão | Fase | `code_revision` | `evidence` |
|---|---|---|---|---|
| 01:42:02 | verification gate | source-selection (1 de 4) | 9 | 0 linhas |
| 01:58:10 | verification gate | source-selection (1 de 4) | 51 | 0 linhas |
| 01:59:43 | escalation gate | source-selection (1 de 4) | 54 | 0 linhas |

Depois do escalonamento a sessão **escreveu uma suíte de 127 testes sobre o
próprio texto** (`build/tests/test_entrega.py`) e gravou evidência na
`code_revision` 73. A revisão chegou a 92 no fim, e `verified` voltou a
`False`. Fonte: cópia só-leitura do `harness.db` do balde
`pdtd-68ed66d8/sessions/146dc03e-...-2c180605`.

## Pergunta 1 — o portão deve exigir pytest num pipeline de docs?

**Não. E hoje ele não tem como exigir outra coisa.**

Causa raiz: o harness tem **uma** definição de "evidência fresca", e ela é
teste. `REGRA_TESTE_VALIDO` (`scripts/transactional_state.py:48`) é a única
regra que liga `verified`, em `record_evidence` (`:1145`). Os três
consumidores de `verified` leem só a coluna, sem olhar o tipo do pipeline:

- o portão de Stop, `_handle_stop` (`hooks/harness-transactional.py:1018`);
- o fechamento, `complete` (`scripts/transactional_state.py:1204`), que
  ainda reconfere com `_has_fresh_test_evidence`;
- a detecção de entrega, `continua` (`scripts/continuation_policy.py:73`).

O contrato diz outra coisa sobre docs:

- `contract/pipelines.json:9,15` — os dois pipelines de docs **não têm
  fase `tdd`**. Nenhuma fase deles produz teste.
- `skills/documentation/SKILL.md` §Verificação — o que o passo final de docs
  confirma são quatro itens: toda afirmação da tabela de fontes aparece na doc
  ou foi descartada com motivo; todo comando de exemplo roda; nenhum
  `[NEEDS CLARIFICATION]` sobrou; nenhum caminho citado sumiu. Nenhum é pytest.
- `skills/verify-against-spec/SKILL.md:23` pressupõe "testes passando"; é
  escrita para código, e o pipeline L2-docs a reutiliza sem adaptação.

Consequência medida: um pipeline de docs **não pode ser concluído
honestamente**. `complete` recusa sem teste; o Stop bloqueia duas vezes e
escala; a saída que sobra é fabricar teste — e foi o que a sessão fez.

"Numa pasta sem suíte" não é a causa, é o agravante. Em pipeline de código a
fase `tdd` cria a suíte; o de docs nunca cria. Dentro de um repositório com
suíte o portão seria satisfeito — por um pytest que verifica o código, não as
afirmações da doc. Destravar não é verificar.

O harness4codex, que compartilha o contrato, tem o mesmo desenho
(`harness4codex/state_db.py:776-805`, `:819-824`, `:873-889`, `hook.py:464-484`):
só `evidence_type='test'` liga `verified`. Não há regra de docs em nenhum dos
três repositórios irmãos.

## Pergunta 2 — por que comando de leitura sobe o `code_revision`?

Os 59 toques `shell-placeholder` da task, casados um a um com o comando do
transcript (principal + 8 subagentes) e reavaliados com as funções de
**produção** do hook (`scratchpad/mapear_toques.py`,
`classificar_toques.py`):

| Classe | Toques | Por que não isentou |
|---|---|---|
| Interpretador (`python -c`, `python - <<EOF`) | 30 | Por desenho: o hook não sabe se um programa escreve |
| Escrita real (`cat > plano.md`, `mkdir && mv && rm`) | 2 | Correto |
| **Leitura pura** | **27** | Vocabulário e um defeito de parse, abaixo |

As 27 leituras puras, por causa (um comando pode ter mais de uma):

1. **`cd` fora do vocabulário** — 20 comandos. `cd X && grep ...` é a forma
   mais comum de leitura, e `cd` não está em `_SOMENTE_LEITURA`
   (`hooks/harness-transactional.py:391`). `cd` não escreve arquivo nenhum.
2. **Destino de redirecionamento vira comando** — 10 comandos. `_segmentos`
   (`:444`) parte o segmento em `>`, então em `ls x 2>/dev/null | grep y` o
   `/dev/null` vira cabeça de segmento, e `null` não é binário de leitura.
   `shell_write_targets` já reconhecia `/dev/null` como destino nulo; o
   `_segmentos` desfazia isso.
3. **`find` fora do vocabulário** — 7 comandos, nenhum com `-delete`/`-exec`.
4. `awk` (2) e laço `for/do/done` (1) — deixados como estão: `awk` escreve
   por `print >` e `system()`, e um caso de laço não paga a gramática.

A hipótese do enunciado — "isenção só para comando sem composição" — vale
para `is_state_management` e `is_trusted_verification`, **não** para
`is_read_only`, que aceita pipe e `&&` desde que cada segmento comece por
binário de leitura (`test_is_read_only_reconhece_inspecao`). O que barrava as
27 era o vocabulário, não a composição.

**Editar o próprio documento deve contar?** Deve — e o erro não está aí. A
doc é o produto do pipeline de docs; editá-la depois de verificada invalida a
verificação, como editar código invalida o teste. `code_revision` é, na
prática, "revisão do produto sob verificação". O defeito é que a única
evidência que ela sabe invalidar e renovar é pytest. Com evidência de docs
(pergunta 1), a mesma edição passa a invalidar a coisa certa.

## Pergunta 3 — classificação pelo regex

Fora deste conserto. A confirmação semântica já tinha corrigido para
`L2-docs` antes do primeiro bloqueio; com o regex perfeito o portão teria
bloqueado igual. Não há conserto trivial que mude este incidente.

## Insumo lido e não adotado: branch `claude/sleepy-tesla-19fpxf`

A sessão na nuvem (`f0c2551`) solta o Stop quando a sessão e os arquivos estão
fora de qualquer repositório. Não adotado, porque o critério é um proxy da
causa:

- deixa o bloqueio para docs **dentro** de um repositório;
- solta pipeline de **código** fora de um repositório (script numa pasta sem
  `.git`), onde a fase `tdd` cria a suíte e o teste é devido.

## Contexto estrutural (fase graph-context)

`graphify-out/graph.json` do repositório principal é de 2026-07-28, anterior a
`HEAD` — não serve como estrutura conferida. Consumidores levantados por
`Grep` sobre `hooks/`, `scripts/` e `tools/`:

- `_segmentos`, `_SOMENTE_LEITURA`, `is_read_only`, `nao_muda_a_arvore`: só em
  `hooks/harness-transactional.py`; ponto de uso único em `_handle_post_tool`
  (`:991`).
- `verified`: decidido só em `transactional_state.record_evidence` e
  `touch_files`; lido em `_handle_stop`, `complete`,
  `continuation_policy.continua` e projetado (sem decidir) por
  `harness-classify.sh`, `harness-reclassify.sh`, `branch_state.py`,
  `confirm_classification.py`, `expire_stale_pipeline.py`.
- `kind` da task está no banco (`tasks.kind`) e chega a `_handle_stop` pelo
  `task` renderizado (`transactional_state.py:1288`).

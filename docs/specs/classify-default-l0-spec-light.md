# Spec-light — default L0 no caminho não reconhecido

**Task:** t-20260904-131317492889 · **Classificação:** L1-refactor
**Data:** 2026-09-04

## Objetivo

Trocar o default de `classify_prompt` para `L0-question` quando **nenhum**
padrão casa, em vez de cair em `L1`.

## Por que agora

O caminho não é borda: medido sobre 1.195 pares reais colhidos de 357
transcripts (`scripts/harvest_classify_labels.py`), **673 pares — 56% do
corpus — não casam padrão nenhum**.

| default | acerta | erra |
|---|---|---|
| `L1` (anterior) | 229 / 673 = **0.340** | 444 pipelines abertos em vazio |
| `L0` (novo) | 436 / 656 = **0.665** | 220 pipelines perdidos |

## Requisitos

- **REQ-1** — Prompt sem nenhum marcador L0/L1/L2/docs/review devolve
  `("L0", "question")`.
- **REQ-2** — O rótulo emitido existe em `scripts/pipelines.json`. `L0-question`
  é o único L0 na tabela; `L0-feature` seria recusado por
  `confirm_classification.py`.
- **REQ-3** — `docs` e `review` continuam em `L1`. Não estão em `L1_PATTERNS`,
  então chegam ao mesmo caminho de "nada casou"; forçá-los a L0 tornaria
  `L1-docs` e `L1-review` inalcançáveis.
- **REQ-4** — Os caminhos que já decidiam continuam idênticos: L1 por palavra,
  L2 por palavra, L0 por pergunta explícita.

## Acceptance criteria

- **AC-1** — *Given* `"Sim!"`, *when* classificado, *then* `("L0", "question")`.
- **AC-2** — *Given* qualquer prompt sem marcador, *when* classificado, *then*
  `f"{level}-{kind}"` pertence à tabela de pipelines.
- **AC-3** — *Given* `"revisa o codigo que acabei de escrever"`, *when*
  classificado, *then* nível `L1` e kind `review`.
- **AC-4** — *Given* `"documenta o modulo de autenticacao no readme"`, *when*
  classificado, *then* nível `L1` e kind `docs`.
- **AC-5** — *Given* `"conserta o bug do login que quebrou"`, *when*
  classificado, *then* `("L1", "bug")`.
- **AC-6** — *Given* `"o que e um hook de UserPromptSubmit?"`, *when*
  classificado, *then* nível `L0`.

## Boundaries

- **ALWAYS** — devolver um rótulo que existe em `pipelines.json`.
- **NEVER** — deixar `L1-docs` ou `L1-review` sem prompt que os alcance.
- **NEVER** — alterar a ordem de decisão do `kind`; `docs` antes de `review`,
  `review` antes de `bug`.
- **ASK** — reverter o default exige decisão do usuário: é uma linha, mas o
  custo é assimétrico (ver abaixo).

## Custo declarado

Nos 220 casos em que havia trabalho real, o hook não emite `CLASSIFIED`, a skill
`harness-workflow` não é convidada, e a perda do pipeline é **silenciosa**. O
erro anterior era visível e desfazível por `confirm_classification.py --final`.

Trade-off aceito por decisão explícita do usuário em 2026-09-04, com os dois
números na mesa.

## Verificação

`tests/test_classify_prompt.py` — 19 casos; 7 falhavam antes da mudança,
exatamente os do default. Suíte completa: 1186 passed, exit 0.
Tabelas em `~/.claude/harness/calib/classify-guard-2026-09-04.*`.

---
name: write-spec-light
description: "Gera uma spec enxuta (~50 linhas) para pipelines L1 no Harness v3, capturando objetivo, requisitos, acceptance criteria Given/When/Then e boundaries mínimas. Mantém [NEEDS CLARIFICATION] e overhead humano ~2 minutos, sem o peso da write-spec completa."
category: workflow
risk: low
source: custom
date_added: "2026-04-10"
metadata:
  version: 1
  triggers: write-spec-light, spec light, spec enxuta, L1 spec
---

# Write Spec Light — Spec Enxuta para L1

## Quando ativar

Ative esta skill quando o harness-workflow estiver conduzindo um pipeline **L1** (tipicamente `L1-feature`) e precisar de uma especificação leve antes de implementar. O foco é velocidade: o usuário deve conseguir revisar a spec em menos de 2 minutos.

- Ativar em: pipelines `L1`, `L1-feature`, tasks de complexidade moderada que ainda se beneficiam de alinhamento formal
- **NÃO ativar em L2**: use a `write-spec` completa, que inclui user stories, edge cases detalhados e seções extras
- **NÃO ativar em L0**: tasks triviais pulam a fase de spec e vão direto para execução

Se o harness classificou o prompt como L2 ou maior, delegue para `write-spec` (versão completa). Se classificou como L0, nem spec é necessária.

## Objetivo

Produzir uma spec light de **~50 linhas** revisável em 2 minutos, contendo apenas o essencial:

1. **Objetivo** em 1–3 frases: o quê, para quem, por quê
2. **Requisitos** em 2–5 bullets (REQ-001…REQ-005), verificáveis e atômicos
3. **Acceptance Criteria** em 2–5 itens no formato Given/When/Then (AC-001…AC-005)
4. **[NEEDS CLARIFICATION]** sempre que houver ambiguidade — nunca assuma silenciosamente
5. **Boundaries mínimas**: in-scope (1–3 bullets) e out-of-scope (1–3 bullets)

A spec light deve ser enxuta por design. Se algo precisa de mais detalhe, é sinal para escalar para `write-spec` completa.

## Workflow

1. **Ler contexto mínimo** — carregue `docs/CONTEXT.md` (se existir) e o prompt do usuário. NÃO explore o codebase além do estritamente necessário; a spec light assume que o contexto L1 cabe em poucos arquivos.
2. **Gerar spec light** — escreva objetivo, requisitos, ACs, clarifications e boundaries seguindo o template acima. Alvo: ~50 linhas, máximo 80.
3. **Apresentar rapidamente** — mostre a spec ao usuário em um único bloco. Pergunte apenas sobre os `[NEEDS CLARIFICATION]` marcados; não abra discussões paralelas.
4. **Salvar** em `docs/specs/{feature-slug}-spec-light.md` usando slug kebab-case derivado do objetivo.
5. **Retornar ao harness-workflow** — sinalize conclusão do passo de spec e deixe o orquestrador avançar para o próximo estágio do pipeline L1 (tipicamente write-plan ou tdd direto).

## Princípios

- **Enxuta por design**: se você precisar de mais de 80 linhas, está usando a skill errada — escale para `write-spec`.
- **Review humano < 2 minutos**: cada seção deve caber em uma tela; sem subseções profundas, sem prosa longa.
- **Clarifications obrigatórias**: qualquer ambiguidade vira `[NEEDS CLARIFICATION]` explícito. Nunca chute, nunca omita.
- **ENTREGA cabe em uma linha**: mesmo no L1, a spec fixa o teto da afirmação — o nível mais baixo que ainda é verdade, e o que não está incluído. No L2 isso é seção própria (`write-spec`); aqui é uma linha dentro de Boundaries, porque o custo humano de dois minutos é o limite.
- **AC ainda Given/When/Then**: mesmo na versão light, acceptance criteria mantêm o formato testável. É o que permite a fase TDD funcionar depois.

## Diferenças vs write-spec completa

| Aspecto              | write-spec-light (L1)       | write-spec (L2)                       |
|----------------------|-----------------------------|---------------------------------------|
| Tamanho alvo         | ~50 linhas (máx 80)         | 200–400 linhas                        |
| Tempo de review      | < 2 minutos                 | 10–20 minutos                         |
| User stories         | Não (objetivo direto)       | Sim, com personas                     |
| Requisitos           | 2–5 bullets REQ-00x         | 10+ requisitos funcionais e não-func. |
| Acceptance Criteria  | 2–5 ACs Given/When/Then     | Matriz completa + edge cases          |
| Edge cases           | Não enumerados              | Seção dedicada                        |
| Boundaries           | In/out mínimo               | Scope detalhado + dependências        |
| [NEEDS CLARIFICATION]| Obrigatório                 | Obrigatório                           |
| Pipeline             | L1, L1-feature              | L2, L2-feature, L2-refactor           |

## Integração com pipeline

No Harness v3, a skill write-spec-light encaixa no pipeline **L1-feature** assim:

```
classify (L1) → write-spec-light → write-plan → tdd → verify-against-spec → done
```

O harness-workflow lê `state.json`, detecta o passo atual `write-spec-light`, invoca esta skill, persiste o artefato em `docs/specs/{slug}-spec-light.md` e marca o passo como concluído antes de seguir para `write-plan`. Se em qualquer momento a task se mostrar mais complexa do que L1, o harness pode promovê-la para L2 e re-rodar com `write-spec` completa.

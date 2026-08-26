---
name: write-spec
description: "Gera specs formais completas com user stories priorizadas (P1/P2/P3), acceptance criteria no formato Given/When/Then, marcadores [NEEDS CLARIFICATION] para ambiguidades e boundaries ALWAYS/NEVER/ASK. Use esta skill em L2 pipelines do Harness v3 quando o usuário pedir spec, user stories ou acceptance criteria, ou logo após brainstorming/discuss para formalizar requisitos antes de design-doc e TDD."
category: workflow
risk: low
source: custom
date_added: "2026-04-10"
metadata:
  version: 1
  triggers: write-spec, spec, user stories, acceptance criteria, formal spec
---

# Write Spec — Geração de Spec Formal Assistida

Skill responsável por transformar ideias, brainstorms ou pedidos informais em
specs formais completas, prontas para alimentar as fases seguintes do pipeline
L2 do Harness v3 (design-doc → TDD → implementação).

## Quando ativar

Invoque esta skill quando:

- Estiver em um pipeline **L2-feature** do Harness v3 e for hora de formalizar
  requisitos antes do `design-doc`.
- O usuário pedir explicitamente uma **spec**, **user stories**, **acceptance
  criteria** ou termos equivalentes ("escreva uma spec", "bora fechar os
  requisitos").
- Logo após `brainstorming` ou `discuss`, quando já existe contexto suficiente
  mas ainda falta estrutura formal.
- Para features novas que precisam de critérios de aceitação testáveis antes de
  começar a codar.

**Não ativar** em:

- Tarefas **L0** (execução direta, sem pipeline).
- Bugs ou fixes pequenos **L1** que dispensam spec formal.
- Perguntas/exploração sem intenção de implementar.

## Objetivo

1. **User stories priorizadas** em três níveis: **P1** (MVP, indispensável),
   **P2** (importante, desejável no primeiro release) e **P3** (nice-to-have,
   pode ficar para depois).
2. **Acceptance criteria** no formato **Given/When/Then** — cada user story
   deve ter pelo menos um cenário testável que possa virar teste automatizado.
3. **[NEEDS CLARIFICATION]** como marcador literal toda vez que houver
   ambiguidade ou decisão pendente que o usuário precisa resolver.
4. **Boundaries** explícitos no formato **ALWAYS** (sempre faz), **NEVER**
   (jamais faz) e **ASK** (pergunta antes de fazer) — delimitam o escopo da
   feature com clareza.
5. **Nível de garantia** declarado: o que a feature pronta poderá ser
   **afirmada** como fazendo, e o que alguém razoavelmente assumiria incluído e
   não está.
6. **Suposições registradas**: toda `[NEEDS CLARIFICATION]` resolvida vira uma
   `ASSUMPTION-nnn` na seção `## Suposições`, amarrada aos requisitos que ela
   sustenta. O requisito guarda **o que** fazer; a suposição guarda **por que
   ele está correto**.
7. **Artefato reutilizável**: a spec serve de insumo direto para `design-doc`
   (arquitetura) e `tdd` (testes), fechando o ciclo Spec-Driven Development.

## Workflow

1. **Ler contexto upstream**: revise brainstorm, discuss, mensagens anteriores
   do usuário, issues relacionadas e código existente quando houver.
2. **Draft inicial**: produza um rascunho completo da spec seguindo o template,
   preenchendo o que for possível a partir do contexto. Marque lacunas com
   `[NEEDS CLARIFICATION]` em vez de inventar requisitos.
3. **Apresentar ao usuário**: mostre o draft e liste explicitamente todos os
   pontos `[NEEDS CLARIFICATION]` encontrados, agrupados por seção.
4. **Resolver ambiguidades**: itere com o usuário até que todos os
   `[NEEDS CLARIFICATION]` sejam respondidos ou deliberadamente adiados
   (marcar como "ASK later" na seção boundaries).
4b. **Coagular cada resposta em `ASSUMPTION-nnn`**: a resposta do usuário não
   pode se dissolver no texto do requisito. Para cada `CLARIF-n` respondida,
   escreva uma linha em `## Suposições` com a condição assumida, a data, quem
   decidiu (`usuário` ou `inferência`) e a lista dos REQ/AC que ela sustenta. A
   `CLARIF-n` original fica riscada, não some.

   Também vira `ASSUMPTION-nnn` toda condição que você assumiu **sem perguntar**
   para conseguir escrever um requisito — marcada `por inferência`. Essas são as
   perigosas: ninguém as confirmou e elas parecem requisito.
5. **Salvar**: grave o arquivo final em `docs/specs/{feature-slug}-spec.md`
   (crie o diretório se não existir).
6. **Retornar path**: devolva o caminho absoluto do arquivo salvo para que o
   próximo passo do pipeline (design-doc) possa consumi-lo.

## Template

Use o template em `templates/spec-template.md` (adjacente a este SKILL.md) como
base estrutural. Ele já contém os headers obrigatórios, placeholders e exemplos
de formatação para user stories, acceptance criteria e boundaries.

## Princípios

- **AI gera 90%+ do conteúdo**: o humano revisa, corrige e aprova; não deve
  precisar escrever a spec do zero.
- **Nunca inventar requisitos**: se não estiver claro, use
  `[NEEDS CLARIFICATION]`. Especulação silenciosa é o pior inimigo de uma spec.
- **User stories MVP-testáveis**: cada P1 deve ser demonstrável isoladamente —
  se você não consegue imaginar o teste, a user story está mal escrita.
- **AC alimentam TDD**: cada cenário Given/When/Then deve ser implementável
  como teste automatizado (pytest) na fase seguinte.
- **Priorização honesta**: resista à tentação de marcar tudo como P1. Se tudo é
  prioritário, nada é.
- **Boundaries explícitos**: ALWAYS/NEVER/ASK evitam scope creep e deixam claro
  o que está fora do escopo desta feature.
- **Exemplos concretos**: um bom `[NEEDS CLARIFICATION: qual o formato de
  autenticação — JWT, session cookie ou API key?]` é infinitamente melhor que
  um genérico `[NEEDS CLARIFICATION: auth]`.
- **Teto da afirmação se fixa antes do código**: a spec diz o nível mais baixo
  que ainda é verdade sobre a entrega, e o que ele não cobre. Sem isso o limite
  só aparece no docstring de quem implementou — tarde, e apenas para quem abre o
  arquivo. Mecanismo conferido no `Provenance Guarantee Level` do PRD do
  open-science (2026-08-19, `wiki/sources/open-science.md`), onde a própria spec
  rebaixa a palavra "proveniência" para "auditoria e rastreabilidade" e instrui
  produto e revisor a descrever a versão assim.
- **Resposta do usuário é decisão, e decisão não evapora**: a spec registra o
  requisito e esquece a condição que o tornou correto. Quando essa condição muda
  meses depois, o requisito continua lá, com cara de válido, e ninguém sabe
  quais outros dependiam da mesma premissa. `ASSUMPTION-nnn` com `justifica:`
  é o que transforma "a suposição caiu" numa lista de requisitos afetados em vez
  de uma releitura da spec inteira. Mesma família do buraco `human_approvals` em
  `wiki/decisions/estado-duravel-do-pipeline.md` — aprovação humana que o estado
  descarta assim que é dada. Absorvido de @0xWast3 (2026-08-26): a auditoria
  checa contra a **suposição**, não contra o objetivo.
- **Vocabulário da spec não é herdado do código**: quando a feature mexe em algo
  que já existe, o nome que a entidade tem hoje é uma resposta anterior ao
  problema, não um requisito dele. Nomeie o que a coisa **é** no domínio; se o
  termo do código diverge, isso é achado a registrar, não conflito a resolver
  copiando o código. Restrição externa — contrato de API, schema em produção,
  decisão Locked no `CONTEXT.md` — é o oposto: preserve literalmente.

## Saída

O artefato final é salvo em:

```
docs/specs/{feature-slug}-spec.md
```

Onde `{feature-slug}` é um identificador kebab-case derivado do nome da
feature (ex: `user-auth-oauth`, `payment-retry-logic`, `harness4claude`).

Se o repositório não tiver o diretório `docs/specs/`, crie-o. Se a spec já
existir, pergunte ao usuário se deve sobrescrever ou versionar
(`...-spec-v2.md`).

## Integração com pipeline

Pipeline típico **L2-feature** do Harness v3:

```
brainstorming → discuss → write-spec → design-doc → tdd → implementation → verification → commit
```

- **Upstream** (`brainstorming`, `discuss`): fornecem contexto e exploração.
- **write-spec** (esta skill): formaliza os requisitos como spec.
- **Downstream** (`design-doc`): consome a spec para projetar a arquitetura.
- **TDD**: usa os acceptance criteria (Given/When/Then) como base para os
  testes que guiam a implementação.

A spec é o **contrato** entre a fase de exploração e a fase de execução — uma
vez aprovada, ela vira a fonte da verdade para o que a feature deve entregar.

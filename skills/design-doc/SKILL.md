---
name: design-doc
description: "Gera design técnico separado a partir de uma spec aprovada, cobrindo arquitetura, data model, API contracts, test strategy e risks. Usar em pipelines L2 do Harness v3 entre grill-me e validate-plan, separando o 'como' (design) do 'o que' (spec)."
category: workflow
risk: low
source: custom
date_added: "2026-04-10"
metadata:
  version: 1
  triggers: design-doc, design, technical design, architecture design, data model, api contracts
---

# Design Doc — Design Técnico Separado

Transforma uma **spec** aprovada (sem ambiguidades pendentes) em um documento de **design** técnico separado. O objetivo é dividir explicitamente o "o que" (spec funcional) do "como" (design técnico), permitindo que decisões de arquitetura, data model e API sejam revisadas sem poluir requisitos.

## Quando ativar

- Em pipelines **L2** do Harness v3, após `grill-me` e antes de `validate-plan`.
- Quando existe uma **spec** aprovada em `docs/specs/{feature-slug}-spec.md` pronta para ser traduzida em design técnico.
- Quando o usuário pede explicitamente um "design doc", "technical design", "architecture design", "data model" ou "API contracts".

**NÃO** ativar nestes casos:

- Se a spec ainda contém marcadores `[NEEDS CLARIFICATION]` pendentes — retornar para `write-spec` e resolver ambiguidades primeiro.
- Em pipelines **L0** ou **L1** — nesses níveis o design inline (dentro da spec light ou direto no plano) é suficiente, e criar um design doc separado é overhead desnecessário.
- Quando não existe spec formal — primeiro criar a spec via `write-spec`.

## Objetivo

1. **Separar "como" do "o que"**: a spec descreve requisitos e user stories; o design descreve estrutura técnica. Nunca misturar.
2. **Cobrir arquitetura**: componentes principais, responsabilidades, diagramas ASCII, dependências entre módulos.
3. **Definir data model**: entidades, campos, relações, invariantes, estados possíveis, validações no nível do domínio.
4. **Definir API contracts**: endpoints, assinaturas de funções públicas, schemas de request/response, códigos de erro.
5. **Definir test strategy**: que camadas testar (unit, integration, e2e), quais edge cases cobrir, critérios de completude.
6. **Listar risks**: riscos técnicos conhecidos, dívidas esperadas, pontos frágeis, mitigações propostas.
7. **Traçar para spec**: cada componente, entidade e contrato deve conter referência explícita via `[traces: REQ-X, US-Y]` apontando para requisitos e user stories da spec.

## Workflow

1. **Ler a spec aprovada** em `docs/specs/{feature-slug}-spec.md`.
   - Validar que **não** existem marcadores `[NEEDS CLARIFICATION]`. Se houver, abortar e retornar para `write-spec`.
   - Extrair lista de `REQ-F*` (requisitos funcionais), `REQ-NF*` (não-funcionais) e `US-*` (user stories) para uso em traceability.

2. **Ler contexto técnico do projeto** (Technical Context).
   - Inspecionar `docs/CONTEXT.md`, arquivos de configuração (`pyproject.toml`, `package.json`, etc.), stack declarada, convenções existentes, testes presentes.
   - Se o repositório não tem contexto claro, perguntar ao usuário antes de inventar suposições (marcar como **Open questions** no design).

3. **Gerar o design** usando `templates/design-template.md` como base.
   - Preencher seções: Technical Context, Architecture, Data Model, API Contracts, Test Strategy, Risks, Open Questions, Phases.
   - Phases devem refletir **user stories** priorizadas na spec (P1 primeiro, depois P2, etc.).

4. **Aplicar traceability obrigatória** em cada item técnico.
   - Formato: `[traces: REQ-F1, US-1]`.
   - Exemplo de componente com trace:
     ```
     ### Componente: AuthTokenStore  [traces: REQ-F1, REQ-NF2, US-1]
     Responsável por armazenar e invalidar tokens JWT.
     ```
   - Nenhum componente, entidade ou contrato deve ficar sem referência à spec.

5. **Declarar `applies_to`** — o front matter que diz quais caminhos este design governa.
   - Formato: lista YAML de globs relativos à raiz do projeto, com `/` mesmo no Windows.
     `*` fica dentro de um segmento; `**` atravessa diretórios.

     ```yaml
     ---
     applies_to:
       - src/auth/**
       - src/middleware/session.py
     ---
     ```

   - Os padrões identificam o código **governado** pelo documento, não todo arquivo que
     por acaso menciona o assunto. Largos o bastante para sobreviver a um refactor comum,
     estreitos o bastante para que casar signifique que o doc é mesmo relevante.
   - No máximo ~10 padrões. Passando disso, consolidar em padrões mais amplos, ainda que
     menos precisos — uma lista longa envelhece a cada movimentação de arquivo.
   - Dois designs podem governar o mesmo caminho. Sobreposição é esperada onde as
     preocupações se cruzam, e nesse caso ambos se aplicam.
   - Conferir com `python tools/design_scope.py <caminho> --explain` antes de salvar: se um
     arquivo que deveria ser governado não aparece, o glob está errado; se meio repositório
     aparece, está largo demais.

6. **Salvar e atualizar state**.
   - Gravar em `docs/specs/{feature-slug}-design.md`.
   - Atualizar `~/.claude/harness/state.json` adicionando o path ao array `artifacts_so_far` e avançando `current_step` para o próximo pipeline step (`validate-plan`).

## Template

O template canônico está em `templates/design-template.md` dentro desta skill. Ele define a ordem exata de seções, placeholders e blocos de exemplo. Sempre começar a partir dele — nunca inventar layout ad-hoc.

## Princípios

1. **Design ≠ Plan**. Design descreve a **estrutura técnica**; plan descreve **passos de execução** (TDD, ordem de arquivos, checkpoints). Nunca misturar: plano vai para `writing-plans`, design fica aqui.
2. **Traceability obrigatória**. Todo componente, entidade, endpoint e cenário de teste deve conter `[traces: REQ-*, US-*]`. Sem trace = sem justificativa = remover.
3. **ASCII diagrams OK**. Diagramas simples em ASCII são preferidos a ferramentas externas. Caixas, setas e camadas bastam para comunicar intenção.
4. **Phases refletem user stories**. Organizar phases por ordem de prioridade das user stories (P1 → P2 → P3), não por ordem de implementação técnica. Isso garante entrega incremental de valor.
5. **Risks honestos**. Listar riscos reais, não placeholders. Se não há risco claro, escrever "nenhum identificado" e seguir — melhor do que inventar.
6. **Open questions vão para usuário**. Dúvidas técnicas que o agent não pode resolver sozinho (ex: escolha entre dois bancos de dados) devem ir explicitamente para a seção **Open Questions**, não ser resolvidas por chute.

7. **O design é norma; o código é evidência**. Divergência entre os dois é uma contradição a resolver, não um fato a registrar — e resolver significa decidir qual dos dois está defeituoso. Refactor que não pretende mudar comportamento deve **preservar** o design. Nunca editar o documento só para racionalizar comportamento que o código adquiriu sem decisão.

8. **`applies_to` mantém o design vivo depois do pipeline**. Sem ele o documento é lido uma vez, no `verify-against-spec`, e vira órfão: a próxima alteração no mesmo código não sabe que ele existe. Com ele, `python tools/design_scope.py src/auth/token.py` responde *qual norma governa este arquivo* — a direção inversa do `[traces:]`, e a única que serve para quem está prestes a editar. Atualizar os padrões quando a propriedade se move, área nova passa a ser governada, ou área velha deixa de ser.

## Saída

O arquivo gerado fica em:

```
docs/specs/{feature-slug}-design.md
```

Onde `{feature-slug}` é o mesmo slug usado pela spec correspondente (ex: `user-auth-jwt`). O path é registrado em `state.json` como artefato do pipeline L2 atual, e passado para o próximo step (`validate-plan`) como input.

## Integração com pipeline

No Harness v3, o pipeline L2 completo passa por:

```
discuss → write-spec → grill-me → design-doc → validate-plan → tdd → verify-against-spec
```

A skill **design-doc** entra logicamente **entre** `grill-me` (que endurece a spec eliminando ambiguidades) e `validate-plan` (que checa se o plano cobre a spec + design). Ou seja:

- **Input**: `docs/specs/{slug}-spec.md` (aprovada, sem `[NEEDS CLARIFICATION]`) + `docs/CONTEXT.md`.
- **Output**: `docs/specs/{slug}-design.md` com traceability completa para a spec.
- **Próximo step**: `validate-plan` consome spec + design para verificar cobertura antes de TDD.

Dessa forma, o design doc separa claramente o **o que** (spec) do **como** (design), mantém o plano focado em **passos de execução**, e preserva traceability ponta-a-ponta dos requisitos até o código final.

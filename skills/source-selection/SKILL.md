---
name: source-selection
description: "Decide QUAIS fontes mandam antes de escrever ou atualizar documentação. Confronta código, testes, specs, commits, vault e a doc existente; classifica cada fonte em autoritativa, corroborante, obsoleta ou suspeita; e nomeia explicitamente o que ninguém sabe. Primeiro passo dos pipelines L1-docs e L2-docs do Harness v3. Use antes de qualquer trabalho de documentação, README, changelog ou guia — documentar sem escolher a fonte produz texto plausível e errado."
category: workflow
risk: low
source: custom
date_added: "2026-09-02"
metadata:
  version: 1
  triggers: source-selection, selecao de fontes, qual fonte manda, fonte autoritativa, antes de documentar
---

# Source Selection — decidir de onde a verdade vem

Documentação errada é pior que documentação ausente. Ausente, alguém vai ler o
código. Errada, a pessoa acredita e age.

Este repositório tem a prova. Por semanas o `README.md` afirmou *"Each pipeline
ends with `verify-against-spec`"*, e isso deixou de ser verdade no commit
`d7fa6d8` — quando as pipelines L2 passaram a terminar em `verify-multimodel`.
Ninguém mentiu; a frase simplesmente foi escrita a partir da fonte errada (a
doc anterior) em vez da fonte que manda (`contract/pipelines.json`). O
`spec-template.md` carregou pelo mesmo motivo um checkbox impossível — *"review
multi-modelo (Claude + Codex + Gemini)"* — meses depois de o plugin que faria
isso ter sido apagado do disco.

Esta fase existe para que a pergunta *"de onde eu sei isso?"* seja respondida
**antes** de a frase ser escrita, não depois de alguém tropeçar nela.

## Quando ativar

- Primeiro passo de `L1-docs` e `L2-docs`.
- Antes de escrever ou atualizar README, changelog, guia, docstring de módulo,
  ou qualquer página do vault.
- Quando duas fontes discordam e você precisa decidir qual vale.

**Não ativar** quando: a doc é sobre código que você acabou de escrever nesta
mesma sessão (a fonte é a conversa, e ela é autoritativa por construção); ou o
pedido é um comentário de uma linha.

## Objetivo

Produzir uma tabela curta que diga, para cada afirmação que a doc vai fazer,
**qual arquivo manda** — e uma lista explícita do que ninguém sabe.

Não é levantamento exaustivo. É a resposta a uma pergunta: *se código e doc
discordarem, qual dos dois eu acredito?*

## Hierarquia de fontes

Da mais forte para a mais fraca. A regra é simples: **fonte que a máquina
executa vence fonte que humano escreveu.**

| Nível | Fonte | Por quê |
|---|---|---|
| 1 | **Código executado** — o arquivo que roda em produção | Não pode mentir; se mentisse, quebrava |
| 2 | **Testes que passam** | Codificam a intenção *e* são verificados a cada run |
| 3 | **Schemas, contratos, lockfiles** | Máquina valida; drift vira erro |
| 4 | **Spec aprovada** | Intenção declarada, mas pode ter sido superada pela implementação |
| 5 | **Commits e PRs** | Dizem o *porquê*, que o código não diz — mas envelhecem |
| 6 | **Vault / wiki** | Bom para decisão e contexto; **frequentemente velho**. Cheque `updated:` |
| 7 | **Documentação existente** | A mais fraca. É o que você veio consertar |

Duas armadilhas que aparecem sempre:

- **A doc citando a doc.** Se a única sustentação de uma frase é outra página,
  a frase não tem fonte — tem eco. Marque como suspeita.
- **O vault com `updated:` antigo.** Uma página de julho descrevendo um sistema
  que mudou em agosto é fonte de *história*, não de *estado*. Neste vault, as
  páginas de `wiki/projects/harness4claude/` estão em `2026-07-23` e falam de
  "164 testes" num repo que hoje tem mais de 960.

## Workflow

1. **Liste as afirmações que a doc vai fazer.** Cinco a quinze, em uma linha
   cada. Se você não consegue enumerá-las, ainda não sabe o que vai escrever.

2. **Para cada uma, ache a fonte de maior nível que a sustenta.** Cite
   `arquivo:linha`. Se a maior fonte disponível é nível 6 ou 7, isso é um
   achado, não um detalhe.

3. **Marque as discordâncias.** Onde duas fontes divergem, registre as duas e
   diga qual vence pela hierarquia. Discordância entre nível 1 e nível 7 quase
   sempre significa doc podre — mas às vezes significa **bug**, e distinguir os
   dois é trabalho desta fase, não da seguinte.

4. **Nomeie o que ninguém sabe.** Uma afirmação sem fonte de nível 1–5 vira
   `[NEEDS CLARIFICATION]`, exatamente como em `write-spec`. Não invente
   sustentação; a fase `documentation` precisa saber onde não pisar.

5. **Consulte o vault com `wiki-query`, e trate como nível 6.** Ele responde
   *por que* uma decisão foi tomada, coisa que o código não guarda. Mas
   confirme o `updated:` antes de citar como estado atual.

## Saída

Um bloco em Markdown, entregue à fase `documentation` na mesma conversa. Sem
arquivo próprio — é insumo, não artefato.

```markdown
## Fontes

| Afirmação a documentar | Fonte que manda | Nível | Nota |
|---|---|---|---|
| L2 termina em verify-multimodel | `contract/pipelines.json:12` | 3 | README:37 dizia o contrário |
| São 5 dimensões de review | `scripts/workflows/wf-verify-multimodel.js:57-78` | 1 | vault corrobora (04 Algoritmos) |
| O threshold é 0.5 | `wf-verify-multimodel.js:148` | 1 | **não calibrado**; ver R9 |

## Discordâncias

- `README.md:37` × `contract/pipelines.json:12` → vence o contrato. README podre desde `d7fa6d8`.

## Sem fonte

- [NEEDS CLARIFICATION] o que `source-selection` deveria fazer segundo o autor original — declarado no contrato v1.1.0, nunca especificado.
```

## Boundaries

**ALWAYS**
- Citar `arquivo:linha`. Fonte sem endereço não é fonte.
- Registrar a data quando a fonte é vault ou doc.
- Dizer "não sei" em vez de escolher a fonte mais conveniente.

**NEVER**
- Sustentar uma afirmação apenas na documentação que se está reescrevendo.
- Tratar uma página de vault como estado atual sem checar `updated:`.
- Silenciar uma discordância por ser inconveniente — ela pode ser um bug.

**ASK**
- Quando a discordância entre nível 1 e nível 4 (código × spec) for grande o
  bastante para significar que um dos dois está errado. Aí não é doc: é
  decisão do usuário sobre qual dos dois consertar.

---
name: documentation
description: "Escreve ou atualiza documentação a partir das fontes que a fase source-selection já classificou, deixando cada afirmação rastreável até um arquivo:linha. Cobre README, changelog, guias, docstrings de módulo e páginas de vault. Fase de execução dos pipelines L1-docs e L2-docs do Harness v3. Use depois de source-selection, quando o pedido é documentar, atualizar README ou escrever guia."
category: workflow
risk: low
source: custom
date_added: "2026-09-02"
metadata:
  version: 1
  triggers: documentation, documentar, atualizar readme, escrever doc, changelog, guia de uso
---

# Documentation — escrever o que é verdade, e só

Esta fase pressupõe que `source-selection` já rodou e já disse **de onde cada
afirmação vem**. Se essa tabela não existe, volte e rode aquela fase primeiro:
escrever doc sem decidir a fonte foi o que produziu, neste próprio repositório,
um `README.md` que afirmou por semanas algo que o `contract/pipelines.json`
contradizia.

## Quando ativar

- Depois de `source-selection`, nos pipelines `L1-docs` e `L2-docs`.
- Quando o usuário pede README, changelog, guia, tutorial ou docstring.

**Não ativar** para: comentário inline de uma linha (é parte de escrever o
código, não uma fase); spec formal (`write-spec`); design técnico
(`design-doc`); ou nota de decisão (é página de vault, escrita pelo fluxo do
`assimilar`).

## O que documentar, e o que não

O critério é único: **documente o que o leitor não consegue derivar do código
em trinta segundos.**

| Documente | Não documente |
|---|---|
| Por que a decisão foi essa, e o que foi recusado | O que a função faz, quando o nome já diz |
| Como usar — comando concreto, entrada e saída reais | Assinatura repetida em prosa |
| O que quebra se o leitor fizer o óbvio errado | Restatement do type hint |
| O limite conhecido, o não calibrado, o não medido | Aspiração ("deve ser rápido") |
| A unidade quando ela engana (turno = chamada de hook, não troca) | Data de criação, autor — o git guarda |

A regra mais violada é a última coluna do topo: prosa que reescreve a
assinatura. Ela envelhece com a assinatura e não acrescenta nada, e é o
material do qual a documentação podre é feita.

## Workflow

1. **Leia a tabela de fontes.** Cada afirmação que você vai escrever tem que
   estar lá. Se você sentir vontade de escrever algo que não está, pare: ou
   volte à `source-selection`, ou marque `[NEEDS CLARIFICATION]`.

2. **Escreva a mudança mínima.** Documentação é código: diff pequeno, revisável.
   Reescrever a página inteira para consertar uma frase perde o histórico e
   esconde o que mudou.

3. **Números levam procedência.** Um número sem medição é chute com cara de
   fato — foi assim que `FLOOR=0.55` sobreviveu meses neste repo sem que
   ninguém soubesse que ele nunca vetava nada. Escreva *"0.5, não calibrado
   (ver R9)"*, não *"0.5"*.

4. **O que não existe, diga que não existe.** Uma doc que descreve um passo
   como se ele funcionasse, quando ele não tem implementação, custa mais que o
   silêncio: manda o leitor procurar o que não há.

5. **Atualize o vault quando a decisão for durável.** Estado do código vive no
   código; *por que* a decisão foi tomada vive no vault. Antes de escrever
   página nova, cheque prior art com `wiki-query`.

## Rastreabilidade

Toda afirmação não-óbvia carrega um endereço, em prosa ou em comentário:

```markdown
L2 termina no Workflow `wf-verify-multimodel` (5 dimensões em paralelo +
adjudicação adversarial). L1 termina em `verify-against-spec`, que checa
cobertura item por item.
```

Não vire nota de rodapé para tudo — vira ruído. A régua: **se a afirmação
puder envelhecer sem ninguém notar, ela precisa de endereço.**

## Boundaries

**ALWAYS**
- Escrever a partir da tabela de fontes, nunca da doc que se está substituindo.
- Marcar número não calibrado como não calibrado.
- Dizer explicitamente quando algo não existe ou não foi executado nenhuma vez.

**NEVER**
- Afirmar comportamento que você não confirmou numa fonte de nível 1–3.
- Copiar exemplo de comando sem tê-lo rodado. Exemplo quebrado é pior que
  ausência de exemplo: ele parece testado.
- Documentar aspiração no presente do indicativo. *"O roteador escolhe o
  modelo"* quando o roteador não está ligado é uma afirmação falsa, não uma
  meta.

**ASK**
- Quando a doc correta expõe que o código está errado. Consertar qual dos dois
  é decisão do usuário; documentar o bug em silêncio, não.

## Verificação

O passo final do pipeline (`verify` em L1-docs, `verify-against-spec` em
L2-docs) fecha o ciclo. O que ele deve conseguir confirmar:

1. Toda afirmação da tabela de fontes aparece na doc, ou foi explicitamente
   descartada com motivo.
2. Todo comando de exemplo roda como escrito — no shell desta máquina, que é
   PowerShell, e não apenas em bash.
3. Nenhum `[NEEDS CLARIFICATION]` sobrou sem decisão.
4. Nenhum caminho ou identificador citado deixou de existir.

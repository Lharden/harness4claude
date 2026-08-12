---
name: wiki-query
description: "Consulta a wiki AI-Brain e responde com citacao de pagina, em vez de reler fontes. Use quando a pergunta for sobre decisao ja tomada, tecnica ja assimilada, historico do trabalho ou arquitetura de um dos harnesses — e antes de assimilar tecnica nova, para checar prior art. Tambem arquiva a descoberta como pagina nova quando a resposta exigiu raciocinio inedito."
category: knowledge
risk: low
source: custom
date_added: "2026-08-11"
metadata:
  version: 1
  triggers: wiki, consultar wiki, prior art, ja assimilamos, ja decidimos, ja tentamos, historico, decisao anterior, o que ficou registrado
---

# Wiki Query — a operacao de leitura da wiki

> Das quatro operacoes do padrao LLM Wiki, esta e a unica que **le**. Sem ela o vault
> vira espelho de escrita e apodrece em silencio — foi o que aconteceu de 2026-05 a
> 2026-08, quando `query` foi trocada por uma segunda operacao de escrita.

## Quando usar

- **Antes de assimilar tecnica nova** (o caso principal): checar se ja entrou, ou se ja
  foi recusada e por que. Evita reassimilar e evita relitigar decisao fechada.
- Pergunta sobre decisao, arquitetura ou historico que a wiki provavelmente cobre.
- Automaticamente na fase `discuss` de pipelines L2 — ver `graph-context` para o
  contexto de **codigo**; esta skill cobre o contexto de **decisao**.

Nao use para pergunta sobre o codigo atual de um repo: para isso existe `graph-context`
(knowledge graph do graphify). A wiki guarda o porque, o grafo guarda o como.

## Protocolo

### 1. Garantir o indice fresco

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/build_wiki_index.py" --root "$VAULT_PATH/AI-Brain" --check-stale \
  || python "$CLAUDE_PLUGIN_ROOT/scripts/build_wiki_index.py" --root "$VAULT_PATH/AI-Brain"
```

`--check-stale` sai 0 quando fresco e 1 quando ausente/desatualizado. Rebuild custa um
embed por secao — nao rode a cada consulta, so quando o check acusar.

### 2. Consultar

```bash
python "$CLAUDE_PLUGIN_ROOT/tools/wiki_query.py" "<pergunta>" --top-k 5
```

Cada hit traz `wikilink`, secao, camada (A = match exato, B = semantico), score e
`confident`.

### 3. Ler as paginas antes de responder

**O score ranqueia; quem julga relevancia e voce.** Abra as paginas dos hits e confirme
que respondem de fato. Um hit alto pode ser vizinhanca tematica, nao resposta.

### 4. Responder com citacao

Toda afirmacao tirada da wiki cita a pagina: `Trocamos TLA+ por twin-execution
([[decisions/assimilacoes-2026]])`. Sem citacao, a resposta e indistinguivel de memoria
do modelo — e a rastreabilidade e o ponto do vault.

### 5. Arquivar descoberta nova

Se a resposta exigiu raciocinio que a wiki nao tinha, escreva-o como pagina e linke.
E o passo que faz a base crescer a cada pergunta, em vez de so ser consumida.

## Como ler as bandas de confianca

| Situacao | O que dizer |
|----------|-------------|
| `confident: true` | A wiki cobre. Responda citando. |
| `confident: false`, hits presentes | "A wiki pode nao cobrir isto; o mais proximo e X." Verifique antes de citar. |
| `confident_hits: 0` e nada relevante ao ler | **Diga que a wiki nao cobre.** Nao force uma pagina a virar resposta. |
| `available: false` | Indice ausente — rode o build (passo 1). |

A banda de confianca (`0.45`) e o piso do skill-router, reaproveitado como barra de
"vale afirmar": nenhuma pergunta fora do dominio do vault a alcanca. A banda de exibicao
(`0.32`) e mais baixa de proposito — acerto correto em rank #1 costuma pontuar 0.33-0.40,
e cortar em 0.45 descartaria resposta certa.

## Regras

- Citar sempre. Afirmacao sem `[[pagina]]` nao veio da wiki.
- Nao inventar cobertura: sem hit relevante, a resposta correta e "a wiki nao cobre".
- Nao reescrever pagina existente para "encaixar" a resposta — expanda ou crie nova.
- Consulta e read-only. Escrita na wiki so no passo 5, deliberada.
- Falha de busca nunca bloqueia a tarefa: sem Ollama, a Camada A ainda responde.

## Operacao e limites

`docs/wiki-query.md` — knobs, latencia medida, golden set e limites conhecidos.

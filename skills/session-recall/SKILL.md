---
name: session-recall
description: "Encontra e traz contexto de conversas anteriores — desta sessão, deste projeto ou de outro. Busca semântica sobre os transcripts indexados, devolve a sessão, o turno e o trecho, e o comando para retomar. Use quando o usuário disser 'a gente já discutiu isso', 'o que decidimos sobre X', 'em qual sessão eu vi isso', quando precisar do que uma sessão-mãe decidiu, ou quando você mesmo suspeitar que o assunto já foi tratado antes."
category: workflow
risk: low
source: custom
date_added: "2026-09-02"
metadata:
  version: 1
  triggers: session-recall, sessao anterior, ja discutimos, o que decidimos, em qual sessao, contexto da mae, cross-sessao
---

# Session Recall — achar a conversa que já tocou nisto

Há 343 transcripts em `~/.claude/projects/`. Até 2026-09-02 nada os indexava:
reencontrar uma decisão exigia lembrar o uuid, e `/resume` carrega a sessão
inteira — um jsonl de 5,7 MB não cabe em contexto.

Esta skill devolve **o trecho e o endereço**, não a sessão. O que carregar
depois é decisão do usuário.

## Como invocar

```bash
H4C="$(cat ~/.claude/harness/plugin-root)"          # bash
# $H4C = Get-Content "$HOME/.claude/harness/plugin-root"   # PowerShell

python "$H4C/tools/session_query.py" "<pergunta em linguagem natural>"
```

| Flag | Para quê |
|---|---|
| `--project <nome>` | restringe a um projeto ou cwd |
| `--session <uuid\|ref>` | busca dentro de UMA sessão — é assim que um ramo consulta a mãe sem carregá-la |
| `--recent <cwd>` | últimas sessões daquele diretório, **sem embedding**, custo zero |
| `--top-k N` | padrão 5 |
| `--json` | saída estruturada |

## Quando ativar

- O usuário referencia trabalho anterior: *"a gente já discutiu"*, *"o que
  decidimos sobre X"*, *"em qual sessão eu vi aquilo"*.
- Você está num **ramo** e precisa do que a sessão-mãe decidiu — use
  `--session <parent_session>`, que está em `branches.json`.
- Você suspeita que o assunto já foi tratado. Suspeitar e não checar é como o
  mesmo problema é resolvido duas vezes com respostas diferentes.

**Não ativar** quando: o contexto está nesta conversa (leia daqui); o usuário
pediu prior art de *decisão técnica* — aí é `wiki-query`, que cobre o vault.

## Como ler o resultado

```
[sessoes relacionadas] 3 conversa(s):
  * e7e515 · "branch-keeper-ramification" · 2026-08-27 · turno 0 · cos 0.4848
      eficiente? /brainstorming
      claude --resume e7e51517-a1ed-495b-af16-a52c8cd453d8
```

O `*` marca acima do piso de confiança. **Os pisos são herdados do
`wiki_query` e não foram calibrados neste corpus** — prosa de wiki e transcript
de conversa têm distribuições de cosseno diferentes. Trate `cos` como ordem
relativa, não como probabilidade.

## O que fazer com o resultado

1. **Mostre ao usuário** as linhas, com ref curta e data. Não carregue nada
   ainda — a decisão foi híbrida de propósito: o sistema sugere, ele escolhe.
2. **Se ele escolher**, o caminho barato é rodar `--session <ref>` com uma
   pergunta mais específica: traz mais trechos daquela conversa sem carregar a
   sessão inteira.
3. **`/resume` só quando ele pedir.** É o caminho caro, e substitui a sessão
   atual.

## Quando o índice está velho

O `SessionEnd` marca `.stale` ao fechar cada sessão; o índice não se reconstrói
sozinho, porque reindexar 2900 chunks custa ~30s de Ollama num momento em que
ninguém espera resultado.

```bash
python "$H4C/scripts/build_sessions_index.py" --check-stale   # fresh | stale
python "$H4C/scripts/build_sessions_index.py"                 # reconstrói
```

Uma sessão fechada hoje só aparece na busca depois da reconstrução. Se o
usuário procura algo muito recente e não acha, é a primeira coisa a checar.

## Boundaries

**ALWAYS**
- Mostrar a ref curta e a data. Sem elas o usuário não distingue duas conversas
  parecidas.
- Dizer quando não achou nada, em vez de apresentar o melhor resultado ruim
  como se fosse resposta.

**NEVER**
- Carregar uma sessão sem o usuário pedir.
- Tratar o `cos` como certeza: o piso não foi calibrado neste corpus.
- Usar isto para prior art de decisão técnica — é `wiki-query`.

**ASK**
- Quando dois resultados de confiança parecida apontam para conversas
  diferentes. Qual é a certa é ele quem sabe.

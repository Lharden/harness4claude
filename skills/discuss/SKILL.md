---
name: discuss
description: "Fase de alinhamento upstream. Captura decisoes do usuario em 3 tiers (Locked/Deferred/Discretion) antes de planejar. Gera docs/CONTEXT.md que constraina todas as etapas downstream. Usar em pipelines L2 antes de brainstorming."
category: workflow
risk: low
source: custom
date_added: "2026-04-03"
metadata:
  version: 1
  triggers: discuss, contexto, decisoes, alinhamento, requisitos, constraints
---

# Discuss — Alinhamento Upstream

> Captura o que o usuario quer ANTES de planejar, evitando que a AI drope requisitos ou adicione features nao pedidas.

## Quando usar

- Automaticamente em pipelines L2 (antes de `brainstorming`)
- Manualmente quando houver ambiguidade sobre requisitos

## Protocolo

### 0. Checar prior-art na wiki

Antes de perguntar qualquer coisa, veja se a decisao ja foi tomada:

```bash
python "$CLAUDE_PLUGIN_ROOT/tools/wiki_prior_art.py" "<descricao da tarefa>"
```

- **Saida vazia** (o caso comum): siga para o passo 1, sem comentar.
- **Bloco de prior-art**: leia as paginas citadas antes de perguntar. Se a tarefa
  propoe algo ja recusado, traga isso ao usuario com o motivo registrado — a decisao
  dele pode mudar, mas ele decide sabendo. Se ja foi adotado, nao reabra: pergunte se
  quer estender ou substituir.

Nunca bloqueia: o comando sai 0 mesmo com indice ausente ou Ollama fora do ar. Se o
bloco avisar que o indice esta desatualizado, trate os achados como incompletos.

### 1. Extrair decisoes

Pergunte ao usuario sobre estes eixos (apenas os relevantes ao pedido):

- **O que e critico vs nice-to-have?** (prioridade)
- **Preferencias de design?** (ex: "layout em cards", "sem animacoes", "modo escuro")
- **Constraints tecnicas?** (ex: "usar SQLAlchemy, nao Django ORM", "Python 3.12+")
- **Areas de risco?** (ex: "a autenticacao e sensivel, precisa de testes extras")
- **O que NAO fazer?** (ex: "nao mexer no modulo X", "nao adicionar dependencias novas")

### 2. Classificar em 3 tiers

Com base nas respostas, organize:

**Locked (L-01, L-02, ...)** — DEVE ser implementado exatamente como descrito. Planner e executor nao podem desviar.

**Deferred** — Fora do escopo desta tarefa. Planner deve excluir explicitamente.

**Discretion** — Claude decide a implementacao. Usuario confia no julgamento da AI.

### 3. Gerar docs/CONTEXT.md

Escreva o arquivo com este formato:

```markdown
# CONTEXT — [nome da feature/tarefa]

> Gerado pela fase discuss do Harness v3. Este documento constraina todas as etapas downstream.

## Locked Decisions

- **L-01**: [descricao exata]
- **L-02**: [descricao exata]

## Deferred (fora do escopo)

- [item excluido e por que]

## Discretion (Claude decide)

- [area onde Claude tem liberdade]

## Constraints Tecnicas

- [constraint 1]
- [constraint 2]

## Notas

- [qualquer contexto adicional relevante]
```

### 4. Confirmar com usuario

Exiba o CONTEXT.md ao usuario e pergunte: "Estas decisoes estao corretas? Algo a ajustar?"

So prossiga para o proximo step do pipeline apos confirmacao.

## Regras

- Nao inventar decisoes — so registrar o que o usuario disse
- Se o usuario nao tem opiniao sobre algo, classificar como Discretion
- Manter CONTEXT.md conciso — nao e um PRD, e um registro de constraints
- Todas as etapas downstream (brainstorming, prd, plan, tdd) DEVEM ler e respeitar CONTEXT.md
- Prior-art informa, nao decide: achado da wiki e insumo para a conversa, nunca veto

## Onde o CONTEXT.md vai parar

O `vault_sync` espelha `docs/CONTEXT.md` para `wiki/decisions/{projeto}-context.md` no
vault AI-Brain. Os tres tiers viram registro de assimilacao — **Locked** e o que foi
adotado, **Deferred** e o que foi recusado, **Discretion** e a area de liberdade. E por
isso que o passo 0 acha decisao de tarefas antigas: o que voce escrever aqui alimenta o
prior-art da proxima.

---
adr: 000
title: O objeto-de-medição da reforma é o plugin em cache, sincronizado com main
status: accepted
date: 2026-07-24
deciders: Leonardo, Harness4Claude
---

# ADR-000 — Objeto-de-medição da reforma

## Contexto

O Claude Code carrega o plugin de `~/.claude/plugins/cache/harness4claude/harness4claude/3.2.0`, instalado em 2026-06-17 a partir do commit `24c1812`. O clone de trabalho está em `a56ee80` (v3.3.0-beta.1) — cinco semanas e doze commits à frente, incluindo o skill-router completo, que está **mergeado em main e inativo em runtime**.

Existe ainda um terceiro clone em `~/.claude/plugins/local/harness4claude`, e dez caminhos hardcoded em skills e docs instruem o LLM a executar scripts dele — enquanto os hooks executam do cache. Duas árvores de código em uso simultâneo.

O plano exige (§5) "impedir que a autorreforma altere o objeto que está sendo medido durante a medição". Antes disso, é preciso decidir **qual é** o objeto.

## Decisão

**O objeto-de-medição é o plugin instalado em cache, mantido sincronizado com `main` por release deliberado.**

Consequências operacionais:

1. Antes da Fase 0, promover `3.3.0` (sair de beta) e fazer ship. `marketplace.json` e `plugin.json` alinhados na mesma versão.
2. Todo hook emite um `VERSION_STAMP` (versão + commit) no seu output de debug e, a partir da Onda 1, em todo evento de telemetria.
3. `health-check.sh` ganha um bloco de proveniência que compara cache × `git HEAD` × `marketplace.json` e falha fora de janela de ship.
4. Os dez caminhos hardcoded passam a `${CLAUDE_PLUGIN_ROOT}` — uma única árvore em uso.
5. O runtime muda **apenas em fronteira de onda**, por merge → ship → verificação → tag `reform-wN-shipped`, com gate humano. Nunca no meio de uma onda.

## Alternativa considerada

**Fazer baseline do 3.2.0 que roda hoje.** Mede exatamente o que está em produção há cinco semanas — mais estável, sem depender de um ship no início da reforma.

Rejeitada porque: (a) o baseline nasceria obsoleto em relação a main, e toda comparação posterior teria de descontar doze commits não medidos; (b) o skill-router — o componente de engenharia mais recente e o padrão de qualidade de referência para a reforma — ficaria sem medição; (c) o pipeline de ship precisa ser exercitado de qualquer forma, e é preferível fazê-lo agora, com uma mudança conhecida e já testada, do que na Onda 2 com o store novo em jogo.

## Consequências

**Positivas.** O baseline mede o sistema que main descreve. O ship é exercitado cedo, com risco baixo. A proveniência passa a ser verificável por código, não por inspeção manual. O gap de deployment deixa de ser um defeito e vira o mecanismo de canário: o cache é o canal estável pinado, e a promoção é um ato deliberado.

**Negativas.** Exige uma ação manual do usuário (`/plugin update`) em cada fronteira de onda — não automatizável pelo agente. O ship inicial ativa o skill-router em produção, que até agora só foi exercitado manualmente; mitigado pelo golden set de 93,3% e pelo rollback documentado em `docs/router.md`.

## Verificação

`bash scripts/health-check.sh` reporta cache == main == marketplace, e um evento de hook real emite o stamp esperado.

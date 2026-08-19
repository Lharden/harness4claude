---
name: graph-context
description: "Fase de contexto estrutural dos pipelines L2 do Harness v3. Consulta o knowledge graph do repositório (graphify: graphify-out/GRAPH_REPORT.md + graph.json) ANTES de qualquer varredura bruta de arquivos, com queries focadas via graphify query e fallback para o Workflow wf-context-scan quando não há grafo. Use quando iniciar tarefa L2 num repo, quando o usuário pedir contexto/arquitetura de codebase, ou mencionar knowledge graph, graphify, god nodes ou grafo do código."
category: workflow
risk: low
source: custom
date_added: "2026-06-12"
metadata:
  version: 1
  triggers: graph-context, knowledge graph, graphify, god nodes, contexto estrutural, mapa do codebase
---

# Graph Context — contexto estrutural via knowledge graph

Objetivo: alimentar `write-spec`/`design-doc`/exploração com **estrutura real** do repo gastando o mínimo de tokens — grafo primeiro, arquivos brutos depois (relatos upstream: ~70x menos tokens por consulta vs. re-ler arquivos).

## Protocolo

1. **Detectar grafo**: existe `graphify-out/graph.json` no repo?
2. **Existe →** ler `graphify-out/GRAPH_REPORT.md` (1 página: god nodes, comunidades Leiden, conexões surpreendentes, perguntas sugeridas). Para pergunta específica, aprofundar com:
   ```bash
   graphify query "<pergunta>" --graph graphify-out/graph.json
   ```
   Ou `/graphify query|path|explain` (travessia hop-by-hop com tipo de aresta, confiança e arquivo-fonte). Saída da fase: resumo dos god nodes/comunidades **relevantes à task** — nunca o grafo inteiro.
3. **Não existe →**
   - `graphify` CLI instalado? Sugerir `/graphify .` no repo (passe 1 é AST determinístico, sem LLM; passes seguintes usam subagents).
   - CLI ausente? Avisar UMA vez: `bash <plugin>/scripts/setup-graphify.sh` (instalação é decisão do usuário) e seguir.
   - **Fallback sempre disponível**: Workflow `wf-context-scan` (fan-out de exploração). O pipeline NUNCA trava por ausência do graphify.
4. **Frescor — gate, não sugestão**: `graph.json` anterior ao HEAD atual, ou `manifest.json` com `mtime` mais velho que arquivo do disco? Então a saída desta fase **não é contexto conferido**: é a última medição conhecida, e diz isso em voz alta para as fases seguintes. Re-run é barato (cache SHA256 só reprocessa mudanças) — rode `graphify update .` antes de responder; se não der, marque cada afirmação estrutural como não conferida.

   O contra-exemplo que fixa a regra: grafo velho não devolve "não sei". Devolve vizinhança plausível, com nome de arquivo e tipo de aresta, descrevendo código que já não existe — a forma exata de uma boa notícia. É por isso que `tools/impact.py` expõe `grafo_status` no `resumo` em vez de listar o fato entre os avisos (absorvido do `isCurrentGraph` do open-science, 2026-08-19, `wiki/sources/open-science.md`).
5. **Espelho no vault (conhecimento permanente)**:
   ```bash
   /graphify . --obsidian --obsidian-dir "${AI_BRAIN_PATH:-$VAULT_PATH/AI-Brain}/wiki/graphs/{repo-slug}"
   ```
   Convenção: `wiki/graphs/{repo-slug}/` no vault AI-Brain (slug = nome do diretório git, kebab-case). O vault-bridge indexa em `wiki/index.md` e cruza wikilinks com `wiki/specs/{proj}/`.

## Regras

- **ALWAYS**: `graphify-out/` no `.gitignore` do repo analisado; resumir antes de citar (graph.json nunca inteiro no contexto).
- **NEVER**: bloquear pipeline por ausência de grafo; instalar pacotes sem o usuário (use setup-graphify.sh); apresentar leitura de grafo desatualizado como estrutura conferida do repo.
- O hook PreToolUse instalado por `graphify claude install` já lembra de ler o GRAPH_REPORT antes de Glob/Grep — esta skill é a versão **ativa e dirigida** desse comportamento dentro dos pipelines.

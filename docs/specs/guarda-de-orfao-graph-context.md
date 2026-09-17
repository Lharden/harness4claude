# Graph context — guarda de órfão

**Task:** `t-20260916-215220419949` · **Grafo:** `graphify-out/graph.json`, build de `5ca9d4ea` (= HEAD)
**Escala:** 5 169 nós · 7 882 arestas · 320 comunidades · extração 99% AST, 0 tokens de LLM

---

## O que o grafo respondeu

**1. Os validadores de saúde do repositório são uma família com contrato comum, e o grafo os agrupa.**
`graph_lint.py`, `wiki_lint.py`, `arsenal.py`, `compendium.py`, `impact.py`,
`design_scope.py` e `vault_sync_doctor.py` compartilham o contrato de saída que o
`tools/README.md` descreve: JSON no stdout, booleano `ready`, exit 1, **reporta e
nunca corrige**. O guarda de órfão nasce nessa família, e herdar o contrato é a
decisão de menor custo — o `health-check.sh` já sabe consumir essa forma.

**2. `analyze_wiki()` tem comunidade própria, e ela é feita de teste.**
O grafo dá ao `analyze_wiki` uma comunidade nomeada com o próprio nome, cujos nós
vizinhos são os do `test_wiki_lint.py`. Os hubs de comunidade da lista incluem
`test_wiki_moc.py`, `test_harness_lite_adapter.py`, `test_compendium.py`. **Quando
o centro de gravidade de um módulo é o teste dele, é porque não há mais nada
puxando.** É a mesma leitura da medição, por outra via.

## O que o grafo NÃO respondeu — e por que isso decidiu o desenho

A vizinhança de `graph_lint.py` no grafo tem **zero** arestas. O `tools/README.md`
já nomeia o limite e ele vale aqui inteiro:

> "o grafo do graphify é **não-direcionado**, então a saída é vizinhança, não
> dependência — chamar de 'quem depende' seria inventar causalidade a partir de
> adjacência."

A pergunta deste ramo é *"existe caminho de uma raiz externa até esta função?"*, e
ela é **direcionada por definição**. Um grafo não-direcionado não a responde, e um
com arestas faltando responde errado com confiança.

**Consequência para o desenho:** o guarda constrói o próprio grafo de chamadas,
dirigido, por AST, na hora. Não consome `graphify-out/`. Isso não é duplicação —
é a diferença entre vizinhança temática e alcançabilidade.

**Ângulo morto declarado:** o grafo não foi usado para validar a medição, então
ele não corrobora nem contradiz os 67. A corroboração veio de leitura manual caso
a caso, registrada na medição.

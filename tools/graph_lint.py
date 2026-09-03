"""Health check do knowledge graph — a única coisa aqui que ninguém conferia.

Este repositório valida quase tudo: `wiki_lint` a wiki, `arsenal check` o
registry, `compendium check` os verbetes, smoke e liveness os hooks. O grafo do
graphify era a exceção — se ele perdesse arestas, ganhasse referência órfã ou
degenerasse numa comunidade só, nada avisava, e as três coisas que dependem dele
(`graph-context`, `impact.py`, o export para o vault) passariam a responder com
confiança sobre uma estrutura errada.

A lacuna foi apontada em 2026-08-13 por duas fontes externas independentes — o
`graph-reviewer` do Understand-Anything e o bloco de *knowledge gaps* do
code-review-graph — e nenhuma delas foi instalada. O que entrou foi a pergunta.

**Reporta, nunca corrige**, no mesmo contrato do `wiki_lint`: o grafo é derivado,
e a correção é `graphify update .`, não uma edição no artefato.

Severidade calibrada, e a distinção importa:

    ERRO    integridade referencial — aresta apontando para nó que não existe.
            É defeito do artefato: alguma coisa foi escrita errada ou perdida.
    AVISO   característica que degrada o uso — nó isolado, comunidade que é um
            quarto do grafo, comunidade de um nó só. O grafo está íntegro; o que
            está ruim é o que dá para fazer com ele.

Tratar as duas como a mesma coisa faria o relatório viver vermelho por
característica e ninguém veria o defeito de verdade no meio.

Uso:
    python tools/graph_lint.py [--graph DIR] [--report]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Comunidade acima desta fração do grafo não explica nada: dizer que um quarto
# dos nós "pertence ao mesmo agrupamento" é o mesmo que não agrupar. O limiar vem
# do code-review-graph, que faz split recursivo acima dele.
FRACAO_COMUNIDADE_GORDA = 0.25

# Nó com grau acima disto é ponto de passagem. Não é defeito — é informação de
# arquitetura, e é o mesmo limiar que o impact.py usa como barreira.
GRAU_DE_HUB = 500


def carrega(diretorio: Path) -> tuple[dict, list[str]]:
    caminho = Path(diretorio) / "graph.json"
    if not caminho.is_file():
        return {}, [f"grafo ausente em {caminho} — rode `graphify update .`"]
    try:
        with open(caminho, encoding="utf-8") as handle:
            return json.load(handle), []
    except (OSError, ValueError) as exc:
        return {}, [f"grafo ilegível: {exc}"]


def analisa(grafo: dict) -> dict:
    nos = grafo.get("nodes") or []
    arestas = grafo.get("links") or []
    ids = {n.get("id") for n in nos if n.get("id")}

    # --- integridade referencial: o único ERRO deste lint --------------------
    orfas: list[dict] = []
    for aresta in arestas:
        for ponta in ("source", "target"):
            alvo = aresta.get(ponta)
            if alvo not in ids:
                orfas.append({"aresta": f"{aresta.get('source')} -> {aresta.get('target')}",
                              "ponta": ponta, "alvo": alvo,
                              "relacao": aresta.get("relation")})
                break

    grau: dict[str, int] = defaultdict(int)
    for aresta in arestas:
        if aresta.get("source") in ids:
            grau[aresta["source"]] += 1
        if aresta.get("target") in ids:
            grau[aresta["target"]] += 1

    isolados = sorted(n["id"] for n in nos if n.get("id") and grau.get(n["id"], 0) == 0)
    sem_arquivo = sorted(n["id"] for n in nos
                         if n.get("id") and not str(n.get("source_file") or "").strip())

    comunidades = Counter(n.get("community") for n in nos if n.get("community") is not None)
    total = len(nos) or 1
    gordas = [{"comunidade": c, "nos": q, "fracao": round(q / total, 3)}
              for c, q in comunidades.most_common()
              if q / total > FRACAO_COMUNIDADE_GORDA]
    finas = sum(1 for q in comunidades.values() if q <= 1)

    hubs = sorted(((i, g) for i, g in grau.items() if g > GRAU_DE_HUB),
                  key=lambda x: -x[1])

    # Confiança: quanto do grafo é inferência? Não é defeito, é calibragem — quem
    # lê o impact.py precisa saber se está olhando fato ou palpite.
    conf = Counter(a.get("confidence") or "?" for a in arestas)
    inferidas = conf.get("INFERRED", 0)

    return {
        "nos": len(nos), "arestas": len(arestas),
        "arestas_orfas": orfas,
        "isolados": isolados,
        "sem_source_file": sem_arquivo,
        "comunidades": len(comunidades),
        "comunidades_gordas": gordas,
        "comunidades_de_um_no": finas,
        "hubs": [{"no": i, "grau": g} for i, g in hubs[:10]],
        "arestas_inferidas": inferidas,
        "fracao_inferida": round(inferidas / len(arestas), 4) if arestas else 0.0,
    }


def command_graph_lint(diretorio: Path) -> dict:
    grafo, erros = carrega(diretorio)
    if not grafo:
        return {"comando": "graph-lint", "ready": False, "errors": erros,
                "warnings": [], "resumo": {}, "detalhe": {}}

    d = analisa(grafo)
    erros_reais, avisos = [], []

    if d["arestas_orfas"]:
        n = len(d["arestas_orfas"])
        erros_reais.append(
            f"{n} aresta(s) apontam para nó inexistente — integridade referencial "
            f"quebrada. Exemplo: {d['arestas_orfas'][0]['aresta']}"
        )
    if d["isolados"]:
        avisos.append(f"{len(d['isolados'])} nó(s) isolados: existem no grafo e não se "
                      "conectam a nada. Consultar por eles nunca traz vizinhança.")
    if d["sem_source_file"]:
        avisos.append(f"{len(d['sem_source_file'])} nó(s) sem source_file — não dá para "
                      "voltar deles ao arquivo, e o impact.py os ignora.")
    for gorda in d["comunidades_gordas"]:
        avisos.append(f"comunidade {gorda['comunidade']} tem {gorda['nos']} nós "
                      f"({gorda['fracao']:.0%} do grafo) — agrupamento desse tamanho não "
                      "explica nada. Reconstrua com `--mode deep` ou `--cluster-only`.")
    if d["comunidades_de_um_no"]:
        avisos.append(f"{d['comunidades_de_um_no']} comunidade(s) de um nó só — ruído de "
                      "clustering, não estrutura.")
    if d["fracao_inferida"] > 0.25:
        avisos.append(f"{d['fracao_inferida']:.0%} das arestas são INFERRED — mais de um "
                      "quarto do grafo é palpite, e o impact.py propaga isso.")

    return {
        "comando": "graph-lint",
        "ready": not erros_reais,
        "errors": erros_reais,
        "warnings": avisos,
        "resumo": {
            "nos": d["nos"], "arestas": d["arestas"],
            "arestas_orfas": len(d["arestas_orfas"]),
            "isolados": len(d["isolados"]),
            "comunidades": d["comunidades"],
            "hubs": len(d["hubs"]),
            "fracao_inferida": d["fracao_inferida"],
        },
        "detalhe": d,
    }


def render(res: dict) -> str:
    linhas = [f"# graph-lint — {'OK' if res['ready'] else 'REPROVADO'}", ""]
    if res.get("resumo"):
        linhas += ["| campo | valor |", "|---|---|"]
        linhas += [f"| {k} | {v} |" for k, v in res["resumo"].items()]
        linhas.append("")
    for titulo, chave in (("Erros (integridade)", "errors"), ("Avisos (uso)", "warnings")):
        itens = res.get(chave) or []
        linhas.append(f"## {titulo} ({len(itens)})")
        linhas += [f"- {i}" for i in itens] or ["- nenhum"]
        linhas.append("")
    hubs = (res.get("detalhe") or {}).get("hubs") or []
    if hubs:
        linhas += ["## Pontos de passagem (não é defeito)", ""]
        linhas += [f"- `{h['no']}` — grau {h['grau']}" for h in hubs]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", default="graphify-out")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    res = command_graph_lint(Path(args.graph))
    print(render(res) if args.report else json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    # `sys.path` conta a historia certa nos DOIS modos de invocacao. Ate
    # 2026-09-03 este bloco confiava em `tools/` ser `sys.path[0]`, o que so
    # vale em `python tools/x.py`: sob `python -m tools.x` o primeiro caminho
    # e o diretorio de trabalho, e o import morria com ModuleNotFoundError.
    # `scripts/health-check.sh` invoca com `-m`, e reportava a falha como
    # "Obsidian doctor nao-ready (app fechado / REST off?)" — com o Obsidian
    # rodando e a porta 27124 respondendo 200.
    import os as _os
    import sys as _sys

    _AQUI = _os.path.dirname(_os.path.abspath(__file__))
    if _AQUI not in _sys.path:
        _sys.path.insert(0, _AQUI)

    from console import usar_utf8

    usar_utf8()
    sys.exit(main())

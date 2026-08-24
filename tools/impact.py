"""Raio de impacto de uma mudança ainda não commitada.

Responde a pergunta que nenhuma ferramenta daqui respondia: **o que esta minha
mudança afeta?** Duas fontes externas independentes apontaram esse buraco no
mesmo dia — o `/understand-diff` do Understand-Anything e o
`get_impact_radius_tool` do code-review-graph — e o graphify, conferido, não tem
nada disso: o `graph_diff` dele compara dois grafos já construídos, pós-commit.

Três coisas que este módulo NÃO faz, e cada uma está escrita porque a alternativa
seria mentir com cara de resposta:

1. **Não diz "quem depende de".** O grafo do graphify é `directed: false`
   (conferido no artefato real, 2026-08-13). Sem direção não existe "chamador" —
   existe "conectado". A saída fala em VIZINHANÇA, e o campo `direcionado` diz se
   o grafo permitia mais. Trocar um pelo outro seria inventar causalidade a
   partir de adjacência.

2. **Não atravessa hub.** Treze nós têm grau acima de 500 no grafo do vault, o
   maior com 4.526. Passar por um deles a dois saltos devolve o repositório
   inteiro: verdadeiro, e inútil. Hub atingido é REPORTADO como barreira — "a
   mudança toca um ponto de passagem, e daqui o grafo não estreita mais" — em vez
   de virar uma lista de tudo.

3. **Não finge saber sobre arquivo que o grafo não conhece.** Arquivo alterado
   que não está no grafo entra em `fora_do_grafo` com impacto DESCONHECIDO.
   Devolver "nenhum impacto" para ele seria a pior falha possível aqui, porque é
   silenciosa e tem a forma exata de uma boa notícia.

Precisão sobre recall, deliberadamente: prefere apontar demais a perder
dependência. Um falso positivo custa uma olhada; um falso negativo custa o bug.

Contrato de saída herdado do wiki_lint: JSON no stdout, booleano `ready`, exit 1
quando há erro. Nunca levanta por grafo ausente — degrada dizendo o que faltou.

Uso:
    python tools/impact.py                      # diff não commitado do repo atual
    python tools/impact.py --ref HEAD~1         # contra outro ponto
    python tools/impact.py --files a.py,b.py    # arquivos explícitos
    python tools/impact.py --depth 3 --report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

# Grau a partir do qual um nó deixa de estreitar e passa a ligar tudo a tudo.
# Medido no grafo do vault em 2026-08-13: 13 nós acima de 500, o maior com 4.526
# arestas. Abaixo disso a vizinhança ainda diz alguma coisa.
GRAU_DE_HUB = 500
PROFUNDIDADE_PADRAO = 2

# Relação que significa dependência de verdade. `contains` é estrutural (arquivo
# contém símbolo) e, num grafo não-direcionado, atravessá-la equivale a dizer
# "tudo no mesmo arquivo é afetado" — o que é trivialmente certo e não informa.
RELACOES_DE_DEPENDENCIA = {"calls", "method", "imports", "extends", "inherits", "uses"}


def carrega_grafo(diretorio: Path) -> tuple[dict, list[str]]:
    """Grafo + avisos. Nunca levanta: ausência de grafo é resposta, não exceção."""
    caminho = Path(diretorio) / "graph.json"
    if not caminho.is_file():
        return {}, [f"grafo ausente em {caminho} — rode `graphify update .` neste repo"]
    try:
        with open(caminho, encoding="utf-8") as handle:
            return json.load(handle), []
    except (OSError, ValueError) as exc:
        return {}, [f"grafo ilegível ({exc})"]


def arquivos_alterados(ref: str | None, cwd: Path) -> tuple[list[str], list[str]]:
    """Arquivos do diff. Inclui não rastreados: arquivo novo também muda o mundo."""
    avisos: list[str] = []
    alvo = ["git", "diff", "--name-only"] + ([ref] if ref else ["HEAD"])
    try:
        saida = subprocess.run(alvo, cwd=cwd, capture_output=True, text=True, timeout=30)
        if saida.returncode != 0:
            return [], [f"git diff falhou: {saida.stderr.strip()[:120]}"]
        arquivos = [linha.strip() for linha in saida.stdout.splitlines() if linha.strip()]
        novos = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                               cwd=cwd, capture_output=True, text=True, timeout=30)
        if novos.returncode == 0:
            arquivos += [linha.strip() for linha in novos.stdout.splitlines() if linha.strip()]
    except (OSError, subprocess.SubprocessError) as exc:
        return [], [f"não foi possível ler o diff: {exc}"]
    return sorted(set(arquivos)), avisos


def _normaliza(caminho: str) -> str:
    return caminho.replace("\\", "/").lstrip("./")


def frescor_do_grafo(diretorio: Path, alterados: set[str], raiz: Path) -> tuple[str, str]:
    """Estado do grafo em relação ao disco: `(status, motivo)`.

    Arquivos que mudaram DESDE a construção do grafo e não estão no diff. O diff
    é esperado estar fora do grafo — é justamente o que se quer analisar. O
    problema é o outro: arquivo que mudou antes, entrou no commit, e o grafo
    nunca foi reconstruído. Aí a vizinhança descreve um código que não existe
    mais, e nada avisaria.

    Devolve status em vez de aviso porque a diferença precisa aparecer no
    `resumo`, não no meio de uma lista que ninguém lê até o fim. Mecanismo
    conferido no `isCurrentGraph` do `module-test-impact.mjs` do open-science
    (2026-08-19, `wiki/sources/open-science.md`): lá o grafo só entra no plano
    quando está corrente, e o status do grafo é campo de saída de primeira
    classe ao lado da resposta. Grafo velho respondendo com cara de medição é o
    mesmo erro que este módulo já evita em `fora_do_grafo` — só que a causa é
    outra e a aparência é ainda melhor, porque o arquivo ESTÁ no grafo.

    Três estados, e os três são resposta:

        atual                  nada mudou depois da construção.
        desatualizado          mudou, e a vizinhança pode descrever código morto.
        frescor-desconhecido   sem manifest.json legível — não dá para afirmar.
    """
    manifesto = Path(diretorio) / "manifest.json"
    if not manifesto.is_file():
        return ("frescor-desconhecido",
                "sem manifest.json — não dá para saber se o grafo está atualizado")
    try:
        with open(manifesto, encoding="utf-8") as handle:
            dados = json.load(handle)
    except (OSError, ValueError):
        return ("frescor-desconhecido", "manifest.json ilegível — frescor do grafo desconhecido")
    velhos = 0
    for rel, meta in dados.items():
        if _normaliza(rel) in alterados:
            continue
        arquivo = raiz / rel
        try:
            if arquivo.is_file() and arquivo.stat().st_mtime > float(meta.get("mtime") or 0) + 1:
                velhos += 1
        except OSError:
            continue
    if velhos:
        return ("desatualizado",
                f"{velhos} arquivo(s) mudaram DEPOIS da construção do grafo e não estão "
                "neste diff — a vizinhança pode descrever código que já não existe. "
                "Rode `graphify update .`")
    return ("atual", "")


def calcula(grafo: dict, alterados: list[str], profundidade: int) -> dict:
    nos = grafo.get("nodes") or []
    arestas = grafo.get("links") or []
    direcionado = bool(grafo.get("directed"))

    por_arquivo: dict[str, list[str]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for no in nos:
        ident = no.get("id")
        if not ident:
            continue
        meta[ident] = no
        origem = _normaliza(str(no.get("source_file") or ""))
        if origem:
            por_arquivo[origem].append(ident)

    grau: dict[str, int] = defaultdict(int)
    # (vizinho, confiança da aresta) — a confiança viaja junto porque um caminho
    # que passa por uma aresta INFERRED não pode ser reportado como certo.
    vizinhos: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for aresta in arestas:
        origem, destino = aresta.get("source"), aresta.get("target")
        if not origem or not destino:
            continue
        grau[origem] += 1
        grau[destino] += 1
        if aresta.get("relation") not in RELACOES_DE_DEPENDENCIA:
            continue
        confianca = aresta.get("confidence") or "EXTRACTED"
        vizinhos[origem].add((destino, confianca))
        if not direcionado:
            vizinhos[destino].add((origem, confianca))

    alterados_norm = {_normaliza(a) for a in alterados}
    sementes, fora = [], []
    for arquivo in sorted(alterados_norm):
        achados = por_arquivo.get(arquivo)
        if achados:
            sementes.extend(achados)
        else:
            fora.append(arquivo)

    hubs: dict[str, int] = {}
    alcancados: dict[str, tuple[int, str]] = {}
    fila = deque((s, 0, "EXTRACTED") for s in sementes)
    vistos = set(sementes)
    truncou = False
    while fila:
        atual, dist, conf = fila.popleft()
        if dist >= profundidade:
            truncou = True
            continue
        if grau.get(atual, 0) > GRAU_DE_HUB and dist > 0:
            # Barreira: daqui o grafo liga tudo a tudo e para de informar.
            hubs[atual] = grau[atual]
            continue
        for vizinho, conf_aresta in vizinhos.get(atual, ()):
            pior = "INFERRED" if "INFERRED" in (conf, conf_aresta) else "EXTRACTED"
            if vizinho in vistos:
                continue
            vistos.add(vizinho)
            alcancados[vizinho] = (dist + 1, pior)
            fila.append((vizinho, dist + 1, pior))

    for semente in sementes:
        if grau.get(semente, 0) > GRAU_DE_HUB:
            hubs[semente] = grau[semente]

    arquivos_afetados: dict[str, dict] = {}
    for ident, (dist, conf) in alcancados.items():
        arquivo = _normaliza(str(meta.get(ident, {}).get("source_file") or ""))
        if not arquivo or arquivo in alterados_norm:
            continue
        atual = arquivos_afetados.get(arquivo)
        if atual is None or dist < atual["saltos"]:
            arquivos_afetados[arquivo] = {"arquivo": arquivo, "saltos": dist,
                                          "confianca": conf, "nos": 1}
        else:
            atual["nos"] += 1
            if conf == "INFERRED":
                atual["confianca"] = "INFERRED"

    ordenados = sorted(arquivos_afetados.values(), key=lambda a: (a["saltos"], a["arquivo"]))
    testes = [a for a in ordenados if "test" in a["arquivo"].lower()]
    return {
        "direcionado": direcionado,
        "sementes": len(sementes),
        "fora_do_grafo": fora,
        "afetados": ordenados,
        "testes_afetados": testes,
        "hubs_atingidos": [{"no": h, "grau": g} for h, g in
                           sorted(hubs.items(), key=lambda x: -x[1])],
        "truncou_na_profundidade": truncou,
    }


def command_impact(diretorio: Path, ref: str | None, files: list[str] | None,
                   profundidade: int, cwd: Path) -> dict:
    grafo, erros = carrega_grafo(diretorio)
    avisos: list[str] = []

    if files:
        alterados, avisos_git = files, []
    else:
        alterados, avisos_git = arquivos_alterados(ref, cwd)
    avisos += avisos_git

    if not grafo:
        return {"comando": "impact", "ready": False, "errors": erros, "warnings": avisos,
                "resumo": {"alterados": len(alterados)}, "detalhe": {}}
    if not alterados:
        return {"comando": "impact", "ready": True, "errors": [],
                "warnings": avisos + ["nenhum arquivo alterado — nada a analisar"],
                "resumo": {"alterados": 0}, "detalhe": {}}

    grafo_status, motivo_frescor = frescor_do_grafo(
        diretorio, {_normaliza(a) for a in alterados}, cwd)
    if motivo_frescor:
        avisos.append(motivo_frescor)
    d = calcula(grafo, alterados, profundidade)

    if d["fora_do_grafo"]:
        avisos.insert(0, f"IMPACTO DESCONHECIDO em {len(d['fora_do_grafo'])} arquivo(s): "
                         "alterados e ausentes do grafo. Não é 'sem impacto' — é não sei.")
    if not d["direcionado"]:
        avisos.append("grafo NÃO direcionado: a saída é vizinhança, não dependência. "
                      "Para direção real, reconstrua com `graphify . --directed`.")
    if d["hubs_atingidos"]:
        avisos.append(f"{len(d['hubs_atingidos'])} hub(s) atingido(s) — daqui o grafo liga "
                      "tudo a tudo e a busca parou. Impacto potencialmente amplo.")
    if d["truncou_na_profundidade"]:
        avisos.append(f"busca truncada em {profundidade} salto(s); pode haver mais além disso")
    if grafo_status != "atual":
        avisos.insert(0, f"VIZINHANÇA NÃO CONFERIDA (grafo {grafo_status}): a lista abaixo foi "
                         "lida de um grafo que o disco já contradiz ou não confirma. Não é "
                         "medição — é a última medição conhecida.")

    return {
        "comando": "impact",
        "ready": True,   # impacto não é defeito: é informação para revisar.
        "errors": [],
        "warnings": avisos,
        "resumo": {
            "alterados": len(alterados),
            "no_grafo": d["sementes"],
            "fora_do_grafo": len(d["fora_do_grafo"]),
            "arquivos_afetados": len(d["afetados"]),
            "testes_afetados": len(d["testes_afetados"]),
            "hubs": len(d["hubs_atingidos"]),
            "profundidade": profundidade,
            "direcionado": d["direcionado"],
            "grafo_status": grafo_status,
        },
        "detalhe": d,
    }


def render(res: dict) -> str:
    d = res.get("detalhe") or {}
    r = res.get("resumo") or {}
    linhas = [f"# impacto — {'OK' if res['ready'] else 'sem grafo'}", ""]
    if r:
        linhas += ["| campo | valor |", "|---|---|"]
        linhas += [f"| {k} | {v} |" for k, v in r.items()]
        linhas.append("")
    for titulo, chave in (("Erros", "errors"), ("Avisos", "warnings")):
        itens = res.get(chave) or []
        if itens:
            linhas.append(f"## {titulo}")
            linhas += [f"- {i}" for i in itens]
            linhas.append("")
    if d.get("fora_do_grafo"):
        linhas += ["## Impacto DESCONHECIDO (fora do grafo)", ""]
        linhas += [f"- `{a}`" for a in d["fora_do_grafo"][:20]]
        linhas.append("")
    if d.get("hubs_atingidos"):
        linhas += ["## Hubs atingidos (a busca parou aqui)", ""]
        linhas += [f"- `{h['no']}` — grau {h['grau']}" for h in d["hubs_atingidos"][:10]]
        linhas.append("")
    if d.get("testes_afetados"):
        linhas += ["## Testes na vizinhança", ""]
        linhas += [f"- `{t['arquivo']}` ({t['saltos']} salto)" for t in d["testes_afetados"][:15]]
        linhas.append("")
    if d.get("afetados"):
        linhas += ["## Arquivos na vizinhança", "", "| arquivo | saltos | confiança | nós |",
                   "|---|---:|---|---:|"]
        linhas += [f"| `{a['arquivo']}` | {a['saltos']} | {a['confianca']} | {a['nos']} |"
                   for a in d["afetados"][:30]]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", default="graphify-out", help="diretório do grafo")
    parser.add_argument("--ref", help="comparar contra este ref em vez de HEAD")
    parser.add_argument("--files", help="arquivos explícitos, separados por vírgula")
    parser.add_argument("--depth", type=int, default=PROFUNDIDADE_PADRAO)
    parser.add_argument("--cwd", default=".", help="raiz do repositório")
    parser.add_argument("--report", action="store_true", help="markdown em vez de JSON")
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    res = command_impact(Path(args.graph) if os.path.isabs(args.graph) else cwd / args.graph,
                         args.ref, args.files.split(",") if args.files else None,
                         args.depth, cwd)
    print(render(res) if args.report else json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    # `tools/` e sys.path[0] quando o arquivo roda como script; em modo importado
    # este bloco nao executa, e o stdout do chamador fica intacto.
    from console import usar_utf8

    usar_utf8()
    sys.exit(main())

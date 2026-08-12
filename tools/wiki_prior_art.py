"""Prior-art da wiki para a fase `discuss` de pipelines L2.

Responde uma pergunta que o pipeline nao fazia: **isto ja foi decidido antes?** — para
nao reassimilar o que ja entrou nem relitigar o que ja foi recusado.

Prior-art e uma tarefa de busca diferente da consulta livre. A descricao de uma tarefa
chega como *proposta* ("quero adotar TLA+ para verificar as invariantes"), nao como
pergunta, e o embedding responde com vizinhos tematicos — paginas sobre invariantes —
em vez do registro da decisao. Medido: a pagina que recusa TLA+ cai para rank 24/512.
Por isso aqui ha duas camadas:

  - **literal**: nome proprio de tecnica (TLA+, pm4py, HNSW, Ollama) e o sinal mais
    forte que existe para "ja falamos disso". Termos sao filtrados por discriminancia —
    so vale o que aparece em poucas paginas, senao "harness" casaria com tudo.
  - **semantica**: os hits do wiki_query, **so os confiantes**. Injecao automatica que
    mostra "possivelmente relacionado" vira ruido em toda tarefa nova.

Contrato, herdado do skill-router:
  - **nunca levanta e sempre sai 0** — e um passo de contexto, nao um gate;
  - **silencioso quando nao ha o que dizer** (saida vazia);
  - **nunca reconstroi o indice** (14.9s no corpus atual); se estiver stale, avisa.

Uso:
    python tools/wiki_prior_art.py "<descricao da tarefa>" [--top-k N] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_query as wq

DEFAULT_TOP_K = 4
SNIPPET_CHARS = 180

# Um termo so serve como prior-art se distinguir: aparecer em ate esta fracao das
# paginas. "TLA+" (1 pagina) discrimina; "harness" (dezenas) nao.
MAX_PAGE_FRACTION = 0.15
MAX_LITERAL_HITS = 4

# Prior-art e **decisao** sobre X, nao mencao de X. Sem este filtro, uma tarefa sobre
# "parser de CSV" puxava tres specs que so citam um arquivo .csv de passagem. So conta
# o achado literal que esta numa pagina de decisao ou sob um cabecalho de decisao.
_DECISION_HEADING_RE = re.compile(
    r"recus|decis|adot|assimil|troca|rejeit|substitu|escolh|descart", re.I
)
DECISION_PAGE_TYPE = "decision"

# Nome proprio de tecnica: tem maiuscula fora do inicio, digito, ou simbolo tecnico.
_TOKEN_RE = re.compile(r"[A-Za-z][\w.+#-]{2,}")
_STOPWORDS = {
    "para", "como", "quero", "sobre", "usar", "fazer", "esse", "esta", "este", "isso",
    "with", "from", "that", "this", "the", "and", "for", "was", "not", "nao",
}


def is_distinctive(token: str) -> bool:
    """Token que parece nome proprio de tecnica, nao palavra comum."""
    if token.lower() in _STOPWORDS or len(token) < 3:
        return False
    corpo = token[1:]
    return any(c.isupper() for c in corpo) or any(c.isdigit() for c in token) or any(
        c in "+#._-" for c in corpo
    )


def salient_terms(task: str) -> list[str]:
    """Termos candidatos a match literal, em ordem de aparicao e sem repetir."""
    vistos, saida = set(), []
    for token in _TOKEN_RE.findall(task):
        limpo = token.strip(".-_")
        chave = limpo.lower()
        if chave in vistos or not is_distinctive(limpo):
            continue
        vistos.add(chave)
        saida.append(limpo)
    return saida


def carries_decision(chunk: dict) -> bool:
    """True se o chunk registra uma decisao, e nao apenas menciona o termo."""
    return (
        chunk.get("type") == DECISION_PAGE_TYPE
        or bool(_DECISION_HEADING_RE.search(chunk.get("heading", "")))
    )


def literal_hits(task: str, chunks: list[dict]) -> list[dict]:
    """Paginas que decidem sobre um termo discriminante da tarefa."""
    termos = salient_terms(task)
    if not termos or not chunks:
        return []
    total_paginas = len({c["page_id"] for c in chunks}) or 1
    limite = max(1, int(total_paginas * MAX_PAGE_FRACTION))

    achados: dict[str, dict] = {}
    for termo in termos:
        padrao = re.compile(r"(?<![\w+#])" + re.escape(termo) + r"(?![\w])", re.I)
        casaram = [c for c in chunks if padrao.search(c.get("description", ""))]
        paginas = {c["page_id"] for c in casaram}
        if not paginas or len(paginas) > limite:
            continue  # termo ausente ou generico demais para discriminar
        for chunk in (c for c in casaram if carries_decision(c)):
            atual = achados.get(chunk["page_id"])
            if atual is None:
                achados[chunk["page_id"]] = {
                    "id": chunk["page_id"],
                    "title": chunk.get("title", chunk["page_id"]),
                    "section": chunk.get("heading", ""),
                    "type": chunk.get("type", "page"),
                    "layer": "literal",
                    "terms": [termo],
                    "confident": True,
                    "wikilink": f"[[{chunk['page_id']}]]",
                    "path": chunk.get("path", ""),
                    "snippet": wq.snippet(chunk),
                }
            elif termo not in atual["terms"]:
                atual["terms"].append(termo)
    return sorted(achados.values(), key=lambda h: (-len(h["terms"]), h["id"]))[:MAX_LITERAL_HITS]


def _stale() -> bool:
    """True se o wiki-index esta desatualizado. Nunca levanta."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import build_wiki_index as bwi

        return bool(bwi.check_stale(bwi._default_root()))
    except Exception:
        return False


def collect(task: str, *, top_k: int = DEFAULT_TOP_K, index_dir: Path | None = None) -> dict:
    """Junta camada literal e camada semantica confiante. Nunca levanta."""
    alvo = index_dir or wq.DEFAULT_INDEX
    try:
        index, _ = wq.load_index(alvo)
        chunks = index.get("pages", [])
    except Exception:
        chunks = []
    literais = literal_hits(task, chunks)
    ja_citadas = {h["id"] for h in literais}

    try:
        resultado = wq.query(task, top_k=top_k, index_dir=alvo)
        disponivel = resultado.get("available", False)
        # Mesmo filtro da camada literal: hit semantico confiante costuma ser vizinho
        # tematico ("invariantes", "verificacao"), nao registro de decisao.
        semanticos = [
            h for h in resultado.get("hits", [])
            if h["confident"]
            and h["id"] not in ja_citadas
            and carries_decision({"type": h.get("type"), "heading": h.get("section", "")})
        ]
    except Exception:
        disponivel, semanticos = bool(chunks), []

    return {
        "task": task,
        "available": disponivel or bool(chunks),
        "terms": salient_terms(task),
        "literal": literais,
        "semantic": semanticos,
        "stale": _stale(),
    }


def _linha(hit: dict) -> str:
    trecho = hit.get("snippet", "")
    if len(trecho) > SNIPPET_CHARS:
        trecho = f"{trecho[:SNIPPET_CHARS].rsplit(' ', 1)[0]}..."
    secao = f" › {hit['section']}" if hit.get("section") else ""
    termos = f" **({', '.join(hit['terms'])})**" if hit.get("terms") else ""
    return f"- {hit['wikilink']}{secao}{termos} — {trecho}"


def render(dados: dict) -> str:
    """Bloco de prior-art. String vazia quando nao ha o que dizer."""
    achados = dados.get("literal", []) + dados.get("semantic", [])
    if not dados.get("available") or not achados:
        return ""
    linhas = [
        "## Prior-art na wiki AI-Brain",
        "",
        (
            "Decisoes ja registradas que tocam esta tarefa. Confira se alguma resolve o "
            "ponto — pode ja ter entrado, ou ja ter sido recusada com motivo."
        ),
        "",
    ]
    linhas += [_linha(h) for h in achados]
    if dados.get("stale"):
        linhas += [
            "",
            (
                "*Indice de busca desatualizado — pode haver decisao recente fora "
                "deste resultado (`scripts/build_wiki_index.py`).*"
            ),
        ]
    return "\n".join(linhas)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", help="Descricao da tarefa a checar contra a wiki.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    dados = collect(args.task, top_k=args.top_k, index_dir=args.index)
    if args.json:
        print(json.dumps(dados, ensure_ascii=False, indent=2))
    else:
        bloco = render(dados)
        if bloco:
            print(bloco)
    return 0  # passo de contexto nunca reprova a fase


if __name__ == "__main__":
    sys.exit(main())

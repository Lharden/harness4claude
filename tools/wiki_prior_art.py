"""Prior-art da wiki para a fase `discuss` de pipelines L2.

Responde uma pergunta que o pipeline não fazia: **isto já foi decidido antes?** — para
não reassimilar o que já entrou nem relitigar o que já foi recusado.

Prior-art e uma tarefa de busca diferente da consulta livre. A descrição de uma tarefa
chega como *proposta* ("quero adotar TLA+ para verificar as invariantes"), não como
pergunta, e o embedding responde com vizinhos tematicos — páginas sobre invariantes —
em vez do registro da decisão. Medido: a página que recusa TLA+ cai para rank 24/512.
Por isso aqui ha duas camadas:

  - **literal**: nome próprio de técnica (TLA+, pm4py, HNSW, Ollama) e o sinal mais
    forte que existe para "já falamos disso". Termos são filtrados por discriminância —
    so vale o que aparece em poucas páginas, senao "harness" casaria com tudo.
  - **semântica**: os hits do wiki_query, **so os confiantes**. Injeção automática que
    mostra "possivelmente relacionado" vira ruido em toda tarefa nova.

Contrato, herdado do skill-router:
  - **nunca levanta e sempre sai 0** — e um passo de contexto, não um gate;
  - **silencioso quando não ha o que dizer** (saida vazia);
  - **nunca reconstroi o índice** (14.9s no corpus atual); se estiver stale, avisa.

Uso:
    python tools/wiki_prior_art.py "<descrição da tarefa>" [--top-k N] [--json]
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

# Um termo so serve como prior-art se distinguir: aparecer em até esta fração das
# páginas. "TLA+" (1 página) discrimina; "harness" (dezenas) não.
MAX_PAGE_FRACTION = 0.15
MAX_LITERAL_HITS = 4

# Prior-art e **decisão** sobre X, não menção de X. Sem este filtro, uma tarefa sobre
# "parser de CSV" puxava três specs que so citam um arquivo .csv de passagem. So conta
# o achado literal que esta numa página de decisão ou sob um cabeçalho de decisão.
_DECISION_HEADING_RE = re.compile(
    r"recus|decis|adot|assimil|troca|rejeit|substitu|escolh|descart", re.I
)
DECISION_PAGE_TYPE = "decision"

# Nome próprio de técnica: tem maiuscula fora do início, digito, ou simbolo técnico.
_TOKEN_RE = re.compile(r"[A-Za-z][\w.+#-]{2,}")
_STOPWORDS = {
    "para", "como", "quero", "sobre", "usar", "fazer", "esse", "esta", "este", "isso",
    "with", "from", "that", "this", "the", "and", "for", "was", "not", "nao",
}


def is_distinctive(token: str) -> bool:
    """Token que parece nome próprio de técnica, não palavra comum."""
    if token.lower() in _STOPWORDS or len(token) < 3:
        return False
    corpo = token[1:]
    return any(c.isupper() for c in corpo) or any(c.isdigit() for c in token) or any(
        c in "+#._-" for c in corpo
    )


def salient_terms(task: str) -> list[str]:
    """Termos candidatos a match literal, em ordem de aparição e sem repetir."""
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
    """True se o chunk registra uma decisão, e não apenas menciona o termo."""
    return (
        chunk.get("type") == DECISION_PAGE_TYPE
        or bool(_DECISION_HEADING_RE.search(chunk.get("heading", "")))
    )


def literal_hits(task: str, chunks: list[dict]) -> list[dict]:
    """Páginas que decidem sobre um termo discriminante da tarefa."""
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
            continue  # termo ausente ou genérico demais para discriminar
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


def registry_hits(task: str, root: Path | None = None) -> list[dict]:
    """Camada de REGISTRO: casa o nome da ferramenta contra o arsenal, por id.

    Existe porque a pergunta "isto já foi decidido?" tem uma resposta
    autoritativa — `arsenal/tools.toml` e `arsenal/dispensados.toml` — e as duas
    outras camadas tentavam chegar nela pelo caminho errado.

    Medido em 2026-08-13: perguntar "graphify ja foi assimilado?" devolvia
    silêncio, com o graphify registrado como adotado desde o dia anterior. A
    camada literal descarta termo que aparece em muitas páginas (graphify é
    citado em 52 chunks, logo não discrimina) e a semântica fazia o verbete
    competir por cosseno com 673 chunks, perdendo para specs sobre o mesmo tema.

    Entidade se busca por NOME, não por similaridade. Absorvido de
    neo4j-labs/llm-graph-builder, que declara o schema de entidades ANTES da
    extração em vez de deixar o LLM inferir: a lista de entidades é dado, e
    dado se consulta.

    Esta camada é exata e barata — nem embed nem índice. Nunca levanta: registry
    ausente ou TOML quebrado devolve lista vazia, porque prior-art é passo de
    contexto e não pode derrubar o pipeline.
    """
    import tomllib

    raiz = Path(root) if root else _vault_root()
    if raiz is None:
        return []

    entradas: list[tuple[str, dict, str]] = []
    for rel, chave, estado in (("tools.toml", "tools", "registrada"),
                               ("dispensados.toml", "dispensados", "dispensada")):
        caminho = raiz / "arsenal" / rel
        try:
            with open(caminho, "rb") as handle:
                dados = tomllib.load(handle)
        except (OSError, ValueError):
            continue
        for item in dados.get(chave) or []:
            if item.get("id"):
                entradas.append((str(item["id"]), item, estado))
    if not entradas:
        return []

    baixo = task.lower()
    achados = []
    for ident, item, estado in entradas:
        alvo = ident.lower()
        if not re.search(r"(?<![\w-])" + re.escape(alvo) + r"(?![\w-])", baixo):
            continue
        decisao = item.get("decisao") or ("dispensada" if estado == "dispensada" else "?")
        motivo = item.get("por_que") or item.get("motivo") or ""
        achados.append({
            "id": f"arsenal/{ident}",
            "title": f"{ident} — já {estado} no arsenal",
            "section": f"decisao: {decisao}",
            "type": "arsenal",
            "layer": "registro",
            "terms": [ident],
            "confident": True,
            "wikilink": "[[arsenal/00 Arsenal]]",
            "path": str(raiz / "arsenal" / ("tools.toml" if estado == "registrada" else "dispensados.toml")),
            "snippet": " ".join(str(motivo).split())[:240],
        })
    return achados


def _vault_root() -> Path | None:
    """Raiz do AI-Brain, na mesma precedência do vault_sync."""
    import os
    for var in ("AI_BRAIN_PATH",):
        valor = os.environ.get(var)
        if valor and Path(valor).is_dir():
            return Path(valor)
    valor = os.environ.get("VAULT_PATH")
    if valor and (Path(valor) / "AI-Brain").is_dir():
        return Path(valor) / "AI-Brain"
    padrao = Path.home() / "Documents" / "mainframe" / "AI-Brain"
    return padrao if padrao.is_dir() else None


def _stale() -> bool:
    """True se o wiki-index esta desatualizado. Nunca levanta."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import build_wiki_index as bwi

        return bool(bwi.check_stale(bwi._default_root()))
    except Exception:
        return False


def collect(task: str, *, top_k: int = DEFAULT_TOP_K, index_dir: Path | None = None) -> dict:
    """Junta registro, camada literal e camada semântica confiante. Nunca levanta."""
    alvo = index_dir or wq.DEFAULT_INDEX
    try:
        index, _ = wq.load_index(alvo)
        chunks = index.get("pages", [])
    except Exception:
        chunks = []
    # Registro primeiro: é exato, é barato e é a resposta autoritativa. Deixá-lo
    # depois faria o hit certo competir por posição com vizinho temático.
    try:
        registros = registry_hits(task)
    except Exception:  # noqa: BLE001 - passo de contexto, nunca derruba o pipeline
        registros = []
    literais = literal_hits(task, chunks)
    ja_citadas = {h["id"] for h in literais} | {h["id"] for h in registros}

    try:
        resultado = wq.query(task, top_k=top_k, index_dir=alvo)
        disponivel = resultado.get("available", False)
        # Mesmo filtro da camada literal: hit semântico confiante costuma ser vizinho
        # tematico ("invariantes", "verificação"), não registro de decisão.
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
        "registro": registros,
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
    """Bloco de prior-art. String vazia quando não ha o que dizer."""
    achados = (dados.get("registro", []) + dados.get("literal", [])
               + dados.get("semantic", []))
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

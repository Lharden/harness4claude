"""Consulta semântica da wiki AI-Brain — a operação `query` do padrão LLM Wiki.

Espelha o skill-router: **Camada A** (match exato de título/slug), pinada no topo, e
**Camada B** (cosseno sobre embeddings) completando sempre. As três funções de decisão
(`layer_a`, `layer_b`, `pick`) são importadas de hooks/skill_router.py, não copiadas —
os registros de página carregam `enabled`/`usage_count` neutros justamente para caber
nesse contrato.

Contrato de falha herdado do router: **nunca levanta**. Ollama fora do ar, índice
ausente ou corrompido degradam para a Camada A (ou para lista vazia), nunca para
exceção — quem chama e um passo de pipeline que não pode quebrar por causa de busca.

Uso:
    python tools/wiki_query.py "pergunta" [--top-k N] [--index DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import skill_router as sr

DEFAULT_INDEX = Path.home() / ".claude" / "harness" / "wiki-index"
OLLAMA_URL = os.environ.get("HARNESS_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = sr.EMBED_MODEL

# Mais folgado que o EMBED_TIMEOUT=1.2s do router: ali o embed roda no caminho quente
# de todo prompt; aqui e uma consulta deliberada, onde esperar 8s e aceitável.
EMBED_TIMEOUT = 8.0
DEFAULT_TOP_K = 5
SNIPPET_CHARS = 240

# Dois patamares, ambos medidos com scripts/calibrate_wiki_floor.py contra
# tests/data/golden-wiki.json — nenhum dos dois foi chutado.
#
# MIN_COS = "vale mostrar". Com chunking por seção, as respostas certas aparecem em
# rank 1 mas com cosseno 0.31-0.40: cortar em 0.45 descartaria acerto #1.
#
# Recalibrado sobre 640 chunks e 20 positivas (a primeira medição foi com 512 e 15). O
# patamar de 0.20 a 0.36 é plano — 95% de hit@3, 1 falso-positivo — e só desaba a partir
# de 0.38. Dentro de um platô, o piso mais baixo é o melhor: custa o mesmo e alcança
# mais. A 0.32 a pergunta "como sei que uma mudança não quebrou nada" ficava sem
# resposta com a página certa em rank 1 a 0.3144, seis milésimos abaixo do corte.
#
#   piso   0.28  0.30  0.32  0.34  0.36  0.38  0.40  0.45
#   hit@3   95%   95%   95%   95%   95%   90%   85%   75%
#   falso+    1     1     1     1     1     1     1     0
#
# CONFIDENT_COS = "vale afirmar". É o MIN_COS=0.45 do skill-router, aqui reaproveitado
# como barra de confiança: nenhuma pergunta fora do domínio do vault alcança esse
# patamar (medido: 0 falso-positivos em 0.45). Quem consome decide o que fazer com hit
# abaixo da barra — mostrar como "talvez" em vez de afirmar cobertura. O falso-positivo
# que sobra em 0.30 vive nessa faixa: aparece marcado "confira antes de citar", que é
# exatamente o que a faixa existe para dizer.
MIN_COS = 0.30
CONFIDENT_COS = 0.45

# Quantos chunks buscar por página desejada antes de deduplicar.
OVERFETCH = 4

# Abaixo deste tamanho de corpus, a regra relativa do pick (cos >= mediana + margem)
# e degenerada: com 2 chunks a mediana E o próprio topo, e todo hit abaixo dele cai.
# Invisível nos 512 chunks do vault real, fatal num vault recem-criado.
MIN_CHUNKS_FOR_MARGIN = 10

# Como neutralizar a regra sem forkar o pick: cosseno vive em [-1, 1], então
# `mediana + NEUTRALIZED_MARGIN` fica sempre <= 0 e o piso absoluto (MIN_COS > 0) passa
# a ser o único filtro. Zerar a margem não bastaria — a comparação com a mediana
# continuaria barrando tudo que estivesse abaixo dela.
NEUTRALIZED_MARGIN = -2.0


def load_index(index_dir: Path = DEFAULT_INDEX) -> tuple[dict, list]:
    """Carrega o índice e os vetores. Devolve ({}, []) se ausente ou corrompido."""
    try:
        with open(Path(index_dir) / "wiki-index.json", encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, ValueError):
        return {}, []
    dim = index.get("dim") or 0
    if not dim:
        return index, []
    try:
        data = (Path(index_dir) / "embeddings.f16.bin").read_bytes()
        rows = len(data) // (2 * dim)
        flat = struct.unpack(f"<{rows * dim}e", data[: rows * dim * 2])
    except (OSError, struct.error):
        return index, []
    return index, [flat[i * dim:(i + 1) * dim] for i in range(rows)]


def embed_query(question: str, *, timeout: float = EMBED_TIMEOUT) -> list[float]:
    """Embeda a pergunta via Ollama, normalizada. Levanta se o Ollama não responder."""
    request = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/embed",
        data=json.dumps(
            {"model": EMBED_MODEL, "input": [f"search_query: {question[:1500]}"],
             "keep_alive": "30m"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        vector = json.load(response)["embeddings"][0]
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


# Numa página comum, três seções são três partes do mesmo argumento e a citação útil é a
# página. Numa coleção de referência, três seções são três VERBETES diferentes — deduplicar
# por página ali jogaria fora duas respostas legítimas para caber uma.
UNIDADE_E_A_SECAO = ("compendium",)


def _chave_de_dedupe(chunk: dict, fallback: str) -> str:
    if chunk.get("type") in UNIDADE_E_A_SECAO and chunk.get("heading"):
        return f"{chunk.get('page_id', fallback)}#{chunk['heading']}"
    return chunk.get("page_id", fallback)


def chaves_de_citacao(hit: dict) -> set[str]:
    """Formas pelas quais um resultado pode ser citado como alvo esperado.

    Mora aqui, e não no teste, porque o golden e o calibrador precisam da MESMA regra:
    quando só o teste sabia dela, o calibrador passou a marcar todo caso de compêndio
    como MISS e a tabela de piso ficou inutilizável.
    """
    chaves = {hit["id"]}
    if hit.get("section"):
        chaves.add(f"{hit['id']}#{hit['section']}")
    return chaves


def dedupe_by_page(hits: list[dict], top_k: int) -> list[dict]:
    """Mantém o melhor chunk por unidade de sentido — página, ou seção no compêndio."""
    best: dict[str, dict] = {}
    for hit in hits:
        chave = _chave_de_dedupe(hit["skill"], hit["id"])
        current = best.get(chave)
        if current is None or hit.get("score", 0) > current.get("score", 0):
            best[chave] = hit
    ordered = sorted(best.values(), key=lambda h: h.get("score", 0), reverse=True)
    return ordered[:top_k]


def route(question: str, pages: list[dict], vecs: list, *, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """Camada A pinada no topo, Camada B **sempre** completando. Nunca levanta.

    Aqui a Camada A não curto-circuita a B, ao contrário do skill-router. Lá o match
    exato de nome de skill É a decisão: há um vencedor e a rota acaba. Aqui um match
    exato diz "este verbete é relevante", não "mais nada é" — quem pergunta "o que fazer
    quando o embedding cai no meio do pipeline" nomeia um verbete mas quer outra página.
    Custa uma ida ao Ollama por consulta; é uma consulta deliberada, com 8s de folga.

    Sobre-busca antes de deduplicar: vários chunks da mesma página podem ocupar o topo,
    e cortar em top_k antes da dedupe devolveria uma página só.
    """
    a_hits = sr.layer_a(question.lower(), pages)
    b_scored: list[dict] = []
    if vecs:
        try:
            b_scored = sr.layer_b(embed_query(question), pages, vecs)
        except Exception:  # Ollama fora / timeout / payload torto: degrada p/ Camada A
            b_scored = []
    saved = (sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN)
    sr.TOP_K, sr.MIN_COS = top_k * OVERFETCH, MIN_COS
    if len(b_scored) < MIN_CHUNKS_FOR_MARGIN:
        sr.MIN_MARGIN = NEUTRALIZED_MARGIN
    try:
        chosen = sr.pick(a_hits, b_scored)
    finally:
        sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN = saved
    return dedupe_by_page(chosen, top_k)


def snippet(page: dict, limit: int = SNIPPET_CHARS) -> str:
    """Trecho citável da página."""
    text = page.get("description", "")
    return f"{text[:limit].rsplit(' ', 1)[0]}..." if len(text) > limit else text


def query(question: str, *, index_dir: Path = DEFAULT_INDEX,
          top_k: int = DEFAULT_TOP_K) -> dict:
    """Executa a consulta e devolve resultado estruturado com citações."""
    index, vecs = load_index(index_dir)
    chunks = index.get("pages", [])
    if not chunks:
        return {"question": question, "available": False, "hits": [], "confident_hits": 0}
    hits = []
    for hit in route(question, chunks, vecs, top_k=top_k):
        page = hit["skill"]
        score = round(float(hit.get("cos", hit.get("score", 1.0))), 4)
        page_id = page.get("page_id", hit["id"])
        hits.append({
            "id": page_id,
            "title": page.get("title", page_id),
            "section": page.get("heading", ""),
            "type": page.get("type", "page"),
            "layer": hit["layer"],
            "score": score,
            # Camada A e match exato de titulo/slug: confiança não depende de cosseno.
            "confident": hit["layer"] == "A" or score >= CONFIDENT_COS,
            "wikilink": (
                f"[[{page_id}#{page.get('heading')}]]"
                if page.get("type") in UNIDADE_E_A_SECAO and page.get("heading")
                else f"[[{page_id}]]"
            ),
            "path": page.get("path", ""),
            "snippet": snippet(page),
        })
    return {
        "question": question,
        "available": True,
        "pages_indexed": len({c["page_id"] for c in chunks}),
        "chunks_indexed": len(chunks),
        "embeddings": bool(vecs),
        "confident_hits": sum(h["confident"] for h in hits),
        "hits": hits,
    }


def render(result: dict) -> str:
    """Renderiza o resultado para leitura humana."""
    if not result["available"]:
        return "wiki-index ausente ou vazio — rode scripts/build_wiki_index.py."
    if not result["hits"]:
        return f'Nenhuma pagina acima do piso para: "{result["question"]}"'
    lines = [
        (
            f'Consulta: "{result["question"]}"  '
            f'({result["pages_indexed"]} paginas / {result["chunks_indexed"]} secoes indexadas)'
        ),
        "",
    ]
    if not result["confident_hits"]:
        lines += [
            (
                "A wiki pode nao cobrir isto — nenhum resultado atingiu a barra de "
                "confianca. Os abaixo sao os mais proximos; confira antes de citar."
            ),
            "",
        ]
    for i, hit in enumerate(result["hits"], 1):
        secao = f" › {hit['section']}" if hit["section"] else ""
        marca = "" if hit["confident"] else "  (abaixo da barra)"
        lines += [
            f"{i}. {hit['wikilink']} — {hit['title']}{secao}{marca}",
            f"   tipo: {hit['type']} · camada {hit['layer']} · score {hit['score']}",
            f"   {hit['snippet']}",
            "",
        ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="Pergunta em linguagem natural.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--json", action="store_true", help="Saida JSON em vez de texto.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = query(args.question, index_dir=args.index, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))


if __name__ == "__main__":
    main()

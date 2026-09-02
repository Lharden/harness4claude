#!/usr/bin/env python3
"""session_query.py — acha a conversa anterior que ja tocou neste assunto.

## Por que existe

Reencontrar o que foi decidido em outra sessao exigia lembrar o uuid. `/resume`
carrega a sessao INTEIRA — um jsonl de 5,7 MB nao cabe em contexto — e os
transcripts nao tem registro `type: "summary"` (zero em 343 arquivos). Na
pratica, contexto de uma semana atras era mais barato de refazer do que de
achar.

Este modulo devolve o par de turno relevante e o endereco da sessao, nao a
sessao. O que se carrega depois e decisao de quem perguntou.

## Reuso, nao fork

`layer_a` / `layer_b` / `pick` vem de `hooks/skill_router.py`, como
`tools/wiki_query.py` ja faz. Uma segunda implementacao do ranqueador
divergiria na primeira mudanca de modelo.

A diferenca em relacao ao wiki e a **unidade de dedupe**: la e a pagina, aqui e
a SESSAO. Cinco chunks da mesma conversa no topo sao uma resposta, nao cinco —
e devolver a mesma sessao cinco vezes gastaria o top-k inteiro numa conversa so.

## Pisos

`MIN_COS` e `CONFIDENT_COS` foram herdados do wiki_query como ponto de partida
declarado, **nao calibrados para este corpus**. Prosa de wiki e transcript de
conversa tem distribuicoes diferentes de cosseno; herdar numero sem medir foi
exatamente o erro que deixou o piso do branch-sensor decorativo por meses. Ver
`--calibrar` para a varredura que produz o numero honesto.
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
import skill_router as sr  # noqa: E402

HOME = Path(os.path.expanduser("~"))
DEFAULT_INDEX = HOME / ".claude" / "harness" / "sessions-index"
DEFAULT_CATALOG = HOME / ".claude" / "harness" / "sessions-catalog.json"

OLLAMA_URL = os.environ.get("HARNESS_OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("HARNESS_EMBED_MODEL", "nomic-embed-text-v2-moe")

#: Consulta deliberada, nao hook: 8s de folga em vez dos 3s do caminho passivo.
EMBED_TIMEOUT = 8.0

DEFAULT_TOP_K = 5
OVERFETCH = 6
SNIPPET_CHARS = 220

#: HERDADOS do wiki_query, nao medidos neste corpus. Ver docstring do modulo.
MIN_COS = 0.30
CONFIDENT_COS = 0.45
MIN_CHUNKS_FOR_MARGIN = 3
NEUTRALIZED_MARGIN = 0.0


def load_index(index_dir: Path = DEFAULT_INDEX) -> tuple[dict, list]:
    """Indice e vetores. ({}, []) se ausente ou corrompido — nunca levanta."""
    try:
        with open(Path(index_dir) / "sessions-index.json", encoding="utf-8") as fh:
            index = json.load(fh)
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
    """Embeda a pergunta, normalizada. Levanta se o Ollama nao responder."""
    request = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/embed",
        data=json.dumps({
            "model": EMBED_MODEL,
            "input": [f"search_query: {question[:1500]}"],
            "keep_alive": "30m",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        vector = json.load(response)["embeddings"][0]
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def dedupe_by_session(hits: list[dict], top_k: int) -> list[dict]:
    """Uma linha por sessao. Cinco chunks da mesma conversa sao uma resposta."""
    vistos, saida = set(), []
    for hit in hits:
        chunk = hit.get("skill") or {}
        sid = chunk.get("session_id") or hit.get("id", "")
        if sid in vistos:
            continue
        vistos.add(sid)
        saida.append(hit)
        if len(saida) >= top_k:
            break
    return saida


def route(question: str, chunks: list, vecs: list, *, top_k: int = DEFAULT_TOP_K,
          vector: list | None = None) -> list[dict]:
    """Camada A no topo, camada B completando. Nunca levanta.

    `vector` permite reaproveitar um embedding ja calculado — o hook de sessao
    ja embeda o primeiro prompt para fixar a ancora do branch-sensor, e pagar
    Ollama duas vezes pelo mesmo texto seria desperdicio puro.
    """
    a_hits = sr.layer_a(question.lower(), chunks)
    b_scored: list[dict] = []
    if vecs:
        try:
            qvec = vector if vector else embed_query(question)
            b_scored = sr.layer_b(qvec, chunks, vecs)
        except Exception:  # Ollama fora: degrada para a camada A
            b_scored = []
    salvo = (sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN)
    sr.TOP_K, sr.MIN_COS = top_k * OVERFETCH, MIN_COS
    if len(b_scored) < MIN_CHUNKS_FOR_MARGIN:
        sr.MIN_MARGIN = NEUTRALIZED_MARGIN
    try:
        escolhidos = sr.pick(a_hits, b_scored)
    finally:
        sr.TOP_K, sr.MIN_COS, sr.MIN_MARGIN = salvo
    return dedupe_by_session(escolhidos, top_k)


def snippet(chunk: dict, limit: int = SNIPPET_CHARS) -> str:
    texto = chunk.get("description", "")
    return f"{texto[:limit].rsplit(' ', 1)[0]}..." if len(texto) > limit else texto


def query(question: str, *, index_dir: Path = DEFAULT_INDEX,
          top_k: int = DEFAULT_TOP_K, project: str | None = None,
          session: str | None = None, vector: list | None = None) -> dict:
    """Consulta estruturada. `available: False` quando nao ha indice."""
    index, vecs = load_index(index_dir)
    chunks = index.get("pages", [])
    if not chunks:
        return {"question": question, "available": False, "hits": [],
                "confident_hits": 0,
                "hint": "indice ausente: rode scripts/build_sessions_index.py"}

    if project:
        alvo = project.lower()
        mantidos = [c for c in chunks if alvo in (c.get("project", "") + c.get("cwd", "")).lower()]
        chunks = mantidos or chunks
    if session:
        chunks = [c for c in chunks
                  if session in (c.get("session_id", ""), c.get("short_ref", ""))] or chunks

    hits = []
    for hit in route(question, chunks, vecs, top_k=top_k, vector=vector):
        chunk = hit["skill"]
        score = round(float(hit.get("cos", hit.get("score", 1.0))), 4)
        hits.append({
            "session_id": chunk.get("session_id"),
            "short_ref": chunk.get("short_ref"),
            "title": chunk.get("title"),
            "project": chunk.get("project"),
            "started_at": chunk.get("started_at"),
            "turn": chunk.get("turn"),
            "score": score,
            "confident": score >= CONFIDENT_COS,
            "layer": hit.get("layer"),
            "snippet": snippet(chunk),
            "resume": f"claude --resume {chunk.get('session_id')}",
        })
    return {
        "question": question,
        "available": True,
        "indexed_chunks": len(index.get("pages", [])),
        "hits": hits,
        "confident_hits": sum(1 for h in hits if h["confident"]),
    }


def recent(cwd: str | None = None, limit: int = 3,
           catalog: Path = DEFAULT_CATALOG) -> list[dict]:
    """Ultimas sessoes deste diretorio, pelo catalogo. Zero embedding.

    Barato de proposito: e o que o SessionStart injeta, e pagar ~1s de Ollama
    no inicio de toda sessao para mostrar tres linhas seria taxar o comeco do
    trabalho para lembrar do trabalho.
    """
    try:
        with open(catalog, encoding="utf-8") as fh:
            linhas = json.load(fh).get("sessions", [])
    except (OSError, ValueError):
        return []
    if cwd:
        alvo = str(cwd).replace("\\", "/").lower()
        mesmas = [s for s in linhas
                  if alvo and alvo in str(s.get("cwd", "")).replace("\\", "/").lower()]
        linhas = mesmas or []
    return linhas[:limit]


def render(resultado: dict) -> str:
    if not resultado.get("available"):
        return resultado.get("hint", "indice de sessoes ausente")
    if not resultado["hits"]:
        return f"nenhuma sessao anterior toca em: {resultado['question']}"
    linhas = [f"[sessoes relacionadas] {len(resultado['hits'])} conversa(s):"]
    for h in resultado["hits"]:
        data = (h.get("started_at") or "")[:10]
        marca = "*" if h["confident"] else " "
        linhas.append(
            f"  {marca} {h['short_ref']} · \"{(h['title'] or '')[:52]}\" · "
            f"{data} · turno {h['turn']} · cos {h['score']}"
        )
        linhas.append(f"      {h['snippet'][:150]}")
        linhas.append(f"      {h['resume']}")
    linhas.append("  (* = acima do piso de confianca, NAO calibrado neste corpus)")
    return "\n".join(linhas)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Busca semantica nas sessoes anteriores.")
    ap.add_argument("question", nargs="?", default=None)
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--project", default=None, help="restringe por projeto ou cwd")
    ap.add_argument("--session", default=None, help="restringe a uma sessao (uuid ou ref)")
    ap.add_argument("--recent", metavar="CWD", nargs="?", const="", default=None,
                    help="ultimas sessoes deste diretorio, sem embedding")
    ap.add_argument("--json", action="store_true")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.recent is not None:
        linhas = recent(args.recent or os.getcwd(), args.top_k, args.catalog)
        if args.json:
            print(json.dumps(linhas, ensure_ascii=False, indent=1))
        elif not linhas:
            print("nenhuma sessao anterior registrada para este diretorio")
        else:
            print(f"[sessoes recentes] {len(linhas)}:")
            for s in linhas:
                print(f"  {s['short_ref']} · \"{s['title'][:56]}\" · "
                      f"{(s.get('started_at') or '')[:10]} · {s['n_turns']} turnos")
        return 0

    if not args.question:
        build_parser().error("informe a pergunta, ou use --recent")
    resultado = query(args.question, index_dir=args.index, top_k=args.top_k,
                      project=args.project, session=args.session)
    print(json.dumps(resultado, ensure_ascii=False, indent=2) if args.json
          else render(resultado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

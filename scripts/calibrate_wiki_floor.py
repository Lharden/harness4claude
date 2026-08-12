#!/usr/bin/env python3
"""Varre o piso de cosseno da wiki contra o golden set: hit rate x falso-positivo.

O piso do skill-router (MIN_COS=0.45) foi calibrado para descricoes curtas de skill.
Secoes de wiki sao prosa longa e o cosseno cai sistematicamente — por isso a wiki
precisa do proprio piso. Este script mede qual, em vez de chutar, exercitando o mesmo
caminho da producao (wiki_query.route, com dedupe por pagina).

Uso: python scripts/calibrate_wiki_floor.py [--top-k 3]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import wiki_query as wq

GOLDEN = os.path.join(ROOT, "tests", "data", "golden-wiki.json")
CANDIDATES = [0.20, 0.24, 0.26, 0.28, 0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.45]


def memoize_embed():
    """Embeda cada pergunta uma vez so — a varredura muda o piso, nao o vetor."""
    original, cache = wq.embed_query, {}

    def cached(question, **kwargs):
        if question not in cache:
            cache[question] = original(question, **kwargs)
        return cache[question]

    wq.embed_query = cached


def tops(cases, pages, vecs, floor, top_k):
    """Lista de page_ids do top-k para cada caso, com o piso trocado."""
    saved, wq.MIN_COS = wq.MIN_COS, floor
    try:
        return [
            [h["skill"].get("page_id", h["id"])
             for h in wq.route(c["prompt"], pages, vecs, top_k=top_k)]
            for c in cases
        ]
    finally:
        wq.MIN_COS = saved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()

    memoize_embed()
    with open(GOLDEN, encoding="utf-8") as f:
        golden = json.load(f)
    index, vecs = wq.load_index()
    pages = index.get("pages", [])
    if not pages:
        print("wiki-index ausente — rode scripts/build_wiki_index.py primeiro.")
        return 1

    known = {p["page_id"] for p in pages}
    for case in golden["positives"]:
        if not any(e in known for e in case["expect_any"]):
            print(f"AVISO: nenhum alvo existe no indice: {case['expect_any']}")

    print(f"\n{'piso':>6} {'hit@' + str(args.top_k):>8} {'falso+':>8}  detalhe")
    print("-" * 62)
    resultados = {}
    for floor in CANDIDATES:
        pos = tops(golden["positives"], pages, vecs, floor, args.top_k)
        neg = tops(golden["negatives"], pages, vecs, floor, args.top_k)
        hits = sum(any(e in top for e in c["expect_any"])
                   for c, top in zip(golden["positives"], pos, strict=True))
        falsos = sum(bool(t) for t in neg)
        taxa = hits / len(golden["positives"])
        resultados[floor] = (taxa, falsos, pos)
        marca = "  <== gate ok" if taxa >= 0.80 and falsos == 0 else ""
        print(f"{floor:>6.2f} {taxa:>7.0%} {falsos:>8}  {hits}/{len(golden['positives'])}{marca}")

    viavel = [f for f, (taxa, falsos, _) in resultados.items() if taxa >= 0.80 and falsos == 0]
    escolhido = viavel[0] if viavel else max(resultados, key=lambda f: resultados[f][0])
    taxa, falsos, pos = resultados[escolhido]
    rotulo = "piso recomendado" if viavel else "melhor hit rate (gate de falso+ NAO satisfeito)"
    print(f"\n{rotulo}: {escolhido}  (hit {taxa:.0%}, falso+ {falsos})")
    for case, top in zip(golden["positives"], pos, strict=True):
        if not any(e in top for e in case["expect_any"]):
            print(f"  MISS {case['prompt'][:52]!r} -> {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

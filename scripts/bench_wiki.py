#!/usr/bin/env python3
"""Bench de latencia do wiki_query (subprocess real, p50/p95/max em ms).

Espelha bench_router.py. Mede as duas categorias separadamente, porque a Camada B so
dispara quando a Camada A nao acha nada — reportar um numero unico esconderia a
diferenca de uma ordem de grandeza entre os caminhos.

Uso: python scripts/bench_wiki.py [N]
"""
import json
import os
import statistics
import subprocess
import sys
import time

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "wiki_query.py")

CASOS = [
    ("Camada A (alias curado, embed pulado)", "recusas registradas"),
    ("Camada B (semantico, embed roda)", "como o pipeline decide entre L1 e L2 numa tarefa nova"),
]


def percentil(valores, q):
    return valores[max(0, min(len(valores) - 1, round(q * len(valores)) - 1))]


for rotulo, pergunta in CASOS:
    tempos, camadas = [], set()
    for _ in range(N):
        inicio = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, TOOL, pergunta, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        tempos.append((time.perf_counter() - inicio) * 1000)
        try:
            camadas.update(h["layer"] for h in json.loads(proc.stdout)["hits"])
        except (ValueError, KeyError):
            pass
    tempos.sort()
    print(f"{rotulo}\n  n={N} p50={statistics.median(tempos):.0f}ms "
          f"p95={percentil(tempos, 0.95):.0f}ms max={tempos[-1]:.0f}ms "
          f"camadas={sorted(camadas) or ['-']}")

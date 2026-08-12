#!/usr/bin/env python3
"""bench_stats.py — estatistica para decisoes de benchmark (D5).

Implementa em stdlib o minimo necessario para que uma otimizacao so seja
aceita quando a diferenca for distinguivel do ruido:

- Mann-Whitney U com correcao de empates e aproximacao normal
- bootstrap de intervalo de confianca para a mediana

Motivacao concreta: o baseline da suite tem CV de 3,6%. Comparar medias de
amostras pequenas nessa variancia leva a concluir "melhorou" ou "piorou" a
partir de ruido. O plano (secao 17) exige intervalo/variancia por otimizacao;
isto e a ferramenta que torna a exigencia executavel.

Uso:
    python scripts/bench_stats.py --a 308.91 288.90 291.76 --b 322.50 319.1 320.4
    python scripts/bench_stats.py --a-file antes.txt --b-file depois.txt --alpha 0.05
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
from pathlib import Path

# Amostras menores que isto tornam a aproximacao normal do U pouco confiavel.
MIN_N_FOR_NORMAL = 3


def _rank_with_ties(values: list[float]) -> list[float]:
    """Ranks medios (1-based), empates recebem a media das posicoes."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg = (i + j + 2) / 2  # +2: converte indice 0-based em rank 1-based
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg
        i = j + 1
    return ranks


def mann_whitney_u(a: list[float], b: list[float]) -> dict[str, float | str]:
    """U bicaudal com correcao de empates e aproximacao normal.

    Retorna U, z, p e uma leitura textual. Para n muito pequeno a aproximacao
    normal e grosseira — o campo `warning` avisa em vez de fingir precisao.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        raise ValueError("ambas as amostras precisam de ao menos um valor")

    combined = a + b
    ranks = _rank_with_ties(combined)
    r1 = sum(ranks[:n1])

    u1 = r1 - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    mu = n1 * n2 / 2

    # Correcao de empates no desvio padrao
    counts: dict[float, int] = {}
    for v in combined:
        counts[v] = counts.get(v, 0) + 1
    tie_term = sum(c**3 - c for c in counts.values())
    n = n1 + n2
    sigma_sq = (n1 * n2 / 12) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    sigma = math.sqrt(sigma_sq) if sigma_sq > 0 else 0.0

    if sigma == 0:
        z, p = 0.0, 1.0
    else:
        # Correcao de continuidade aplicada na direcao que REDUZ o desvio.
        # Sem o clamp em zero, amostras identicas (U == mu) produziriam
        # z = 0.5/sigma e um p artificialmente baixo — evidencia fabricada a
        # partir de dados que nao a contem.
        excess = max(0.0, abs(u - mu) - 0.5)
        z = -excess / sigma  # u = min(u1, u2), entao o desvio e sempre <= 0
        p = min(1.0, max(0.0, 2 * (1 - _normal_cdf(excess / sigma))))

    out: dict[str, float | str] = {"u": u, "z": z, "p": p, "n1": n1, "n2": n2}
    if min(n1, n2) < MIN_N_FOR_NORMAL:
        out["warning"] = (
            f"n={min(n1, n2)} e pequeno demais para a aproximacao normal; "
            "trate o p como indicativo, nao como decisao"
        )
    return out


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bootstrap_median_ci(
    sample: list[float], *, iterations: int = 10000, alpha: float = 0.05, seed: int = 20260725
) -> tuple[float, float]:
    """IC percentil da mediana. `seed` fixo para tornar o resultado reproduzivel."""
    if not sample:
        raise ValueError("amostra vazia")
    rng = random.Random(seed)
    n = len(sample)
    medians = [
        statistics.median(rng.choices(sample, k=n)) for _ in range(iterations)
    ]
    medians.sort()
    lo = medians[int((alpha / 2) * iterations)]
    hi = medians[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return lo, hi


def _describe(name: str, s: list[float]) -> str:
    mean = statistics.fmean(s)
    sd = statistics.stdev(s) if len(s) > 1 else 0.0
    cv = (sd / mean * 100) if mean else 0.0
    return (
        f"{name}: n={len(s)} mediana={statistics.median(s):.2f} "
        f"media={mean:.2f} sd={sd:.2f} cv={cv:.1f}%"
    )


def _load(path: Path) -> list[float]:
    return [float(x) for x in path.read_text(encoding="utf-8").split()]


def main() -> int:
    p = argparse.ArgumentParser(description="Mann-Whitney U + bootstrap CI (D5).")
    p.add_argument("--a", nargs="*", type=float, default=None, metavar="V")
    p.add_argument("--b", nargs="*", type=float, default=None, metavar="V")
    p.add_argument("--a-file", type=Path, default=None)
    p.add_argument("--b-file", type=Path, default=None)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--label-a", default="antes")
    p.add_argument("--label-b", default="depois")
    args = p.parse_args()

    a = args.a if args.a else (_load(args.a_file) if args.a_file else None)
    b = args.b if args.b else (_load(args.b_file) if args.b_file else None)
    if not a or not b:
        p.error("forneca --a/--b ou --a-file/--b-file")

    print(_describe(args.label_a, a))
    print(_describe(args.label_b, b))

    lo_a, hi_a = bootstrap_median_ci(a, alpha=args.alpha)
    lo_b, hi_b = bootstrap_median_ci(b, alpha=args.alpha)
    conf = int((1 - args.alpha) * 100)
    print(f"\nIC{conf}% da mediana ({args.label_a}):  [{lo_a:.2f}, {hi_a:.2f}]")
    print(f"IC{conf}% da mediana ({args.label_b}): [{lo_b:.2f}, {hi_b:.2f}]")

    res = mann_whitney_u(a, b)
    print(f"\nMann-Whitney U={res['u']:.1f}  z={res['z']:.3f}  p={res['p']:.4f}")
    if "warning" in res:
        print(f"AVISO: {res['warning']}")

    significant = float(res["p"]) < args.alpha
    print(
        f"\nVeredito (alpha={args.alpha}): "
        + (
            "diferenca DISTINGUIVEL do ruido"
            if significant
            else "diferenca INDISTINGUIVEL do ruido — nao promover com base nela"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

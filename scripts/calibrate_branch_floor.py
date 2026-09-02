#!/usr/bin/env python3
"""calibrate_branch_floor.py — mede QUAL metrica separa, antes de escolher piso.

## Por que quatro metricas e nao um numero novo

A medicao avulsa de 2026-09-01 saiu **anticorrelacionada**: contra a ancora da
sessao, o mesmo assunto pontuou 0.33 e uma tangente clara pontuou 0.44. Trocar
`HARNESS_BRANCH_FLOOR=0.55` por 0.40 seria repetir o chute com outro digito —
o problema nao esta no valor, esta em **o que se mede**.

As quatro colunas testam quatro hipoteses diferentes sobre o que significa
"saiu do assunto":

| metrica | hipotese |
|---|---|
| `cos_ancora` | baseline: distancia do objetivo declarado da sessao |
| `cos_centroide` | deriva e distancia de ONDE A CONVERSA ESTEVE, nao de onde comecou |
| `delta_anteriores` | o que importa e a MUDANCA, nao o nivel — imune ao offset de estilo |
| `z_intra_sessao` | normaliza comprimento e estilo dentro da propria sessao |

## O gate, declarado antes da tabela

Uma metrica so entra em producao se separar de verdade. O criterio herdado de
`calibrate_wiki_floor.py` e **recall >= 0.80 com zero falsos positivos** no
held-out humano. Esse held-out ainda nao existe (custa 40 min do usuario), mas
ele so seria necessario para escolher o PISO EXATO de uma metrica que ja
separa. Se nenhuma separa no conjunto de treino, nao ha piso a escolher — e a
camada B sai desligada, como o plano previu.

Por isso o relatorio reporta, para cada metrica, o melhor ponto de operacao
alcancavel: se o melhor F1 possivel e proximo do acaso, mais dados nao salvam.

## Truncagem

Todo texto e cortado antes do embed. O cosseno do `nomic-embed-text-v2-moe`
degrada com entrada muito longa, e os prompts aqui vao de 25 B a 150 kB —
sem truncar, a metrica mediria tamanho.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_branch_labels as hbl  # noqa: E402
from build_skills_index import l2norm, ollama_embed  # noqa: E402

#: O cosseno degrada com entrada longa. 600 chars cobre o pedido sem virar
#: medida de tamanho.
TRUNC = 600

METRICAS = ("cos_ancora", "cos_centroide", "delta_anteriores", "z_intra_sessao")


def _cos(a, b) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b, strict=False))


def _centroide(vetores: list) -> list | None:
    vivos = [v for v in vetores if v]
    if not vivos:
        return None
    n = len(vivos)
    return l2norm([sum(col) / n for col in zip(*vivos, strict=False)])


def embeddings_de(pares: list) -> dict:
    """Embeda cada texto UMA vez. Ancora repete muito entre pares da mesma sessao."""
    textos = set()
    for par in pares:
        textos.add(par["ancora"][:TRUNC])
        textos.add(par["turno"][:TRUNC])
        for t in par.get("anteriores") or []:
            textos.add(t[:TRUNC])
    lista = sorted(t for t in textos if t.strip())
    vetores = [l2norm(v) for v in ollama_embed(lista)]
    return dict(zip(lista, vetores, strict=False))


def medir(pares: list, vecs: dict) -> list:
    """Anexa as quatro metricas a cada par. None quando nao ha como calcular."""
    por_sessao: dict[str, list] = {}
    saida = []
    for par in pares:
        va = vecs.get(par["ancora"][:TRUNC])
        vt = vecs.get(par["turno"][:TRUNC])
        ants = [vecs.get(t[:TRUNC]) for t in (par.get("anteriores") or [])]

        cos_ancora = _cos(vt, va)
        cent = _centroide(ants)
        cos_centroide = _cos(vt, cent)
        cos_ants = [c for c in (_cos(v, va) for v in ants) if c is not None]
        delta = (cos_ancora - sum(cos_ants) / len(cos_ants)) if (cos_ancora is not None and cos_ants) else None

        linha = dict(par)
        linha.update({
            "cos_ancora": cos_ancora,
            "cos_centroide": cos_centroide,
            "delta_anteriores": delta,
            "z_intra_sessao": None,
        })
        saida.append(linha)
        por_sessao.setdefault(par.get("sessao", ""), []).append(linha)

    # z-score do cos_ancora dentro da propria sessao: tira offset de estilo.
    for linhas in por_sessao.values():
        vals = [x["cos_ancora"] for x in linhas if x["cos_ancora"] is not None]
        if len(vals) < 3:
            continue
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1e-9
        for x in linhas:
            if x["cos_ancora"] is not None:
                x["z_intra_sessao"] = (x["cos_ancora"] - mu) / sd
    return saida


def melhor_ponto(linhas: list, metrica: str) -> dict:
    """Melhor F1 alcancavel varrendo todos os limiares, e a direcao do sinal.

    Testa as duas direcoes porque a medicao anterior saiu anticorrelacionada:
    assumir "menor = mais distante" seria embutir a hipotese na medida.
    """
    dados = [(x[metrica], x["label"]) for x in linhas if x.get(metrica) is not None]
    if len(dados) < 20:
        return {"metrica": metrica, "erro": f"so {len(dados)} pares com valor"}
    pos = sum(1 for _, y in dados if y == 1)
    neg = len(dados) - pos
    if not pos or not neg:
        return {"metrica": metrica, "erro": "uma das classes esta vazia"}

    melhor: dict = {"f1": 0.0}
    valores = sorted({v for v, _ in dados})
    for direcao in ("abaixo", "acima"):
        for corte in valores:
            tp = sum(1 for v, y in dados
                     if y == 1 and (v <= corte if direcao == "abaixo" else v >= corte))
            fp = sum(1 for v, y in dados
                     if y == 0 and (v <= corte if direcao == "abaixo" else v >= corte))
            if tp == 0:
                continue
            prec, rec = tp / (tp + fp), tp / pos
            f1 = 2 * prec * rec / (prec + rec)
            if f1 > melhor["f1"]:
                melhor = {"f1": round(f1, 3), "corte": round(corte, 4),
                          "direcao": direcao, "precisao": round(prec, 3),
                          "recall": round(rec, 3), "tp": tp, "fp": fp}

    # Linha de base: chutar "tudo e positivo" tem F1 conhecido. Uma metrica que
    # nao supera isso nao esta medindo nada.
    base = 2 * (pos / len(dados)) / ((pos / len(dados)) + 1)
    melhor.update({"metrica": metrica, "pares": len(dados), "positivos": pos,
                   "f1_do_acaso": round(base, 3)})
    return melhor


def render(res: list, gate_recall: float) -> str:
    linhas = [
        f"  {'metrica':18} {'F1':>6} {'acaso':>6} {'prec':>6} {'rec':>6} "
        f"{'corte':>8} {'dir':>7}  veredicto",
    ]
    for r in res:
        if r.get("erro"):
            linhas.append(f"  {r['metrica']:18} {r['erro']}")
            continue
        passa = r["recall"] >= gate_recall and r["fp"] == 0
        util = r["f1"] > r["f1_do_acaso"] + 0.05
        veredicto = "PASSA" if passa else ("separa um pouco" if util else "nao separa")
        linhas.append(
            f"  {r['metrica']:18} {r['f1']:>6.3f} {r['f1_do_acaso']:>6.3f} "
            f"{r['precisao']:>6.3f} {r['recall']:>6.3f} {r['corte']:>8.4f} "
            f"{r['direcao']:>7}  {veredicto}"
        )
    return "\n".join(linhas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Calibra a camada B: qual metrica separa.")
    ap.add_argument("--labels", default=hbl.DEFAULT_OUT)
    ap.add_argument("--gate-recall", type=float, default=0.80)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        with open(a.labels, encoding="utf-8") as fh:
            labels = json.load(fh)
    except (OSError, ValueError):
        print(f"rotulos ausentes em {a.labels}", file=sys.stderr)
        return 2

    pares = labels["positivos"] + labels["negativos"]
    print(f"embedando {len(pares)} pares (textos unicos, truncados em {TRUNC})...",
          file=sys.stderr)
    vecs = embeddings_de(pares)
    linhas = medir(pares, vecs)
    res = [melhor_ponto(linhas, m) for m in METRICAS]

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print(f"{len(pares)} pares  "
          f"({sum(1 for x in pares if x['label'] == 1)} positivos)")
    print(f"gate: recall >= {a.gate_recall} com ZERO falsos positivos\n")
    print(render(res, a.gate_recall))
    if not any(r.get("recall", 0) >= a.gate_recall and r.get("fp", 1) == 0 for r in res):
        print("\nnenhuma metrica passa o gate -> camada B sai DESLIGADA por default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

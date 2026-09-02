#!/usr/bin/env python3
"""calibrate_branch_layer_a.py — mede cada padrao da camada A, um por um.

## Por que

`verdict()` exige `hit_a` para emitir `ramo`. Isso torna a camada A o portao:
sem uma das frases da lista, ramo e matematicamente inalcancavel. E a lista
nunca foi medida — hit rate observado nos prompts reais foi de **3,1%**, e o
unico acerto era falso positivo (`\\be se\\b` casando dentro de "sequencia").

Um padrao que nunca dispara nao custa nada e nao serve para nada. Um padrao que
dispara em trabalho normal custa caro: cada falso positivo e uma interrupcao no
meio do foco — exatamente o dano que o Branch Keeper existe para evitar.

## A regra de corte, declarada ANTES de olhar a tabela

    suporte >= MIN_SUPORTE e precisao < MIN_PRECISAO  ->  remover

Declarar depois seria escolher o corte que preserva os padroes de que se
gosta. Os 16 atuais passam pela mesma peneira que os candidatos novos.

## O que os numeros significam aqui

- **suporte** — em quantos pares o padrao disparou (positivos + negativos).
- **precisao** — dos disparos, quantos foram em positivos. Precisao baixa e
  interrupcao no meio do trabalho.
- **recall** — dos positivos, quantos o padrao pegou. Recall baixo nao machuca:
  sao 16 padroes somados, e cada um so precisa cobrir a sua fatia.

Precisao pesa mais que recall, e a assimetria e deliberada: perder um ramo
custa uma ideia nao desenvolvida; um falso positivo custa o foco de agora.

## Limite conhecido

44 positivos e pouco. O intervalo de confianca do recall e largo, e um padrao
com 2 acertos em 44 nao se distingue de ruido. A precisao sobre 662 negativos
e bem mais firme — e e ela que decide o corte. Ver `harvest_branch_labels.py`
para por que 44 e nao os 312 estimados antes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branch_sensor as bs  # noqa: E402
import harvest_branch_labels as hbl  # noqa: E402

#: Abaixo disso o padrao nao tem dados para julgar; fica marcado `?`.
MIN_SUPORTE = 5

#: Disparar mais em trabalho normal que em ramificacao e ser ruido com sintaxe.
MIN_PRECISAO = 0.35

#: Candidatos estruturais. Entram na MESMA peneira que os 16 atuais — nenhum
#: entra por intuicao. Sao formas de ABRIR assunto, nao temas: e por isso que
#: envelhecem devagar e valem em qualquer projeto.
CANDIDATOS = (
    r"^(e )?se .{0,40}\?",            # hipotetica logo no comeco do turno
    r"\bdepois eu\b",
    r"\bfica pra\b",
    r"\bmais tarde\b",
    r"\bnuma proxima\b",
    r"\boutro dia\b",
    r"\bmudando de assunto\b",
    r"\bvoltando\b",
    r"^obs[:.]",
    r"^off[:.]",
    r"\baproveitando\b",
    r"\bagora quero\b",
    r"\bpreciso tambem\b",
    r"\bproxima coisa\b",
    r"\boutra coisa\b",
    r"\bunrelated\b",
    r"\bwhile we are at it\b",
    r"\bcome back to\b",
    r"\bpark(ing)? this\b",
)


def _fold(texto: str) -> str:
    return bs._fold(texto)


def avaliar(padrao: str, pares: list) -> dict:
    """Precisao, recall e suporte de um padrao sobre os pares rotulados."""
    try:
        rx = re.compile(padrao)
    except re.error as exc:
        return {"padrao": padrao, "erro": str(exc)}
    tp = fp = 0
    positivos = sum(1 for p in pares if p["label"] == 1)
    for par in pares:
        if rx.search(_fold(par["turno"])):
            if par["label"] == 1:
                tp += 1
            else:
                fp += 1
    suporte = tp + fp
    return {
        "padrao": padrao,
        "tp": tp,
        "fp": fp,
        "suporte": suporte,
        "precisao": round(tp / suporte, 3) if suporte else None,
        "recall": round(tp / positivos, 3) if positivos else None,
    }


def veredicto(linha: dict) -> str:
    """Aplica a regra de corte declarada no topo do modulo."""
    if linha.get("erro"):
        return "ERRO"
    if not linha["suporte"]:
        return "morto"
    if linha["suporte"] < MIN_SUPORTE:
        return "?"
    return "CORTAR" if linha["precisao"] < MIN_PRECISAO else "manter"


def rodar(labels: dict) -> dict:
    pares = labels["positivos"] + labels["negativos"]
    atuais = [avaliar(p, pares) for p in bs.LAYER_A_PATTERNS]
    novos = [avaliar(p, pares) for p in CANDIDATOS]
    for linha in atuais + novos:
        linha["veredicto"] = veredicto(linha)
    return {
        "pares": len(pares),
        "positivos": sum(1 for p in pares if p["label"] == 1),
        "negativos": sum(1 for p in pares if p["label"] == 0),
        "min_suporte": MIN_SUPORTE,
        "min_precisao": MIN_PRECISAO,
        "atuais": atuais,
        "candidatos": novos,
    }


def render(res: dict) -> str:
    linhas = [
        f"{res['pares']} pares  ({res['positivos']} positivos / {res['negativos']} negativos)",
        f"corte declarado: suporte >= {res['min_suporte']} e precisao < {res['min_precisao']} -> CORTAR",
        "",
        f"  {'veredicto':10} {'sup':>4} {'tp':>4} {'fp':>4} {'prec':>6} {'rec':>6}  padrao",
    ]
    for titulo, chave in (("PADROES ATUAIS", "atuais"), ("CANDIDATOS", "candidatos")):
        linhas.append("")
        linhas.append(f"-- {titulo} " + "-" * 46)
        ordenado = sorted(res[chave], key=lambda x: (-(x.get("suporte") or 0)))
        for x in ordenado:
            if x.get("erro"):
                linhas.append(f"  {'ERRO':10} {x['erro'][:40]}  {x['padrao']}")
                continue
            prec = "—" if x["precisao"] is None else f"{x['precisao']:.3f}"
            rec = "—" if x["recall"] is None else f"{x['recall']:.3f}"
            linhas.append(
                f"  {x['veredicto']:10} {x['suporte']:>4} {x['tp']:>4} {x['fp']:>4} "
                f"{prec:>6} {rec:>6}  {x['padrao']}"
            )
    cortar = [x for x in res["atuais"] if x["veredicto"] == "CORTAR"]
    mortos = [x for x in res["atuais"] if x["veredicto"] == "morto"]
    entram = [x for x in res["candidatos"] if x["veredicto"] == "manter"]
    linhas += [
        "",
        f"resumo: {len(cortar)} atuais a cortar, {len(mortos)} atuais que nunca disparam, "
        f"{len(entram)} candidatos aprovados",
    ]
    return "\n".join(linhas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Calibra a camada A contra rotulos reais.")
    ap.add_argument("--labels", default=hbl.DEFAULT_OUT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        with open(a.labels, encoding="utf-8") as fh:
            labels = json.load(fh)
    except (OSError, ValueError):
        print(f"rotulos ausentes em {a.labels} — rode harvest_branch_labels.py",
              file=sys.stderr)
        return 2

    res = rodar(labels)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

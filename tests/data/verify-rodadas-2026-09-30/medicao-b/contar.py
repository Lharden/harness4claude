"""Conta a medicao da rubrica pela regra de AC-4.4 (spec verify-adjudica-tudo).

Um defeito conhecido "saiu high/critical" se o MAIOR rotulo de revisor entre os
achados casados com ele (casamento.csv, feito por juiz cego) for high ou critical.
A rubrica fica se pelo menos metade dos defeitos distintos re-achados saiu assim
(L-06). Grupos `mesmo_que` de conhecidos.csv contam como um defeito.

Uso: python tests/data/verify-rodadas-2026-09-30/medicao-b/contar.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

AQUI = Path(__file__).parent
ORDEM = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def main() -> None:
    saida = json.loads((AQUI / "wf_0c646605-9aa.output").read_text(encoding="utf-8"))
    achados = {f["n"]: f for f in saida["result"]["findings"]}
    conhecidos = list(csv.DictReader((AQUI / "conhecidos.csv").open(encoding="utf-8")))
    canonico = {k["id"]: (k["mesmo_que"] or k["id"]) for k in conhecidos}
    presentes = {canonico[k["id"]] for k in conhecidos if k["presente_em_578e0bc"] == "sim"}

    por_defeito: dict[str, list[dict]] = {}
    contaminados = []
    for linha in csv.DictReader((AQUI / "casamento.csv").open(encoding="utf-8")):
        n = int(linha["n"])
        if linha["contaminacao"].strip() == "sim":
            contaminados.append(n)
        for d in (x.strip() for x in linha["defeito"].split(";")):
            if d and d != "nenhum":
                por_defeito.setdefault(canonico.get(d, d), []).append(achados[n])

    print(f"defeitos conhecidos distintos presentes: {len(presentes)}")
    print(f"re-achados: {len(por_defeito)} -> {sorted(por_defeito)}")
    altos = []
    for d, fs in sorted(por_defeito.items()):
        maior = max(fs, key=lambda f: ORDEM.get(f["severity"], -1))
        confirmado = any(f.get("adjudicado") for f in fs if ORDEM.get(f["severity"], -1) >= 2)
        alto = ORDEM.get(maior["severity"], -1) >= 2
        if alto:
            altos.append(d)
        rotulos = ",".join(f"#{f['n']}:{f['severity']}" for f in fs)
        print(f"  {d:4} maior={maior['severity']:8} {'ALTO' if alto else '    '} confirmado={confirmado} [{rotulos}]")
    fora = sorted(presentes - set(por_defeito))
    print(f"nao re-achados: {fora}")
    print(f"achados contaminados: {contaminados}")
    total = len(por_defeito)
    print(f"\nsairam high/critical: {len(altos)} de {total} re-achados -> {sorted(altos)}")
    if total < 2 or 2 * len(altos) == total:
        print("DECISAO: ASK (fronteira de L-06)")
    else:
        print("DECISAO: rubrica FICA" if 2 * len(altos) >= total else "DECISAO: rubrica SAI")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""harvest_branch_labels.py — rotulos de ramificacao sem rotulacao manual.

## A ideia

A pergunta que o sensor tenta responder e *"isto merecia sessao propria?"*.

O usuario **ja respondeu essa pergunta centenas de vezes**: toda vez que abriu
uma sessao nova num projeto em vez de continuar na anterior, ele decidiu que
aquilo tinha vida propria. Toda vez que seguiu na mesma sessao, decidiu que
nao tinha.

Isso da supervisao distante, de graca:

- **positivo** — primeiro prompt limpo da sessao *n+1* de um projeto, pareado
  com a ancora da sessao *n* (o primeiro prompt dela). "Abriu conversa nova."
- **negativo** — qualquer turno de continuacao dentro da mesma sessao, pareado
  com a ancora daquela sessao. "Seguiu no mesmo assunto."

## Por que isso vale mais que intuicao

Os pisos `HARNESS_BRANCH_FLOOR=0.55` e `DRIFT_FLOOR=0.35` sao chutes admitidos
no proprio design doc, e a unica medicao avulsa que existe saiu
**anticorrelacionada**: o mesmo assunto pontuou 0.33 e uma tangente clara
pontuou 0.44. Escolher outro numero a olho seria repetir o chute com outro
digito.

Os 16 padroes da camada A tem o mesmo problema com outro sintoma: hit rate
medido de 3,1% nos prompts reais, e o unico acerto era falso positivo. Como
`verdict()` exige `hit_a` para `ramo`, um padrao ruim ali nao e imprecisao —
e um ramo que nunca nasce.

## O que este rotulo NAO e

Nao e verdade fundamental. O usuario as vezes abre sessao nova porque a janela
encheu, nao porque o assunto mudou. O sinal de contaminacao e barato de olhar:
se quase todo positivo vier de sessao longa, o proxy esta medindo saturacao de
contexto e nao mudanca de tema — por isso `--stats` reporta a distribuicao de
tamanho da sessao anterior.

O juiz final e o conjunto held-out rotulado a mao. Este arquivo produz o
conjunto de TREINO, que e grande e barato; o held-out e pequeno e caro.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_sessions_index as bsi  # noqa: E402

HOME = os.path.expanduser("~")
DEFAULT_OUT = os.path.join(HOME, ".claude", "harness", "calib", "branch-labels.json")

#: Um turno curto demais nao carrega tema. O mesmo piso do indice de sessoes.
MIN_CHARS = bsi.MIN_TURN_CHARS

#: A sessao ANCORA precisa ser trabalho real; a sessao NOVA pode ter qualquer
#: tamanho. O enquadramento e assimetrico de proposito: o rotulo positivo quer
#: dizer "o usuario estava numa conversa de verdade e escolheu abrir outra".
#:
#: Medido em 2026-09-02, e a medida derrubou a estimativa anterior:
#:   ancora >= 1 turno  -> 314 positivos, mediana da ancora = 1
#:   ancora >= 2 turnos ->  55 positivos, mediana da ancora = 8
#:   ancora >= 3 turnos ->  44 positivos, mediana da ancora = 12
#: Com ancora de 1 turno o conjunto vira pares de perguntas avulsas nao
#: relacionadas — dois one-shots seguidos nao sao uma ramificacao. Os "312
#: positivos" estimados numa investigacao anterior eram, em maioria, isso.
MIN_TURNOS_ANCORA = 3

#: Aberturas que nao sao prompt do usuario. Uma sessao iniciada por
#: slash-command tem o CORPO DA SKILL como primeiro turno, e usar isso como
#: rotulo mede o texto da skill em vez da decisao do usuario. Medido em
#: 2026-09-02: aparecia em varios dos 44 positivos.
#:
#: Aqui filtrar e certo, ao contrario do indice de sessoes — la o mesmo filtro
#: destruiu o melhor resultado, porque descartava o TURNO inteiro e junto ia a
#: conversa. Aqui o turno filtrado E o rotulo: sem prompt do usuario, nao ha
#: decisao do usuario para rotular.
ABERTURA_NAO_HUMANA = (
    "Base directory for this skill:",
    "<command-name>",
    "Caveat: The messages below",
    "ARGUMENTS:",
)


def _abertura_humana(texto: str) -> bool:
    return not any(m in texto for m in ABERTURA_NAO_HUMANA)

#: Quantos turnos de continuacao por sessao entram como negativo. Sem teto, uma
#: sessao de 200 turnos dominaria o conjunto sozinha e o classificador
#: aprenderia o estilo daquela conversa em vez da diferenca entre os rotulos.
MAX_NEG_POR_SESSAO = 25


def _sessoes_por_projeto(root=bsi.DEFAULT_ROOT, *, days=0):
    """Sessoes agrupadas por projeto, ordenadas no tempo.

    Baixa `MIN_HUMAN_TURNS` do indexador para 1: aqui uma sessao curta ainda
    conta como "o usuario abriu conversa nova". O filtro de substancia e feito
    depois, e so sobre a ANCORA (ver `MIN_TURNOS_ANCORA`).
    """
    original = bsi.MIN_HUMAN_TURNS
    bsi.MIN_HUMAN_TURNS = 1
    try:
        sessoes, _ = bsi.scan_sessions(root, days=days)
    finally:
        bsi.MIN_HUMAN_TURNS = original
    por_projeto: dict[str, list] = {}
    for s in sessoes:
        por_projeto.setdefault(s["project"], []).append(s)
    for lista in por_projeto.values():
        lista.sort(key=lambda s: s.get("started_at") or "")
    return por_projeto


def harvest(root=bsi.DEFAULT_ROOT, *, days=0):
    """Devolve `{"positivos": [...], "negativos": [...], "stats": {...}}`.

    Cada par e `{"ancora": str, "turno": str, "label": 1|0, ...}` — o formato
    que `calibrate_branch_layer_a.py` e `calibrate_branch_floor.py` consomem.
    """
    positivos, negativos = [], []
    tamanhos_antes = []

    for projeto, lista in _sessoes_por_projeto(root, days=days).items():
        for i, sessao in enumerate(lista):
            ancora = sessao["turns"][0]["prompt"]

            # POSITIVO: o primeiro prompt DESTA sessao contra a ancora da
            # ANTERIOR. O usuario tinha a sessao anterior aberta e escolheu
            # abrir outra.
            if i > 0:
                anterior = lista[i - 1]
                if (len(ancora) >= MIN_CHARS
                        and anterior["n_turns"] >= MIN_TURNOS_ANCORA
                        and _abertura_humana(ancora)
                        and _abertura_humana(anterior["turns"][0]["prompt"])):
                    positivos.append({
                        "ancora": anterior["turns"][0]["prompt"],
                        "turno": ancora,
                        "label": 1,
                        "projeto": projeto,
                        "sessao": sessao["session_id"],
                        "sessao_anterior": anterior["session_id"],
                        "turnos_da_anterior": anterior["n_turns"],
                    })
                    tamanhos_antes.append(anterior["n_turns"])

            # NEGATIVO: continuacoes dentro da propria sessao. So de sessoes
            # substantivas — continuacao numa sessao de 2 turnos e um dado fraco
            # sobre "seguir no mesmo assunto".
            if sessao["n_turns"] < MIN_TURNOS_ANCORA:
                continue
            for n, turno in enumerate(sessao["turns"][1:], start=1):
                if len(turno["prompt"]) < MIN_CHARS:
                    continue
                negativos.append({
                    "ancora": ancora,
                    "turno": turno["prompt"],
                    "label": 0,
                    "projeto": projeto,
                    "sessao": sessao["session_id"],
                    "turno_idx": n,
                })
                if len([x for x in negativos if x["sessao"] == sessao["session_id"]]) \
                        >= MAX_NEG_POR_SESSAO:
                    break

    tamanhos_antes.sort()
    stats = {
        "positivos": len(positivos),
        "negativos": len(negativos),
        "projetos_com_dois_ou_mais": sum(
            1 for lista in _sessoes_por_projeto(root, days=days).values() if len(lista) > 1),
        # Sinal de contaminacao: se a mediana for alta, o proxy pode estar
        # medindo "a janela encheu" e nao "o assunto mudou".
        "turnos_da_sessao_anterior": {
            "min": tamanhos_antes[0] if tamanhos_antes else 0,
            "mediana": tamanhos_antes[len(tamanhos_antes) // 2] if tamanhos_antes else 0,
            "max": tamanhos_antes[-1] if tamanhos_antes else 0,
        },
    }
    return {"positivos": positivos, "negativos": negativos, "stats": stats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Colhe rotulos de ramificacao por supervisao distante.")
    ap.add_argument("--root", default=bsi.DEFAULT_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--days", type=int, default=0, help="0 = sem corte temporal")
    ap.add_argument("--stats", action="store_true", help="so conta, nao grava")
    a = ap.parse_args(argv)

    dados = harvest(a.root, days=a.days)
    if a.stats:
        print(json.dumps(dados["stats"], indent=1, ensure_ascii=False))
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    bsi.atomic_write(a.out, json.dumps(dados, ensure_ascii=False, indent=1).encode("utf-8"))
    s = dados["stats"]
    print(f"{s['positivos']} positivos / {s['negativos']} negativos -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

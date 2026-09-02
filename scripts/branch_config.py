#!/usr/bin/env python3
"""branch_config.py — os knobs do Branch Keeper, declarados uma vez.

## Por que existe

Ate 2026-09-01 cada knob era um `os.environ.get` solto, espalhado por tres
modulos, e a documentacao divergiu do codigo sem que nada acusasse. O
`CLAUDE.md` do usuario listava `MAX_OFFERS=2`, `FLOOR=0.55`,
`DRIFT_FLOOR=0.35`, `DRIFT_SAMPLE=2` — nomes que **o codigo nunca leu**. Quem
exportasse `FLOOR=0.7` acreditando estar afrouxando o piso mudava
exatamente nada, e nao havia como perceber: o sensor ja era silencioso por
outros motivos, entao "nao mudou nada" era indistinguivel de "funcionou".

O design doc tinha a mesma doenca com outro sintoma — dizia cooldown de 5
turnos enquanto o codigo usava 8.

Um registry so nao impede o drift; o teste que le o `CLAUDE.md` e compara com
este dicionario e que impede. Este modulo existe para dar a esse teste algo
com que comparar.

## Como usar

    from branch_config import get_float, get_int, get_str, KNOBS

    floor = get_float("HARNESS_BRANCH_FLOOR")

O default vive aqui e em nenhum outro lugar. `--print-config` imprime o estado
efetivo (default vs. sobrescrito pelo ambiente), que e o primeiro comando a
rodar quando o sensor "nao esta respeitando" uma configuracao.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

FLOAT = "float"
INT = "int"
STR = "str"
BOOL = "bool"


class Knob:
    """Um parametro: nome real da env, tipo, default e por que ele existe."""

    __slots__ = ("name", "kind", "default", "unit", "why")

    def __init__(self, name: str, kind: str, default, unit: str, why: str):
        self.name = name
        self.kind = kind
        self.default = default
        self.unit = unit
        self.why = why

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "default": self.default,
            "unit": self.unit,
            "why": self.why,
        }


KNOBS: dict[str, Knob] = {
    k.name: k
    for k in (
        Knob(
            "HARNESS_BRANCH", BOOL, True, "liga/desliga",
            "Desliga o sensor inteiro. `0`, `false` ou `off` calam as tres camadas.",
        ),
        Knob(
            "HARNESS_BRANCH_FLOOR", FLOAT, 0.55, "cosseno",
            "Acima deste cosseno o marcador da camada A e tratado como mesmo "
            "assunto e nao vira ramo. NAO CALIBRADO: os cossenos reais medidos "
            "em 2026-09-01 ficam entre 0.28 e 0.44, entao 0.55 nunca veta e "
            "`ramo` equivale hoje a `hit_a` sozinho.",
        ),
        Knob(
            "HARNESS_BRANCH_DRIFT_FLOOR", FLOAT, 0.35, "cosseno",
            "Abaixo deste cosseno o turno conta como longe da ancora. Mesmo "
            "aviso de calibracao: a medida saiu anticorrelacionada — o proprio "
            "assunto da conversa pontuou 0.33.",
        ),
        Knob(
            "HARNESS_BRANCH_DRIFT_TURNS", INT, 3, "medicoes",
            "Quantas medicoes longe da ancora sustentam um veredicto de deriva. "
            "Uma medida isolada e ruido; deriva e um padrao.",
        ),
        Knob(
            "HARNESS_BRANCH_DRIFT_SAMPLE", INT, 2, "turnos",
            "A camada B roda a cada N turnos quando nao ha marcador. Custa ~1s "
            "(p95 1049ms): cobrar isso de todo prompt seria taxar o foco para "
            "proteger o foco.",
        ),
        Knob(
            "HARNESS_BRANCH_MAX_OFFERS", INT, 2, "ofertas por sessao",
            "Teto de ofertas por sessao. Um sensor que pergunta demais reproduz "
            "o problema que veio resolver.",
        ),
        Knob(
            "HARNESS_BRANCH_COOLDOWN_TURNS", INT, 8, "chamadas de hook",
            "Espera minima entre ofertas. CUIDADO COM A UNIDADE: turno aqui e "
            "CHAMADA DE HOOK, e uma troca completa gera duas "
            "(UserPromptSubmit + Stop). O default 8 vale ~4 trocas.",
        ),
        Knob(
            "HARNESS_BRANCH_MAX_OPEN", INT, 3, "ramos abertos",
            "Teto de ramos abertos simultaneos por projeto. Acima disso o "
            "parking vira lista de tarefas, que e outra coisa.",
        ),
        Knob(
            "HARNESS_BRANCH_LAYER_B", BOOL, False, "liga/desliga",
            "Camada B (embedding). DESLIGADA por default desde 2026-09-02, por "
            "medicao: das 4 metricas testadas contra 703 pares rotulados, o "
            "melhor F1 foi 0.209 contra 0.108 do acaso, e nenhuma passou o gate "
            "de recall 0.80 com zero falsos positivos. Pior, a direcao do sinal "
            "saiu invertida — cosseno MAIOR contra a ancora previa sessao nova. "
            "Ligar de volta so faz sentido depois de scripts/calibrate_branch_"
            "floor.py apontar uma metrica que separe.",
        ),
        Knob(
            "HARNESS_BRANCH_HOST", STR, "wt", "wt | none",
            "Como abrir a janela do ramo. `none` gera a semente e o launcher "
            "sem abrir nada — util em maquina sem Windows Terminal.",
        ),
    )
}


def _raw(name: str) -> str | None:
    return os.environ.get(name)


def get_float(name: str) -> float:
    knob = KNOBS[name]
    try:
        return float(_raw(name) or knob.default)
    except (TypeError, ValueError):
        return float(knob.default)


def get_int(name: str) -> int:
    knob = KNOBS[name]
    try:
        return int(_raw(name) or knob.default)
    except (TypeError, ValueError):
        return int(knob.default)


def get_str(name: str) -> str:
    knob = KNOBS[name]
    return str(_raw(name) or knob.default)


def get_bool(name: str) -> bool:
    knob = KNOBS[name]
    raw = _raw(name)
    if raw is None:
        return bool(knob.default)
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "")


def effective() -> dict:
    """Estado efetivo de cada knob, com a origem do valor."""
    out = {}
    for name, knob in KNOBS.items():
        raw = _raw(name)
        if knob.kind == FLOAT:
            valor = get_float(name)
        elif knob.kind == INT:
            valor = get_int(name)
        elif knob.kind == BOOL:
            valor = get_bool(name)
        else:
            valor = get_str(name)
        out[name] = {
            "value": valor,
            "source": "env" if raw is not None else "default",
            "default": knob.default,
            "unit": knob.unit,
        }
    return out


# ---------------------------------------------------------------------------
# Anti-drift
# ---------------------------------------------------------------------------

#: Nomes curtos que ja apareceram na documentacao e que o codigo NUNCA leu.
#: Ficam registrados para o teste poder acusa-los pelo nome em vez de dizer
#: apenas "algo divergiu": quem escreveu `FLOOR=0.55` no CLAUDE.md acreditava
#: estar documentando o sensor.
NOMES_FANTASMA = (
    "MAX_OFFERS",
    "MAX_OPEN",
    "FLOOR",
    "DRIFT_FLOOR",
    "DRIFT_SAMPLE",
    "COOLDOWN",
    "COOLDOWN_TURNS",
    "DRIFT_TURNS",
)

_ATRIBUICAO = re.compile(r"`?\b(HARNESS_BRANCH[A-Z_]*)\s*=\s*([^`,;)\s]+)")

#: Uma linha que ADVERTE sobre um nome errado tem que citar o nome errado.
#: Sem esta excecao o verificador acusaria a propria frase que existe para
#: evitar o erro — e a saida racional seria apagar o aviso para calar o check,
#: deixando a documentacao pior do que antes. Marcadores de advertencia, nao
#: de uso.
_ADVERTENCIA = re.compile(
    r"nao (existe|existia|existiam|faz nada|fazia nada|e lido|sao lidos)"
    r"|fantasma|obsoleto|NAO USE|deprecad",
    re.IGNORECASE,
)


def scan_doc(texto: str) -> dict:
    """Extrai de um texto as atribuicoes de knob e os nomes fantasma.

    Devolve `{"atribuicoes": {nome: valor_str}, "fantasmas": [...]}`.
    Linhas de advertencia sao ignoradas — ver `_ADVERTENCIA`.
    """
    atribuicoes: dict[str, str] = {}
    fantasmas: list[str] = []

    for linha in texto.splitlines():
        if _ADVERTENCIA.search(linha):
            continue
        for nome, valor in _ATRIBUICAO.findall(linha):
            atribuicoes[nome] = valor.strip().strip("`\"'")
        for curto in NOMES_FANTASMA:
            # `\bFLOOR=` casaria dentro de HARNESS_BRANCH_FLOOR=; exige que o
            # caractere anterior nao seja parte de um nome maior.
            if curto not in fantasmas and re.search(rf"(?<![A-Z_]){curto}\s*=", linha):
                fantasmas.append(curto)
    return {"atribuicoes": atribuicoes, "fantasmas": fantasmas}


_NUMERO = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")


def _prosa_divergente(texto: str) -> list[str]:
    """Drift escrito em prosa, nao em atribuicao.

    O verificador de atribuicoes nao pegava "cooldown de 5 turnos
    (`HARNESS_BRANCH_COOLDOWN_TURNS`)" — a forma exata do drift que existia no
    design doc enquanto o codigo usava 8. Uma linha que cita um knob E cita
    numeros deveria citar o default entre eles; se nao cita, alguem escreveu um
    numero de memoria.
    """
    problemas = []
    for linha in texto.splitlines():
        if _ADVERTENCIA.search(linha):
            continue
        for nome, knob in KNOBS.items():
            if knob.kind not in (INT, FLOAT) or nome not in linha:
                continue
            if _ATRIBUICAO.search(linha):
                continue  # ja coberto pelo verificador de atribuicoes
            numeros = _NUMERO.findall(linha)
            if not numeros:
                continue
            alvo = str(knob.default)
            if alvo not in numeros and not any(
                abs(float(n) - float(knob.default)) < 1e-9 for n in numeros
            ):
                problemas.append(
                    f"'{nome}' citado com {numeros} mas o default do codigo e "
                    f"{knob.default} — numero escrito de memoria?"
                )
    return problemas


def divergencias(texto: str) -> list[str]:
    """Problemas encontrados num texto de documentacao. Lista vazia = ok."""
    achados = scan_doc(texto)
    problemas = []
    for curto in achados["fantasmas"]:
        problemas.append(
            f"nome fantasma '{curto}=': o codigo le "
            f"'HARNESS_BRANCH_{curto}', exportar o curto nao faz nada"
        )
    for nome, valor in achados["atribuicoes"].items():
        knob = KNOBS.get(nome)
        if knob is None:
            problemas.append(f"'{nome}' nao existe no registry")
            continue
        esperado = str(knob.default).lower() if knob.kind == BOOL else str(knob.default)
        if valor.lower() in ("0", "false", "off", "none"):
            continue  # documentando como DESLIGAR, nao o default
        if valor != esperado:
            problemas.append(
                f"'{nome}={valor}' diverge do default do codigo ({esperado})"
            )
    problemas.extend(_prosa_divergente(texto))
    return problemas


def main(argv=None) -> int:
    # stdout nasce cp1252 no Windows e come travessao e acento. Mesmo defeito
    # que corrompia o BRANCH SIGNAL antes de 2026-09-01.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Knobs do Branch Keeper.")
    ap.add_argument("--print-config", action="store_true")
    ap.add_argument("--check-doc", type=Path, default=None,
                    help="verifica um markdown contra o registry")
    a = ap.parse_args(argv)

    if a.check_doc is not None:
        try:
            texto = a.check_doc.read_text(encoding="utf-8")
        except OSError as e:
            print(f"nao consegui ler {a.check_doc}: {e}")
            return 2
        problemas = divergencias(texto)
        for p in problemas:
            print(f"!! {p}")
        if not problemas:
            print(f"ok: {a.check_doc} bate com o registry")
        return 1 if problemas else 0

    print(json.dumps(effective(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""calibrate_classify_guard.py — mede um guard contra abrir pipeline em vazio.

## O defeito

`classify_prompt` decide por presenca de palavra. "pode pushar" casa `\\bpush\\b`
e vira L0 por sorte; "faca" nao casa nada e cai no `else` final, que e **L1**.
Um turno que so confirma algo ja combinado abre entao um pipeline de 3 a 11
fases, o gate do Stop passa a cobrar suite de testes para deixar responder, e a
task seguinte marca a anterior como `abandoned`.

## O corte, declarado ANTES de olhar a tabela

Um guard entra se, sobre os pares colhidos:

    precisao >= MIN_PRECISAO  e  cobertura >= MIN_COBERTURA

- **precisao** — dos prompts que o guard captura, quantos de fato nao geraram
  arquivo. Guard impreciso engole trabalho real.
- **cobertura** — dos casos de pipeline-em-vazio, quantos o guard resolve.
  Abaixo do minimo ele nao paga a linha de codigo.

`MIN_PRECISAO = 0.85` e alto de proposito, e a assimetria e o oposto da
calibracao da camada A do branch-keeper. La o erro caro era interromper o
usuario, entao bastava vencer o acaso (0.35). Aqui o guard **suprime** o
pipeline: quando ele erra, o trabalho real perde a estrutura SDD em silencio,
sem nada na tela que peca revisao. O erro que ele conserta, no sentido oposto,
ja tem conserto visivel — `confirm_classification.py --final L0-question`. Errar
para o lado que da para desfazer custa menos do que errar para o lado que nao
avisa.

Se nada passar o gate, o resultado e "nao embarcar guard nenhum" — nao baixar o
corte ate algo passar.

## Contaminacao herdada

O rotulo vem de `harvest_classify_labels.py` e carrega as tres contaminacoes
declaradas la. A que mais pesa aqui: prompts sob pipeline ativo nunca chegam ao
regex, e este corpus nao consegue exclui-los. Por isso o numero que vale e a
**taxa** e a **forma da distribuicao**, nao o total absoluto de dano.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harvest_classify_labels as hcl  # noqa: E402

#: Abaixo disso o candidato nao tem dados para julgar; sai marcado `?`.
MIN_SUPORTE = 10

#: Ver o cabecalho: guard que suprime pipeline erra em silencio.
MIN_PRECISAO = 0.85

#: Guard que resolve menos que isto nao paga a complexidade que adiciona.
MIN_COBERTURA = 0.10

#: Cortes de comprimento a varrer. A hipotese do ramo e que o erro se concentra
#: abaixo de um comprimento; a varredura existe para que o numero escolhido saia
#: da tabela e nao do gosto de quem escreve.
CORTES = (10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 300)

#: Respostas humanas curtas — a forma que o seed nomeia. Sao ACORDOS e
#: AUTORIZACOES, nao pedidos de trabalho: o turno que as segue executa o que ja
#: foi combinado ou apenas responde. Entram na mesma peneira que os cortes.
CANDIDATOS_LEXICAIS = (
    r"^(sim|ok|okay|certo|isso|exato|beleza|blz|boa|perfeito|otimo|show)\b",
    r"^(pode|manda|vai|segue|continua|prossiga|prossegue)\b",
    r"^(faca|faz|fa[cs]a isso|do it|go ahead)\b",
    r"^(nao|nope|no)\b",
    r"^(obrigado|valeu|vlw|thanks|thank you)\b",
    r"^(aprovado|concordo|de acordo|fechado)\b",
    r"^\W*$",
)

#: Assinaturas de texto que o HOST ou uma automacao emite, e que nenhum humano
#: digita como pedido. Nao estavam previstas quando este arquivo foi escrito:
#: entraram depois de a amostragem mostrar que os prompts de 2000+ chars do
#: corpus sao harness cientifico ("You are running screening stage 1...") e
#: reentrega do host ("[Request interrupted by user]"), nao pedidos humanos.
#:
#: O mecanismo ja existe e ja funcionou — `AUTOMATION_SIGNATURES` em
#: harness-classify.sh resolveu a metade das maquinas em 8078439. Estes sao
#: candidatos para a metade que ficou, e passam pela MESMA peneira.
CANDIDATOS_ASSINATURA = (
    r"^\[request interrupted by user\]",
    r"^continue from where you left off",
    r"^continue\.?$",
    r"^you are (running|screening|auditing|estimating|extracting|evaluating)\b",
    r"^you are [a-z ]{0,40}(stage|screening|corpus|article|record)\b",
)


def _fold(texto: str) -> str:
    return texto.lower().strip()


def avaliar(nome: str, captura, pares: list, vazios: int) -> dict:
    """Precisao, cobertura e suporte de um guard sobre os pares rotulados.

    `captura(par) -> bool` diz se o guard forcaria L0 naquele prompt. Um par so
    conta se o regex quis abrir pipeline nele: guard nao muda nada onde o regex
    ja disse L0.
    """
    tp = fp = 0
    for par in pares:
        if par["suggested_level"] == "L0":
            continue
        if not captura(par):
            continue
        if par["observado"] == "L0":
            tp += 1
        else:
            fp += 1
    suporte = tp + fp
    return {
        "guard": nome,
        "tp": tp,
        "fp": fp,
        "suporte": suporte,
        "precisao": round(tp / suporte, 3) if suporte else None,
        "cobertura": round(tp / vazios, 3) if vazios else None,
    }


def veredicto(linha: dict) -> str:
    """Aplica a regra de corte declarada no topo do modulo."""
    if not linha["suporte"]:
        return "morto"
    if linha["suporte"] < MIN_SUPORTE:
        return "?"
    if linha["precisao"] < MIN_PRECISAO:
        return "REPROVA"
    if linha["cobertura"] < MIN_COBERTURA:
        return "irrelevante"
    return "APROVA"


def distribuicao(pares: list) -> list:
    """Taxa de pipeline-em-vazio por faixa de comprimento do prompt.

    E a tabela que testa a hipotese do ramo diretamente: se o erro se concentra
    no curto, a taxa cai monotonicamente conforme a faixa cresce.
    """
    faixas = [(0, 20), (20, 40), (40, 80), (80, 160), (160, 320),
              (320, 800), (800, 2000), (2000, 10**9)]
    saida = []
    for lo, hi in faixas:
        na_faixa = [p for p in pares
                    if p["suggested_level"] != "L0" and lo <= p["prompt_len"] < hi]
        vazio = sum(1 for p in na_faixa if p["observado"] == "L0")
        saida.append({
            "faixa": f"{lo}-{hi if hi < 10**9 else '+'}",
            "n": len(na_faixa),
            "em_vazio": vazio,
            "taxa": round(vazio / len(na_faixa), 3) if na_faixa else None,
        })
    return saida


def rodar(dados: dict, *, sem_shell: bool = False) -> dict:
    pares = [p for p in dados["pares"] if not p["nao_digitado"]]
    if sem_shell:
        pares = [p for p in pares if not p["shell_no_turno"]]
    vazios = sum(1 for p in pares
                 if p["suggested_level"] != "L0" and p["observado"] == "L0")

    linhas = []
    for corte in CORTES:
        linhas.append(avaliar(
            f"len <= {corte}", lambda p, c=corte: p["prompt_len"] <= c, pares, vazios))
    for padrao in CANDIDATOS_LEXICAIS + CANDIDATOS_ASSINATURA:
        rx = re.compile(padrao)
        linhas.append(avaliar(
            padrao, lambda p, r=rx: bool(r.search(_fold(p["prompt"]))), pares, vazios))
    for linha in linhas:
        linha["veredicto"] = veredicto(linha)

    l1_plus = [p for p in pares if p["suggested_level"] != "L0"]
    return {
        "pares": len(pares),
        "classificados_l1_plus": len(l1_plus),
        "pipeline_em_vazio": vazios,
        "taxa_base": round(vazios / len(l1_plus), 3) if l1_plus else None,
        "sem_shell": sem_shell,
        "min_suporte": MIN_SUPORTE,
        "min_precisao": MIN_PRECISAO,
        "min_cobertura": MIN_COBERTURA,
        "distribuicao": distribuicao(pares),
        "guards": linhas,
    }


def render(res: dict) -> str:
    escopo = "sem turnos com shell" if res["sem_shell"] else "todos os pares"
    linhas = [
        f"{res['pares']} pares uteis ({escopo})",
        f"{res['classificados_l1_plus']} classificados L1+ pelo regex, "
        f"{res['pipeline_em_vazio']} sem escrever arquivo -> taxa base {res['taxa_base']}",
        "",
        "-- TAXA DE PIPELINE EM VAZIO POR COMPRIMENTO " + "-" * 22,
        f"  {'faixa':>12} {'n':>5} {'vazio':>6} {'taxa':>6}",
    ]
    for d in res["distribuicao"]:
        taxa = "—" if d["taxa"] is None else f"{d['taxa']:.3f}"
        linhas.append(f"  {d['faixa']:>12} {d['n']:>5} {d['em_vazio']:>6} {taxa:>6}")
    linhas += [
        "",
        f"corte declarado: precisao >= {res['min_precisao']} e "
        f"cobertura >= {res['min_cobertura']} -> APROVA",
        "",
        f"  {'veredicto':12} {'sup':>5} {'tp':>5} {'fp':>5} {'prec':>6} {'cob':>6}  guard",
    ]
    for x in sorted(res["guards"], key=lambda x: -(x["precisao"] or 0)):
        prec = "—" if x["precisao"] is None else f"{x['precisao']:.3f}"
        cob = "—" if x["cobertura"] is None else f"{x['cobertura']:.3f}"
        linhas.append(
            f"  {x['veredicto']:12} {x['suporte']:>5} {x['tp']:>5} {x['fp']:>5} "
            f"{prec:>6} {cob:>6}  {x['guard']}")
    aprovados = [x for x in res["guards"] if x["veredicto"] == "APROVA"]
    linhas += ["", f"resumo: {len(aprovados)} guards aprovados"]
    if not aprovados:
        linhas.append("nenhum guard passa o gate — nao embarcar nada.")
    return "\n".join(linhas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Calibra um guard de classificacao.")
    ap.add_argument("--labels", default=hcl.DEFAULT_OUT)
    ap.add_argument("--sem-shell", action="store_true",
                    help="descarta pares cujo turno rodou shell (escrita invisivel)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        with open(a.labels, encoding="utf-8") as fh:
            dados = json.load(fh)
    except (OSError, ValueError):
        print(f"rotulos ausentes em {a.labels} — rode harvest_classify_labels.py",
              file=sys.stderr)
        return 2

    res = rodar(dados, sem_shell=a.sem_shell)
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

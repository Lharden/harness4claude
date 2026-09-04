#!/usr/bin/env python3
"""harvest_classify_labels.py — rotulos de classificacao sem rotulacao manual.

## A pergunta

`hooks/harness-classify.sh` responde, por regex, *"este prompt exige pipeline?"*.
Quando ele erra para cima, o custo nao e um rotulo feio: o gate do Stop passa a
cobrar suite de testes para justificar o envio de uma mensagem, e abrir a task
seguinte marca a anterior como `abandoned`.

`aggregates.classify.proxy_regex_vs_observado = 0.307` ja diz que ele acerta
pouco. O que falta e a **classe de erro nomeada** e o **corte medido**.

## De onde vem o rotulo

Confirmacao explicita nao serve: `confirm_classification.py --final` foi chamado
23 vezes em 357 transcripts, e 14 delas na propria sessao que auditava o
mecanismo. Rotular a mao 1.800 prompts tambem nao.

A supervisao distante ja existe no repo e e a mesma que `record_signal` usa:
**o turno que respondeu ao prompt escreveu arquivo, ou nao?** `actual_level()`
mapeia a contagem para L0/L1/L2, e este modulo a importa em vez de recopiar —
um limiar duplicado divergiria na primeira vez que alguem mexesse num dos dois.

## O que este rotulo NAO e

`actual_level` e proxy, e o proprio `signals.schema.json` diz isso ("CANARIO,
NAO ACURACIA"). Tres contaminacoes conhecidas, declaradas antes de qualquer
tabela:

1. **Turno de analise pura vira L0.** Uma resposta longa e correta que nao toca
   arquivo conta como "sem trabalho". Para a pergunta desta calibracao isso e
   aceitavel: o dano medido e *abrir pipeline*, e abrir pipeline sobre um turno
   que nao produziu artefato custa o mesmo tendo a analise valido ou nao.
2. **Pipeline ativo nunca chega a classificar.** Com `status == "active"` o hook
   emite `continuing` e sai antes do regex. Nas emissoes de 2026-09-02 em
   diante sao 41 `continuing` para 49 `classified` — quase metade. Este
   harvester nao consegue reconstruir esse estado a partir do transcript, entao
   ele **superestima** o dano. O numero a reportar e a taxa, nao o total.
3. **Escrita por shell nao e vista.** `git apply`, `sed -i` e heredoc para
   arquivo passam como Bash. `record_signal` tem a mesma cegueira e a marca com
   `atribuicao_incompleta`; aqui a marca e `shell_no_turno`, para que a tabela
   possa ser refeita sem esses pares.

## Recorte

Nenhum piso de comprimento. `MIN_TURN_CHARS` existe no indice de sessoes porque
la turno curto nao carrega tema; aqui o prompt curto **e** o objeto de estudo, e
filtra-lo apagaria a hipotese antes de testa-la.

Os guards do hook, esses sim, sao replicados: um prompt que o hook nunca
classifica nao pode entrar num corpus que mede o classificador.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify_prompt import classify_prompt  # noqa: E402
from record_signal import actual_level  # noqa: E402

HOME = os.path.expanduser("~")
DEFAULT_ROOT = os.path.join(HOME, ".claude", "projects")
DEFAULT_OUT = os.path.join(HOME, ".claude", "harness", "calib", "classify-labels.json")

#: Ferramentas cuja chamada prova escrita em arquivo. Bash fica DE FORA de
#: proposito: ele tanto escreve quanto so lista diretorio, e contar `ls` como
#: trabalho inflaria justamente o lado que esta sob suspeita.
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

#: Copiado de `hooks/harness-classify.sh` (nao importavel: vive dentro de um
#: heredoc no bash). Se a lista de la mudar, esta tem de mudar junto — o teste
#: `test_classify_guard.py::test_listas_de_assinatura_sincronizadas` le o
#: heredoc e reprova se divergirem.
AUTOMATION_SIGNATURES = (
    "you are summarizing a claude code session",
    "<task-notification>",
    "[system notification - not user input]",
    "[cross-session idle notice]",
)
AUTOMATION_PREFIXES = (
    "[request interrupted by user]",
    "continue from where you left off",
    "you are running",
    "you are screening",
    "you are auditing",
    "you are estimating",
)
MAX_CLASSIFY_LEN = 30000

#: Aberturas que nao sao texto digitado pelo usuario. O hook recebe o prompt
#: cru ("/branch close x"); o transcript guarda o corpo ja expandido da skill.
#: Medir o regex contra corpo de SKILL.md mede a skill, nao o usuario.
NAO_DIGITADO = (
    "Base directory for this skill:",
    "<command-name>",
    "Caveat: The messages below",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "ARGUMENTS:",
)


def _texto_do_prompt(rec: dict) -> str | None:
    """Texto digitado pelo usuario, ou None se o registro nao for isso.

    Descarta sidechain (subagente — o hook de UserPromptSubmit nao roda la),
    tool_result e blocos que o host injeta.
    """
    if rec.get("type") != "user" or rec.get("isSidechain"):
        return None
    msg = rec.get("message") or {}
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        texto = content
    elif isinstance(content, list):
        partes = []
        for bloco in content:
            if not isinstance(bloco, dict):
                continue
            if bloco.get("type") == "tool_result":
                return None  # turno de ferramenta, nao de usuario
            if bloco.get("type") == "text":
                partes.append(bloco.get("text") or "")
        texto = "\n".join(partes)
    else:
        return None
    return texto.strip() or None


def _hook_classificaria(texto: str) -> bool:
    """Replica os guards que rodam antes do regex em harness-classify.sh.

    `.lower().strip()` na mesma ordem do extrator do hook: sem o strip, um
    prompt com espaco a frente escaparia do teste de prefixo aqui e nao la.

    Colher DEPOIS de embarcar uma assinatura a remove do corpus — e certo, e o
    ponto do harvester e medir o que o classificador ainda ve. A tabela que
    justificou cada assinatura fica em `calib/classify-labels-<data>.json`.
    """
    baixo = texto.lower().strip()
    if any(sig in baixo for sig in AUTOMATION_SIGNATURES):
        return False
    if baixo.startswith(AUTOMATION_PREFIXES):
        return False
    return len(texto) <= MAX_CLASSIFY_LEN


def _escritas_do_turno(rec: dict, alvo: set, marcas: dict) -> None:
    """Acumula arquivos escritos e presenca de shell no turno corrente."""
    if rec.get("type") != "assistant" or rec.get("isSidechain"):
        return
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for bloco in content:
        if not isinstance(bloco, dict) or bloco.get("type") != "tool_use":
            continue
        nome = bloco.get("name")
        entrada = bloco.get("input") or {}
        if nome in WRITE_TOOLS:
            caminho = entrada.get("file_path") or entrada.get("notebook_path")
            if caminho:
                alvo.add(str(caminho))
        elif nome in ("Bash", "PowerShell"):
            marcas["shell"] = True


def _fechar(pendente: dict, pares: list) -> None:
    """Fecha o par aberto e o anexa, ja com suggested e observado."""
    if pendente.get("prompt") is None:
        return
    texto = pendente["prompt"]
    nivel, tipo = classify_prompt(texto)
    n = len(pendente["arquivos"])
    pares.append({
        "prompt": texto[:2000],
        "prompt_len": len(texto),
        "suggested": f"{nivel}-{tipo}",
        "suggested_level": nivel,
        "arquivos_escritos": n,
        "observado": actual_level(n),
        "shell_no_turno": bool(pendente["marcas"].get("shell")),
        "nao_digitado": any(m in texto for m in NAO_DIGITADO),
        "sessao": pendente["sessao"],
        "projeto": pendente["projeto"],
        "ts": pendente["ts"],
    })


def harvest(root: str = DEFAULT_ROOT) -> dict:
    """Devolve `{"pares": [...], "stats": {...}}` a partir dos transcripts."""
    pares: list = []
    ignorados_guard = 0
    arquivos_lidos = 0

    for caminho in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        projeto = os.path.basename(os.path.dirname(caminho))
        sessao = os.path.splitext(os.path.basename(caminho))[0]
        pendente: dict = {"prompt": None, "arquivos": set(), "marcas": {},
                          "sessao": sessao, "projeto": projeto, "ts": None}
        try:
            with open(caminho, encoding="utf-8") as fh:
                arquivos_lidos += 1
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        rec = json.loads(linha)
                    except ValueError:
                        continue
                    texto = _texto_do_prompt(rec)
                    if texto is not None:
                        _fechar(pendente, pares)
                        if not _hook_classificaria(texto):
                            ignorados_guard += 1
                            pendente = {"prompt": None, "arquivos": set(), "marcas": {},
                                        "sessao": sessao, "projeto": projeto, "ts": None}
                            continue
                        pendente = {"prompt": texto, "arquivos": set(), "marcas": {},
                                    "sessao": sessao, "projeto": projeto,
                                    "ts": rec.get("timestamp")}
                        continue
                    _escritas_do_turno(rec, pendente["arquivos"], pendente["marcas"])
        except OSError:
            continue
        _fechar(pendente, pares)

    return {"pares": pares, "stats": _stats(pares, arquivos_lidos, ignorados_guard)}


def _stats(pares: list, arquivos: int, ignorados: int) -> dict:
    """Contagens brutas. Nenhum julgamento aqui — a tabela e do calibrador."""
    uteis = [p for p in pares if not p["nao_digitado"]]
    vazio = [p for p in uteis
             if p["suggested_level"] != "L0" and p["observado"] == "L0"]
    return {
        "transcripts": arquivos,
        "pares": len(pares),
        "ignorados_por_guard": ignorados,
        "nao_digitados": len(pares) - len(uteis),
        "uteis": len(uteis),
        "pipeline_em_vazio": len(vazio),
        "taxa_pipeline_em_vazio": round(len(vazio) / len(uteis), 4) if uteis else None,
        "com_shell_no_turno": sum(1 for p in uteis if p["shell_no_turno"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Colhe rotulos de classificacao por supervisao distante.")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--stats", action="store_true", help="so conta, nao grava")
    a = ap.parse_args(argv)

    dados = harvest(a.root)
    if a.stats:
        print(json.dumps(dados["stats"], indent=1, ensure_ascii=False))
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = f"{a.out}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, a.out)
    s = dados["stats"]
    print(f"{s['uteis']} pares uteis ({s['pipeline_em_vazio']} pipeline em vazio) -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

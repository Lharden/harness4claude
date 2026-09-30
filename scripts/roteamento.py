#!/usr/bin/env python3
"""roteamento.py — a tabela de roteamento de modelo/esforco para chips.

Spec: docs/specs/roteamento-de-sessoes-spec-light.md.

A fonte unica da tabela e o cabecalho `model:`/`effort:` de
`~/.claude/agents/<tipo>.md` (o mesmo que rege os subagentes). So os 4 tipos de
`TIPOS` contam (NC-1: nao existe tipo livre). `HARNESS_AGENTS_DIR` troca a
pasta (testes usam uma falsa).

Consumidores: `hooks/harness-roteamento.py` (gate do spawn_task e mensagens da
filha/mae) e o subcomando `nascimento` (NC-4), que a filha chama.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TIPOS = ("juiz-alto-risco", "analise-complexa", "execucao-mecanica", "busca-leve")

# "Roteamento: <modelo> · <esforco> (<tipo>)"
_PAR = r"(?P<modelo>[A-Za-z0-9.\-]+)\s*·\s*(?P<esforco>[A-Za-z0-9\-]+)\s*\((?P<tipo>[a-z\-]+)\)"
LINHA_RE = re.compile(r"^[ \t]*Roteamento:\s*" + _PAR + r"[ \t]*$", re.MULTILINE)
# Pedido da filha a mae. `[roteamento-ok]`/`[roteamento-recusado...]` nao casam (REQ-5).
PEDIDO_RE = re.compile(r"\[roteamento\]\s+" + _PAR + r"\s+prompt_sha=(?P<sha>[0-9a-f]{6,64})")
FROM_RE = re.compile(r"""from=["']?([\w\-:.]+)""")


def ligado() -> bool:
    return os.environ.get("HARNESS_ROTEAMENTO", "1").strip() != "0"


def agents_dir() -> Path:
    env = os.environ.get("HARNESS_AGENTS_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude" / "agents"


def _cabecalho(texto: str) -> dict:
    m = re.match(r"\A﻿?---\r?\n(.*?)\r?\n---", texto, re.DOTALL)
    out = {}
    if m:
        for linha in m.group(1).splitlines():
            k, sep, v = linha.partition(":")
            if sep:
                out[k.strip().lower()] = v.strip().strip("\"'")
    return out


def carregar_tabela(pasta=None) -> dict:
    """{tipo: (modelo, esforco)} lido do cabecalho; omite o que falta ou nao le."""
    pasta = Path(pasta) if pasta is not None else agents_dir()
    tabela = {}
    for tipo in TIPOS:
        try:
            cab = _cabecalho((pasta / f"{tipo}.md").read_text(encoding="utf-8"))
        except OSError:
            continue
        if cab.get("model") and cab.get("effort"):
            tabela[tipo] = (cab["model"].lower(), cab["effort"].lower())
    return tabela


_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _normaliza(prompt: str) -> str:
    """Texto do chip igual nas duas pontas: a mae registra o prompt como o passou ao
    spawn_task (LF, sem prefixo); a filha o recebe com o host prefixando um bloco
    <system-reminder> (worktree) e, as vezes, com CRLF (medido 2026-09-30). Fim de
    linha vira LF e os blocos <system-reminder> saem: sao contexto do host, nunca do chip."""
    texto = re.sub(r"\r\n?", "\n", prompt or "")
    return _REMINDER_RE.sub("", texto)


def parse_linha(prompt: str):
    """(modelo, esforco, tipo) da ULTIMA linha de roteamento do texto, ou None."""
    achados = list(LINHA_RE.finditer(_normaliza(prompt)))
    if not achados:
        return None
    m = achados[-1]
    return m["modelo"].lower(), m["esforco"].lower(), m["tipo"]


def termina_na_linha(prompt: str) -> bool:
    """True se a linha de roteamento e a ultima linha nao vazia do prompt."""
    linhas = [x for x in (prompt or "").splitlines() if x.strip()]
    return bool(linhas) and LINHA_RE.fullmatch(linhas[-1].strip()) is not None


def prompt_sha(prompt: str) -> str | None:
    """sha do prompt normalizado ate o fim da ultima linha de roteamento (12 hex)."""
    texto = _normaliza(prompt)
    achados = list(LINHA_RE.finditer(texto))
    if not achados:
        return None
    base = texto[: achados[-1].end()].strip()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]


def par_valido(par, tabela) -> bool:
    modelo, esforco, tipo = par
    return tipo in tabela and tabela[tipo] == (modelo, esforco)


def tabela_texto(tabela) -> str:
    if not tabela:
        return "(tabela indisponivel: cabecalhos de ~/.claude/agents/ ausentes)"
    return "; ".join(f"{t}: {m} · {e}" for t, (m, e) in tabela.items())


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pasta_da_sessao(session_id, cwd) -> Path:
    import harness_paths

    d = harness_paths.ensure_state_dir(None, cwd or os.getcwd(), session_id=session_id)
    # honra HARNESS_DIR (default_root le o ambiente)
    return d


def anexar(arquivo: str, session_id, cwd, linha: dict) -> None:
    d = pasta_da_sessao(session_id, cwd)
    d.mkdir(parents=True, exist_ok=True)
    with (d / arquivo).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")


def ler(arquivo: str, session_id, cwd) -> list:
    p = pasta_da_sessao(session_id, cwd) / arquivo
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def registrar_chip(session_id, cwd, par, prompt) -> None:
    modelo, esforco, tipo = par
    anexar("roteamento-chips.jsonl", session_id, cwd, {
        "ts": _agora(), "tipo": tipo, "modelo": modelo, "esforco": esforco,
        "prompt_sha": prompt_sha(prompt),
    })


def chip_registrado(session_id, cwd, sha, par) -> bool:
    modelo, esforco, tipo = par
    return any(
        r.get("prompt_sha") == sha and (r.get("modelo"), r.get("esforco"), r.get("tipo")) == (modelo, esforco, tipo)
        for r in ler("roteamento-chips.jsonl", session_id, cwd)
    )


def _cmd_nascimento(args) -> int:
    anexar("roteamento-nascimento.jsonl", args.session_id, args.cwd, {
        "ts": _agora(), "session_id": args.session_id, "tipo": args.tipo,
        "modelo": args.modelo, "esforco": args.esforco,
    })
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nascimento", help="grava o nivel de nascimento da filha (NC-4)")
    n.add_argument("--session-id", required=True)
    n.add_argument("--cwd", default=os.getcwd())
    n.add_argument("--modelo", required=True)
    n.add_argument("--esforco", required=True)
    n.add_argument("--tipo", default="")
    n.set_defaults(fn=_cmd_nascimento)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
